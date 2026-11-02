from worker.app.graph import AgenticVideoGraph
from worker.app.models import (
    AgentPlan,
    AgentRunRequest,
    AgentTask,
    ReviewDecision,
    SceneCaption,
    SpecialistCode,
    VideoUnderstanding,
)
from worker.app.sandbox import SandboxResult


class FakeVision:
    def analyze(self, references: list[str]) -> VideoUnderstanding:
        return VideoUnderstanding(
            duration_seconds=10,
            width=1280,
            height=720,
            has_audio=True,
            scenes=[SceneCaption(input=references[0], timestamp_seconds=0, caption="a product")],
        )


class FakeOpenRouter:
    def plan(self, *_args) -> AgentPlan:
        return AgentPlan(
            summary="AI-selected edit",
            creative_direction="clean and energetic",
            tasks=[
                AgentTask(id="task-01", specialist="editing", goal="pace it", preferred_tools=["trim"]),
                AgentTask(id="task-02", specialist="animation", goal="title it", preferred_tools=["add_text"], depends_on=["task-01"]),
                AgentTask(id="task-03", specialist="export", goal="render it", preferred_tools=["export"], depends_on=["task-02"]),
            ],
        )

    def specialist(self, task, *_args) -> SpecialistCode:
        code = {
            "editing": "editor.trim(start=0, end=8)",
            "animation": "editor.fade(kind='both', duration=0.5)",
            "export": "editor.export(filename='demo.mp4', crf=23)",
        }[task.specialist]
        return SpecialistCode(
            task_id=task.id,
            specialist=task.specialist,
            rationale="fake",
            python_code=code,
        )

    def review(self, *_args) -> ReviewDecision:
        return ReviewDecision(approved=True, score=9, notes="good")


class FakeSandbox:
    def execute(self, _job_id, _inputs, output, code) -> SandboxResult:
        assert [item.task_id for item in code] == ["task-01", "task-02", "task-03"]
        return SandboxResult(
            output=output,
            events=[{"task_id": "task-01", "tool": "trim", "artifact": "work/trim.mp4"}],
            code="generated code",
        )


def test_langgraph_fans_out_specialists_and_joins_before_sandbox() -> None:
    graph = AgenticVideoGraph.__new__(AgenticVideoGraph)
    graph.vision = FakeVision()
    graph.llm = FakeOpenRouter()
    graph.sandbox = FakeSandbox()
    graph.graph = graph._build()
    result = graph.run(
        AgentRunRequest(
            job_id="a" * 16,
            prompt="make the best edit",
            inputs=["uploads/input.mp4"],
            output="demo.mp4",
        )
    )
    assert result.output == "demo.mp4"
    assert [operation.type for operation in result.plan.operations] == ["editing", "animation", "export"]
    assert result.metadata["review"]["approved"] is True

