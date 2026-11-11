from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from .config import settings
from .media import MediaError, MediaProcessor


class VideoEditor:
    """Capability object exposed to validated, generated Python statements."""

    def __init__(self, job_id: str, inputs: list[str], output: str) -> None:
        self.media = MediaProcessor(settings)
        self.job_id = job_id
        self.job_dir = settings.work_dir / job_id
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.originals = [self._resolve(reference) for reference in inputs]
        self.current: Path | None = None
        self.output_name = Path(output).name
        self.events: list[dict[str, Any]] = []
        self.step = 0
        self.task_id = "task-00"

    def set_task(self, task_id: str) -> None:
        self.task_id = task_id

    def mcp_effect(self, *, name: str, options: dict[str, Any] | None = None) -> None:
        """Run an explicitly selected ffmpeg-skill tool on the current video.

        This preserves the native methods. An MCP failure never silently reruns a
        potentially destructive operation. Use native methods explicitly instead.
        """
        from .mcp_client import execute_mcp_tool
        if not settings.ffmpeg_skill_mcp_server:
            raise MediaError("FFMPEG_SKILL_MCP_SERVER is not configured")
        if name not in {"cut", "fit", "silence", "loudness", "caption", "export"}:
            raise MediaError(f"unsupported editing MCP tool: {name}")
        self._apply(
            "mcp_" + name,
            lambda out: execute_mcp_tool(
                name, options or {}, self._input(), out,
                server_path=settings.ffmpeg_skill_mcp_server,
                timeout=settings.ffmpeg_skill_timeout_seconds,
            ),
        )

    def merge(self) -> None:
        self._apply("merge", lambda out: self.media._merge(self.originals, out, {}))

    def trim(self, *, start: float, end: float) -> None:
        self._apply("trim", lambda out: self.media._trim([self._input()], out, {"start": start, "end": end}))

    def cut(self, *, start: float, end: float) -> None:
        self._apply("cut", lambda out: self.media._cut([self._input()], out, {"start": start, "end": end}))

    def transition(self, *, name: str = "fade", duration: float = 1.0) -> None:
        sources = self.originals if self.current is None and len(self.originals) > 1 else [self._input()]
        self._apply("transition", lambda out: self.media._transition(sources, out, {"name": name, "duration": duration}))

    def crop(self, *, width: int, height: int, x: int = 0, y: int = 0) -> None:
        def run(out: Path) -> None:
            self.media._run_ffmpeg(
                "-i", str(self._input()), "-vf", f"crop={int(width)}:{int(height)}:{int(x)}:{int(y)}",
                "-c:v", "libx264", "-c:a", "copy", str(out),
            )
        self._apply("crop", run)

    def speed(self, *, factor: float) -> None:
        factor = float(factor)
        if not 0.5 <= factor <= 2.0:
            raise MediaError("speed factor must be between 0.5 and 2.0")
        def run(out: Path) -> None:
            command = ["-i", str(self._input()), "-filter:v", f"setpts={1/factor}*PTS"]
            if self.media._has_audio(self._input()):
                command += ["-filter:a", f"atempo={factor}", "-c:a", "aac"]
            command += ["-c:v", "libx264", str(out)]
            self.media._run_ffmpeg(*command)
        self._apply("speed", run)

    def color_grade(self, *, brightness: float = 0, contrast: float = 1, saturation: float = 1) -> None:
        brightness = max(-1.0, min(1.0, float(brightness)))
        contrast = max(0.0, min(2.0, float(contrast)))
        saturation = max(0.0, min(3.0, float(saturation)))
        self._filter("color_grade", f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}")

    def add_music(self, *, track: str, volume: float = 0.2) -> None:
        music = self._find_music(track)
        if music is None:
            raise MediaError(f"music track not found: {Path(track).name}")
        self._mix_music(music, volume, "add_music")

    def select_music(self, *, mood: str, volume: float = 0.2) -> None:
        tracks = sorted(
            path for path in settings.music_dir.glob("*")
            if path.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg"}
        )
        if not tracks:
            self._event("select_music", artifact=self._relative(self._input()), skipped="assets/music is empty", mood=mood)
            return
        words = set(re.findall(r"[a-z]+", mood.lower()))
        target_bpm = self._mood_bpm(mood)
        scored: list[tuple[float, Path, float | None]] = []
        for track in tracks:
            keyword_matches = len(words & set(re.findall(r"[a-z]+", track.stem.lower())))
            bpm = self._track_tempo(track)
            tempo_distance = abs((bpm or target_bpm) - target_bpm)
            scored.append((tempo_distance - keyword_matches * 20, track, bpm))
        _, music, bpm = min(scored, key=lambda item: item[0])
        self._mix_music(music, volume, "select_music", mood=mood, selected=music.name)
        self.events[-1]["detected_bpm"] = bpm

    def transcribe_subtitles(self, *, language: str | None = None) -> None:
        from faster_whisper import WhisperModel

        model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(self._input()), language=language or None)
        srt = self.job_dir / f"{self.step + 1:02d}-whisper.srt"
        lines: list[str] = []
        for index, segment in enumerate(segments, 1):
            lines += [str(index), f"{self.media._srt_time(segment.start)} --> {self.media._srt_time(segment.end)}", segment.text.strip(), ""]
        srt.write_text("\n".join(lines), encoding="utf-8")
        self._burn_srt(srt, "transcribe_subtitles")

    def add_subtitles(self, *, text: str, start: float = 0, end: float = 5) -> None:
        srt = self.job_dir / f"{self.step + 1:02d}-caption.srt"
        srt.write_text(
            f"1\n{self.media._srt_time(start)} --> {self.media._srt_time(end)}\n{text}\n",
            encoding="utf-8",
        )
        self._burn_srt(srt, "add_subtitles")

    def add_text(self, *, text: str, start: float, end: float, position: str = "bottom") -> None:
        y = {"top": "h*0.1", "center": "(h-text_h)/2", "bottom": "h*0.85"}.get(position, "h*0.85")
        escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
        self._filter(
            "add_text",
            f"drawtext=text='{escaped}':fontcolor=white:fontsize=h/18:borderw=3:"
            f"x=(w-text_w)/2:y={y}:enable='between(t,{float(start)},{float(end)})'",
        )

    def zoom(self, *, start: float = 0, end: float = 5, factor: float = 1.15) -> None:
        factor = max(1.0, min(1.5, float(factor)))
        duration = self.media._duration(self._input())
        frames = max(1, round(duration * 30))
        increment = max(0.0001, (factor - 1) / max(1, (end - start) * 30))
        self._filter(
            "zoom",
            f"scale=1280:720,zoompan=z='min(zoom+{increment},{factor})':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1280x720:fps=30",
        )

    def fade(self, *, kind: str = "both", duration: float = 1.0) -> None:
        total = self.media._duration(self._input())
        filters: list[str] = []
        if kind in {"in", "both"}:
            filters.append(f"fade=t=in:st=0:d={duration}")
        if kind in {"out", "both"}:
            filters.append(f"fade=t=out:st={max(0, total-float(duration))}:d={duration}")
        self._filter("fade", ",".join(filters))

    def export(self, *, filename: str | None = None, width: int | None = None, height: int | None = None, crf: int = 23) -> None:
        params: dict[str, Any] = {
            "filename": Path(filename or self.output_name).name,
            "format": Path(filename or self.output_name).suffix.lstrip(".") or "mp4",
            "crf": crf,
        }
        if width and height:
            params.update(width=width, height=height)
        started = time.monotonic()
        output = self.media._export(self._input(), params)
        self.current = output
        self.output_name = output.name
        self._event("export", artifact=self._relative(output), duration_ms=round((time.monotonic() - started) * 1000))

    def finish(self) -> dict[str, Any]:
        if self.current is None or self.current.parent.resolve() != settings.outputs_dir.resolve():
            self.export(filename=self.output_name)
        metadata = self.media._inspect_with_opencv(self.current)
        thumbnail = self.media._create_thumbnail(self.current)
        if thumbnail:
            metadata["thumbnail"] = thumbnail
        return {"output": self.current.name, "events": self.events, "metadata": metadata}

    def _apply(self, tool: str, action: Callable[[Path], None]) -> None:
        self.step += 1
        output = self.job_dir / f"{self.step:02d}-{tool}.mp4"
        started = time.monotonic()
        action(output)
        self.current = output
        self._event(tool, artifact=self._relative(output), duration_ms=round((time.monotonic() - started) * 1000))

    def _filter(self, tool: str, expression: str) -> None:
        self._apply(
            tool,
            lambda out: self.media._run_ffmpeg(
                "-i", str(self._input()), "-vf", expression, "-c:v", "libx264", "-c:a", "copy", str(out)
            ),
        )

    def _burn_srt(self, srt: Path, tool: str) -> None:
        escaped = self.media._filter_path(srt)
        self._filter(tool, f"subtitles='{escaped}'")

    def _mix_music(self, music: Path, volume: float, tool: str, **metadata: Any) -> None:
        volume = max(0.0, min(1.0, float(volume)))
        def run(out: Path) -> None:
            if self.media._has_audio(self._input()):
                self.media._run_ffmpeg(
                    "-i", str(self._input()), "-stream_loop", "-1", "-i", str(music),
                    "-filter_complex", f"[1:a]volume={volume}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", str(out),
                )
            else:
                self.media._run_ffmpeg(
                    "-i", str(self._input()), "-stream_loop", "-1", "-i", str(music),
                    "-filter:a", f"volume={volume}", "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", str(out),
                )
        self._apply(tool, run)
        self.events[-1].update(metadata)

    def _find_music(self, name: str) -> Path | None:
        safe = Path(name).name
        for parent in (settings.uploads_dir, settings.music_dir):
            candidate = parent / safe
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _mood_bpm(mood: str) -> float:
        lower = mood.lower()
        if any(word in lower for word in ("energetic", "upbeat", "fast", "exciting")):
            return 125.0
        if any(word in lower for word in ("calm", "gentle", "ambient", "soft")):
            return 72.0
        if any(word in lower for word in ("dramatic", "cinematic", "tense")):
            return 92.0
        return 105.0

    @staticmethod
    def _track_tempo(path: Path) -> float | None:
        try:
            import librosa

            audio, sample_rate = librosa.load(path, sr=22_050, mono=True, duration=30)
            tempo, _ = librosa.beat.beat_track(y=audio, sr=sample_rate)
            return round(float(tempo.item() if hasattr(tempo, "item") else tempo), 2)
        except Exception:
            return None

    def _input(self) -> Path:
        if self.current is not None:
            return self.current
        if len(self.originals) == 1:
            return self.originals[0]
        raise MediaError("multiple inputs require merge() or transition() before single-video tools")

    def _resolve(self, reference: str) -> Path:
        root = settings.media_root.resolve()
        path = (root / reference).resolve()
        if root not in path.parents or not path.is_file():
            raise MediaError(f"invalid input: {reference}")
        return path

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(settings.media_root.resolve()).as_posix()

    def _event(self, tool: str, **values: Any) -> None:
        self.events.append({"task_id": self.task_id, "tool": tool, **values})

