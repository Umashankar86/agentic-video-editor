from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import cv2
from PIL import Image

from .config import Settings
from .media import MediaProcessor
from .models import (
    AgentPlan,
    AgentTask,
    ReviewDecision,
    SceneCaption,
    SpecialistCode,
    VideoUnderstanding,
)

logger = logging.getLogger(__name__)


ALLOWED_TOOLS: dict[str, list[str]] = {
    "editing": [
        "merge", "trim", "cut", "transition", "crop", "speed", "color_grade",
    ],
    "music": ["add_music", "select_music"],
    "subtitles": ["transcribe_subtitles", "add_subtitles"],
    "animation": ["add_text", "zoom", "fade"],
    "export": ["export"],
}


class VideoUnderstandingAgent:
    """Samples one frame per configured interval and captions it with BLIP."""

    def __init__(self, config: Settings, media: MediaProcessor) -> None:
        self.config = config
        self.media = media
        self._processor: Any = None
        self._model: Any = None
        self._device = "cpu"

    def analyze(self, references: list[str]) -> VideoUnderstanding:
        paths = [self._resolve(reference) for reference in references]
        scenes: list[SceneCaption] = []
        total_duration = 0.0
        width = height = 0
        has_audio = False
        for path in paths:
            capture = cv2.VideoCapture(str(path))
            if not capture.isOpened():
                raise RuntimeError(f"OpenCV could not open {path.name}")
            try:
                fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
                frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
                duration = frame_count / fps if frame_count else self.media._duration(path)
                width = max(width, int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)))
                height = max(height, int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))
                timestamp = 0.0
                while timestamp < max(duration, 0.01):
                    capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
                    ok, frame = capture.read()
                    if ok:
                        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                        scenes.append(
                            SceneCaption(
                                input=path.name,
                                timestamp_seconds=round(timestamp, 3),
                                caption=self._caption(image),
                            )
                        )
                    timestamp += max(1, self.config.frame_sample_seconds)
                total_duration += duration
                has_audio = has_audio or self.media._has_audio(path)
            finally:
                capture.release()
        return VideoUnderstanding(
            duration_seconds=round(total_duration, 3),
            width=width,
            height=height,
            has_audio=has_audio,
            scenes=scenes,
        )

    def _caption(self, image: Image.Image) -> str:
        if self._model is None:
            from transformers import BlipForConditionalGeneration, BlipProcessor
            import torch

            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("loading BLIP model", extra={"model": self.config.blip_model})
            self._processor = BlipProcessor.from_pretrained(self.config.blip_model)
            self._model = BlipForConditionalGeneration.from_pretrained(self.config.blip_model)
            self._model.to(self._device)
        inputs = self._processor(images=image, return_tensors="pt").to(self._device)
        output = self._model.generate(**inputs, max_new_tokens=40)
        return self._processor.decode(output[0], skip_special_tokens=True)

    def _resolve(self, reference: str) -> Path:
        root = self.config.media_root.resolve()
        path = (root / reference).resolve()
        if root not in path.parents or not path.is_file():
            raise RuntimeError(f"invalid input artifact: {reference}")
        return path


