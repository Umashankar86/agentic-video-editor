from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import cv2

from .config import Settings
from .models import ExecuteOperationRequest, ExecuteOperationResponse

logger = logging.getLogger(__name__)


class MediaError(RuntimeError):
    """An expected, user-facing media processing failure."""


class MediaProcessor:
    def __init__(self, config: Settings) -> None:
        self.config = config
        for directory in (config.uploads_dir, config.work_dir, config.outputs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.handlers: dict[str, Callable[[list[Path], Path, dict[str, Any]], None]] = {
            "trim": self._trim,
            "cut": self._cut,
            "merge": self._merge,
            "subtitles": self._subtitles,
            "transition": self._transition,
        }

    def execute(self, request: ExecuteOperationRequest) -> ExecuteOperationResponse:
        operation = request.operation
        inputs = [self._resolve_artifact(reference) for reference in request.inputs]
        for input_path in inputs:
            if not input_path.is_file():
                raise MediaError(f"input artifact does not exist: {input_path.name}")

        job_dir = self.config.work_dir / request.job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        if operation.type == "export":
            output = self._export(inputs[0], operation.params)
            metadata = self._inspect_with_opencv(output)
            thumbnail = self._create_thumbnail(output)
            if thumbnail:
                metadata["thumbnail"] = thumbnail
            return ExecuteOperationResponse(
                artifact=self._relative(output), output=output.name, metadata=metadata
            )

        output = job_dir / f"{operation.id}-{operation.type}.mp4"
        handler = self.handlers[operation.type]
        handler(inputs, output, operation.params)
        return ExecuteOperationResponse(
            artifact=self._relative(output), metadata=self._probe_summary(output)
        )

    def check_dependencies(self) -> dict[str, str]:
        versions: dict[str, str] = {}
        for name, binary in (
            ("ffmpeg", self.config.ffmpeg_binary),
            ("ffprobe", self.config.ffprobe_binary),
        ):
            try:
                result = subprocess.run(
                    [binary, "-version"], capture_output=True, text=True, timeout=5, check=True
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise MediaError(f"{name} is not available: {exc}") from exc
            versions[name] = result.stdout.splitlines()[0]
        versions["opencv"] = cv2.__version__
        return versions

    def _trim(self, inputs: list[Path], output: Path, params: dict[str, Any]) -> None:
        start = self._number(params, "start", minimum=0)
        end = self._number(params, "end", minimum=0)
        if end <= start:
            raise MediaError("trim end must be after start")
        self._run_ffmpeg(
            "-ss", str(start), "-to", str(end), "-i", str(inputs[0]),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart", str(output),
        )

    def _cut(self, inputs: list[Path], output: Path, params: dict[str, Any]) -> None:
        start = self._number(params, "start", minimum=0)
        end = self._number(params, "end", minimum=0)
        if end <= start:
            raise MediaError("cut end must be after start")
        between = f"between(t\\,{start}\\,{end})"
        video_filter = f"select=not({between}),setpts=N/FRAME_RATE/TB"
        command = ["-i", str(inputs[0]), "-vf", video_filter]
        if self._has_audio(inputs[0]):
            audio_filter = f"aselect=not({between}),asetpts=N/SR/TB"
            command += ["-af", audio_filter, "-c:a", "aac"]
        command += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-movflags", "+faststart", str(output),
        ]
        self._run_ffmpeg(*command)

    def _merge(self, inputs: list[Path], output: Path, _params: dict[str, Any]) -> None:
        if len(inputs) < 2:
            raise MediaError("merge needs at least two input videos")
        list_file = output.with_suffix(".concat.txt")
        lines = [f"file '{self._concat_path(path)}'" for path in inputs]
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._run_ffmpeg(
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-movflags", "+faststart", str(output),
        )

    def _subtitles(self, inputs: list[Path], output: Path, params: dict[str, Any]) -> None:
        subtitle_path: Path
        if params.get("srt"):
            srt_name = Path(str(params["srt"])).name
            subtitle_path = self.config.uploads_dir / srt_name
            if not subtitle_path.is_file():
                raise MediaError(f"subtitle file was not uploaded: {srt_name}")
        else:
            text = str(params.get("text", "Generated by Agentic Video Editor"))
            subtitle_path = output.with_suffix(".srt")
            duration = max(1.0, self._duration(inputs[0]))
            subtitle_path.write_text(
                "1\n00:00:00,000 --> " + self._srt_time(duration) + "\n" + text + "\n",
                encoding="utf-8",
            )
        escaped = self._filter_path(subtitle_path)
        self._run_ffmpeg(
            "-i", str(inputs[0]), "-vf", f"subtitles='{escaped}'",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "copy", "-movflags", "+faststart", str(output),
        )

    def _transition(self, inputs: list[Path], output: Path, params: dict[str, Any]) -> None:
        name = str(params.get("name", "fade"))
        allowed = {
            "fade", "wipeleft", "wiperight", "slideleft", "slideright",
            "circleopen", "circleclose", "dissolve",
        }
        if name not in allowed:
            raise MediaError(f"unsupported transition: {name}")
        transition_duration = self._number(params, "duration", default=1.0, minimum=0.1)

        if len(inputs) == 1:
            duration = self._duration(inputs[0])
            fade_out = max(0.0, duration - transition_duration)
            self._run_ffmpeg(
                "-i", str(inputs[0]),
                "-vf", f"fade=t=in:st=0:d={transition_duration},fade=t=out:st={fade_out}:d={transition_duration}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "copy", "-movflags", "+faststart", str(output),
            )
            return

        durations = [self._duration(path) for path in inputs]
        if any(duration <= transition_duration for duration in durations):
            raise MediaError("each clip must be longer than the transition duration")

        command: list[str] = []
        for path in inputs:
            command += ["-i", str(path)]

        filters: list[str] = []
        for index in range(len(inputs)):
            filters.append(
                f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,fps=30,"
                "scale=1280:720:force_original_aspect_ratio=decrease,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
                f"[v{index}]"
            )
        previous_video = "v0"
        elapsed = durations[0]
        for index in range(1, len(inputs)):
            output_label = f"vx{index}"
            offset = elapsed - transition_duration * index
            filters.append(
                f"[{previous_video}][v{index}]xfade=transition={name}:"
                f"duration={transition_duration}:offset={offset}[{output_label}]"
            )
            previous_video = output_label
            elapsed += durations[index]

        all_have_audio = all(self._has_audio(path) for path in inputs)
        previous_audio = ""
        if all_have_audio:
            for index in range(len(inputs)):
                filters.append(
                    f"[{index}:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS[a{index}]"
                )
            previous_audio = "a0"
            for index in range(1, len(inputs)):
                output_label = f"ax{index}"
                filters.append(
                    f"[{previous_audio}][a{index}]acrossfade=d={transition_duration}[{output_label}]"
                )
                previous_audio = output_label

        command += ["-filter_complex", ";".join(filters), "-map", f"[{previous_video}]"]
        if all_have_audio:
            command += ["-map", f"[{previous_audio}]", "-c:a", "aac"]
        else:
            command += ["-an"]
        command += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-movflags", "+faststart", str(output),
        ]
        self._run_ffmpeg(*command)

    def _export(self, input_path: Path, params: dict[str, Any]) -> Path:
        filename = Path(str(params.get("filename", "output.mp4"))).name
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", filename):
            raise MediaError("invalid export filename")
        output = self.config.outputs_dir / filename
        format_name = str(params.get("format", output.suffix.lstrip(".") or "mp4")).lower()
        codecs = {
            "mp4": ("libx264", "aac"),
            "mov": ("libx264", "aac"),
            "mkv": ("libx264", "aac"),
            "webm": ("libvpx-vp9", "libopus"),
        }
        if format_name not in codecs:
            raise MediaError(f"unsupported export format: {format_name}")
        video_codec, audio_codec = codecs[format_name]
        crf = int(self._number(params, "crf", default=23, minimum=0))
        if crf > 51:
            raise MediaError("crf must be between 0 and 51")
        command = ["-i", str(input_path)]
        width, height = params.get("width"), params.get("height")
        if width and height:
            command += [
                "-vf",
                f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease,"
                f"pad={int(width)}:{int(height)}:(ow-iw)/2:(oh-ih)/2",
            ]
        command += ["-c:v", video_codec, "-crf", str(crf), "-c:a", audio_codec]
        if format_name in {"mp4", "mov"}:
            command += ["-preset", "veryfast", "-movflags", "+faststart"]
        command.append(str(output))
        self._run_ffmpeg(*command)
        return output

    def _run_ffmpeg(self, *arguments: str) -> None:
        command = [self.config.ffmpeg_binary, "-hide_banner", "-y", *arguments]
        logger.info("running ffmpeg operation", extra={"command": command})
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            raise MediaError(f"could not start FFmpeg: {exc}") from exc
        if result.returncode != 0:
            detail = "\n".join(result.stderr.strip().splitlines()[-12:])
            raise MediaError(f"FFmpeg exited with {result.returncode}:\n{detail}")

    def _probe(self, path: Path) -> dict[str, Any]:
        command = [
            self.config.ffprobe_binary, "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            raise MediaError(f"could not inspect {path.name}: {exc}") from exc

    def _duration(self, path: Path) -> float:
        probe = self._probe(path)
        value = probe.get("format", {}).get("duration")
        if value is None:
            raise MediaError(f"could not determine duration of {path.name}")
        return float(value)

    def _has_audio(self, path: Path) -> bool:
        return any(stream.get("codec_type") == "audio" for stream in self._probe(path).get("streams", []))

    def _probe_summary(self, path: Path) -> dict[str, Any]:
        probe = self._probe(path)
        video = next(
            (stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"), {}
        )
        return {
            "duration_seconds": round(float(probe.get("format", {}).get("duration", 0)), 3),
            "width": video.get("width"),
            "height": video.get("height"),
            "video_codec": video.get("codec_name"),
            "has_audio": any(
                stream.get("codec_type") == "audio" for stream in probe.get("streams", [])
            ),
        }

    def _inspect_with_opencv(self, path: Path) -> dict[str, Any]:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            return self._probe_summary(path)
        try:
            fps = capture.get(cv2.CAP_PROP_FPS)
            frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
            return {
                **self._probe_summary(path),
                "fps": round(fps, 3),
                "frame_count": int(frames),
            }
        finally:
            capture.release()

    def _create_thumbnail(self, video_path: Path) -> str | None:
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            return None
        try:
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if frame_count > 1:
                capture.set(cv2.CAP_PROP_POS_FRAMES, min(frame_count // 2, frame_count - 1))
            ok, frame = capture.read()
            if not ok:
                return None
            thumbnail = video_path.with_suffix(".jpg")
            if not cv2.imwrite(str(thumbnail), frame):
                return None
            return self._relative(thumbnail)
        finally:
            capture.release()

    def _resolve_artifact(self, reference: str) -> Path:
        root = self.config.media_root.resolve()
        candidate = (root / reference).resolve()
        if candidate != root and root not in candidate.parents:
            raise MediaError("artifact path escapes MEDIA_ROOT")
        return candidate

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.config.media_root.resolve()).as_posix()

    @staticmethod
    def _number(
        params: dict[str, Any], key: str, *, default: float | None = None, minimum: float
    ) -> float:
        value = params.get(key, default)
        if value is None:
            raise MediaError(f"missing numeric parameter: {key}")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise MediaError(f"parameter {key} must be numeric") from exc
        if not math.isfinite(number) or number < minimum:
            raise MediaError(f"parameter {key} must be at least {minimum}")
        return number

    @staticmethod
    def _concat_path(path: Path) -> str:
        return path.resolve().as_posix().replace("'", "'\\''")

    @staticmethod
    def _filter_path(path: Path) -> str:
        value = path.resolve().as_posix()
        return value.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

    @staticmethod
    def _srt_time(seconds: float) -> str:
        milliseconds = max(0, round(seconds * 1000))
        hours, milliseconds = divmod(milliseconds, 3_600_000)
        minutes, milliseconds = divmod(milliseconds, 60_000)
        whole_seconds, milliseconds = divmod(milliseconds, 1000)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"

