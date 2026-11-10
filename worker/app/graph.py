from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .agents import OpenRouterAgents, VideoUnderstandingAgent
from .config import Settings
from .media import MediaProcessor
from .models import (
    AgentPlan,
    AgentRunRequest,
    AgentRunResponse,
    AgentTask,
    GoOperation,
    GoPlan,
    OperationResult,
    ReviewDecision,
    SpecialistCode,
    VideoUnderstanding,
)
from .sandbox import SandboxExecutor, SandboxResult


class GraphState(TypedDict, total=False):
    job_id: str
    prompt: str
    inputs: list[str]
    output: str
    understanding: VideoUnderstanding
    plan: AgentPlan
    specialist_outputs: Annotated[list[SpecialistCode], operator.add]
    sandbox_result: SandboxResult
    output_understanding: VideoUnderstanding
    review: ReviewDecision


class SpecialistState(TypedDict):
    task: AgentTask
    prompt: str
    output: str
    understanding: VideoUnderstanding
    creative_direction: str
    dependency_outputs: list[SpecialistCode]


class AgenticVideoGraph:
    def __init__(self, config: Settings, media: MediaProcessor) -> None:
        self.config = config
        self.vision = VideoUnderstandingAgent(config, media)
        self.llm = OpenRouterAgents(config)
        self.sandbox = SandboxExecutor(config)
        self.graph = self._build()

    def run(self, request: AgentRunRequest) -> AgentRunResponse:
        final = self.graph.invoke(
            {
                "job_id": request.job_id,
                "prompt": request.prompt,
                "inputs": request.inputs,
                "output": request.output,
                "specialist_outputs": [],
            },
            config={"recursion_limit": 210},
        )
        plan: AgentPlan = final["plan"]
        sandbox: SandboxResult = final["sandbox_result"]
        review: ReviewDecision = final["review"]
        ordered_code = sorted(
            final["specialist_outputs"],
            key=lambda item: next(i for i, task in enumerate(plan.tasks) if task.id == item.task_id),
        )
        operations = [
            GoOperation(
                id=item.task_id,
                type=item.specialist,
                params={"goal": plan.tasks[index].goal, "tools": plan.tasks[index].preferred_tools, "depends_on": plan.tasks[index].depends_on},
            )
            for index, item in enumerate(ordered_code)
        ]
        results = [
            OperationResult(
                operation_id=event.get("task_id", f"event-{index + 1:02d}"),
                type=event.get("tool", "agent_tool"),
                artifact=event.get("artifact"),
                duration_ms=event.get("duration_ms", 0),
                metadata={key: value for key, value in event.items() if key not in {"task_id", "tool", "artifact", "duration_ms"}},
            )
            for index, event in enumerate(sandbox.events)
        ]
        return AgentRunResponse(
            plan=GoPlan(summary=plan.summary, operations=operations),
            results=results,
            output=sandbox.output,
            metadata={
                "creative_direction": plan.creative_direction,
                "scene_captions": [scene.model_dump() for scene in final["understanding"].scenes],
                "generated_python": sandbox.code,
                "review": review.model_dump(),
            },
        )

    def _build(self):
        builder = StateGraph(GraphState)
        builder.add_node("understand_video", self._understand)
        builder.add_node("supervisor", self._supervisor)
        for specialist in ("editing", "music", "subtitles", "animation", "export"):
            builder.add_node(f"{specialist}_agent", self._specialist_node)
        builder.add_node("schedule", lambda state: {})
        builder.add_node("sandbox_execute", self._execute)
        builder.add_node("review_output", self._review)
        builder.add_edge(START, "understand_video")
        builder.add_edge("understand_video", "supervisor")
        builder.add_edge("supervisor", "schedule")
        builder.add_conditional_edges("schedule", self._dispatch)
        for specialist in ("editing", "music", "subtitles", "animation", "export"):
            builder.add_edge(f"{specialist}_agent", "schedule")
        builder.add_edge("sandbox_execute", "review_output")
        builder.add_edge("review_output", END)
        return builder.compile()

    def _understand(self, state: GraphState) -> dict[str, Any]:
        return {"understanding": self.vision.analyze(state["inputs"])}

    def _supervisor(self, state: GraphState) -> dict[str, Any]:
        plan = self.llm.plan(state["prompt"], state["understanding"], state["output"])
        # Validate again at the graph boundary, including mutated model instances.
        return {"plan": AgentPlan.model_validate(plan.model_dump())}

    def _dispatch(self, state: GraphState) -> list[Send] | str:
        completed = {item.task_id: item for item in state["specialist_outputs"]}
        pending = [task for task in state["plan"].tasks if task.id not in completed]
        if not pending:
            return "sandbox_execute"
        ready = [task for task in pending if set(task.depends_on) <= completed.keys()]
        if not ready:
            raise ValueError("Task graph stalled: unresolved dependencies")
        return [
            Send(
                f"{task.specialist}_agent",
                {
                    "task": task,
                    "prompt": state["prompt"],
                    "output": state["output"],
                    "understanding": state["understanding"],
                    "creative_direction": state["plan"].creative_direction,
                    "dependency_outputs": [completed[task_id] for task_id in task.depends_on],
                },
            )
            for task in ready
        ]

    def _specialist_node(self, state: SpecialistState) -> dict[str, Any]:
        return {
            "specialist_outputs": [
                self.llm.specialist(
                    state["task"], state["prompt"], state["understanding"],
                    state["creative_direction"], state["output"], state["dependency_outputs"],
                )
            ]
        }

    def _execute(self, state: GraphState) -> dict[str, Any]:
        task_order = {task.id: index for index, task in enumerate(state["plan"].tasks)}
        ordered = sorted(state["specialist_outputs"], key=lambda item: task_order[item.task_id])
        return {
            "sandbox_result": self.sandbox.execute(
                state["job_id"], state["inputs"], state["output"], ordered
            )
        }

    def _review(self, state: GraphState) -> dict[str, Any]:
        output_reference = f"outputs/{state['sandbox_result'].output}"
        output_understanding = self.vision.analyze([output_reference])
        review = self.llm.review(
            state["prompt"], state["plan"], output_understanding,
            state["sandbox_result"].events,
        )
        return {"output_understanding": output_understanding, "review": review}