class OpenRouterAgents:
    def __init__(self, config: Settings) -> None:
        if not config.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for agentic planning")
        from langchain_openai import ChatOpenAI

        self.model = ChatOpenAI(
            model=config.openrouter_model,
            api_key=config.openrouter_api_key,
            base_url=config.openrouter_base_url,
            default_headers={
                "HTTP-Referer": config.openrouter_site_url,
                "X-Title": config.openrouter_app_name,
            },
            extra_body={"models": config.fallback_model_list},
            temperature=0.15,
        )

    def plan(
        self, prompt: str, understanding: VideoUnderstanding, output: str
    ) -> AgentPlan:
        planner = self.model.with_structured_output(AgentPlan)
        inventory = json.dumps(ALLOWED_TOOLS, indent=2)
        message = f"""
You are the supervisor of an autonomous video-editing system. Create a concrete task graph.
Do not assume a fixed sequence: choose only the specialist agents and tools that improve this video.
Respect the user's explicit intent. Use scene captions and timestamps to infer pacing, cuts, mood,
music style, subtitle need, and tasteful animation. Tasks execute in listed order. Always finish with
exactly one export task. Task IDs must be task-01, task-02, ... and dependencies must reference prior IDs.

USER REQUEST:
{prompt}

BLIP/OPENCV VIDEO UNDERSTANDING:
{understanding.model_dump_json(indent=2)}

AVAILABLE SPECIALISTS AND TOOLS:
{inventory}

REQUESTED OUTPUT: {output}
"""
        plan = planner.invoke(message)
        if not any(task.specialist == "export" for task in plan.tasks):
            plan.tasks.append(
                AgentTask(
                    id=f"task-{len(plan.tasks) + 1:02d}",
                    specialist="export",
                    goal=f"Export the finished edit as {output}",
                    preferred_tools=["export"],
                    depends_on=[plan.tasks[-1].id] if plan.tasks else [],
                )
            )
        return plan

    def specialist(
        self,
        task: AgentTask,
        prompt: str,
        understanding: VideoUnderstanding,
        creative_direction: str,
        output: str,
    ) -> SpecialistCode:
        allowed = ALLOWED_TOOLS[task.specialist]
        coder = self.model.with_structured_output(SpecialistCode)
        tool_docs = _tool_documentation(task.specialist, output)
        message = f"""
You are the {task.specialist} specialist in a video-editing agent team.
Translate your assigned goal into short Python statements. Return only calls in the form
editor.<method>(literal keyword arguments). Never import modules, access files directly, use variables,
loops, comprehensions, attributes other than editor methods, or call anything not explicitly allowed.
The generated statements are AST-validated and executed in a resource-limited subprocess.

ORIGINAL USER REQUEST: {prompt}
CREATIVE DIRECTION: {creative_direction}
ASSIGNED TASK: {task.model_dump_json(indent=2)}
SCENE ANALYSIS: {understanding.model_dump_json(indent=2)}
ALLOWED METHODS: {allowed}
METHOD SIGNATURES:
{tool_docs}
"""
        result = coder.invoke(message)
        result.task_id = task.id
        result.specialist = task.specialist
        return result

    def review(
        self,
        prompt: str,
        plan: AgentPlan,
        output_understanding: VideoUnderstanding,
        execution_events: list[dict[str, Any]],
    ) -> ReviewDecision:
        reviewer = self.model.with_structured_output(ReviewDecision)
        return reviewer.invoke(
            "You are the final QA agent. Judge whether the executed edit follows the request and "
            "creative plan. Be strict but practical.\n"
            f"REQUEST: {prompt}\nPLAN: {plan.model_dump_json()}\n"
            f"OUTPUT ANALYSIS: {output_understanding.model_dump_json()}\n"
            f"EXECUTION EVENTS: {json.dumps(execution_events)}"
        )


def _tool_documentation(specialist: str, output: str) -> str:
    docs = {
        "editing": """
editor.merge()
editor.trim(start=<seconds>, end=<seconds>)
editor.cut(start=<seconds>, end=<seconds>)
editor.transition(name='fade|dissolve|wipeleft|wiperight|slideleft|slideright', duration=<seconds>)
editor.crop(width=<int>, height=<int>, x=<int>, y=<int>)
editor.speed(factor=<0.5..2.0>)
editor.color_grade(brightness=<-1..1>, contrast=<0..2>, saturation=<0..3>)
""",
        "music": """
editor.select_music(mood='<short mood>', volume=<0..1>)  # picks a matching royalty-free local track
editor.add_music(track='<uploaded filename or assets/music filename>', volume=<0..1>)
""",
        "subtitles": """
editor.transcribe_subtitles(language='<optional language code>')  # faster-whisper then burn SRT
editor.add_subtitles(text='<short literal text>', start=<seconds>, end=<seconds>)
""",
        "animation": """
editor.add_text(text='<title>', start=<seconds>, end=<seconds>, position='top|center|bottom')
editor.zoom(start=<seconds>, end=<seconds>, factor=<1.0..1.5>)
editor.fade(kind='in|out|both', duration=<seconds>)
""",
        "export": f"editor.export(filename='{output}', width=<optional int>, height=<optional int>, crf=<18..30>)",
    }
    return docs[specialist]

