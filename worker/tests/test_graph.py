import threading

import pytest
from pydantic import ValidationError

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


def test_sequential_chain_joins_before_sandbox() -> None:
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




def make_graph(llm, sandbox):
    graph = AgenticVideoGraph.__new__(AgenticVideoGraph)
    graph.vision = FakeVision()
    graph.llm = llm
    graph.sandbox = sandbox
    graph.graph = graph._build()
    return graph


def request():
    return AgentRunRequest(job_id='a' * 16, prompt='edit the video',
                           inputs=['uploads/input.mp4'], output='demo.mp4')


def test_parallel_roots_dependency_context_and_single_final_execution():
    barrier = threading.Barrier(2)
    completed = set()
    lock = threading.Lock()

    class Agents(FakeOpenRouter):
        def plan(self, *_):
            return AgentPlan(summary='mixed', creative_direction='clean', tasks=[
                AgentTask(id='task-01', specialist='editing', goal='trim'),
                AgentTask(id='task-02', specialist='editing', goal='grade'),
                AgentTask(id='task-03', specialist='animation', goal='title', depends_on=['task-01', 'task-02']),
                AgentTask(id='task-04', specialist='export', goal='export'),
            ])

        def specialist(self, task, *args):
            dependencies = args[-1]
            assert {item.task_id for item in dependencies} == set(task.depends_on)
            with lock:
                assert set(task.depends_on) <= completed
            if task.id in {'task-01', 'task-02'}:
                barrier.wait(timeout=5)  # Fails if independent agents are serialized.
            result = super().specialist(task)
            with lock:
                assert task.id not in completed
                completed.add(task.id)
            return result

    class Sandbox:
        calls = 0
        def execute(self, _job, _inputs, output, code):
            self.calls += 1
            assert len(completed) == 4
            assert [item.task_id for item in code] == ['task-01', 'task-02', 'task-03', 'task-04']
            return SandboxResult(output=output, events=[], code='joined')

    sandbox = Sandbox()
    result = make_graph(Agents(), sandbox).run(request())
    assert sandbox.calls == 1
    assert result.plan.operations[-1].params['depends_on'] == ['task-01', 'task-02', 'task-03']


@pytest.mark.parametrize('tasks', [
    [('task-01', 'editing', ['task-01']), ('task-02', 'export', [])],
    [('task-01', 'editing', ['task-99']), ('task-02', 'export', [])],
    [('task-01', 'editing', ['task-02']), ('task-02', 'export', ['task-01'])],
    [('task-01', 'editing', []), ('task-01', 'export', [])],
    [('task-01', 'export', []), ('task-02', 'editing', [])],
    [('task-01', 'editing', [])],
    [('task-01', 'export', []), ('task-02', 'export', [])],
])
def test_invalid_plans_rejected(tasks):
    with pytest.raises(ValidationError):
        AgentPlan(summary='invalid', creative_direction='', tasks=[
            AgentTask(id=id, specialist=specialist, goal='', depends_on=deps)
            for id, specialist, deps in tasks
        ])


def test_failed_dependency_prevents_rendering():
    class Agents(FakeOpenRouter):
        def specialist(self, task, *_):
            assert task.id == 'task-01'
            raise RuntimeError('agent failed')

    class Sandbox:
        def execute(self, *_):
            pytest.fail('must not render after agent failure')

    with pytest.raises(RuntimeError, match='agent failed'):
        make_graph(Agents(), Sandbox()).run(request())


def test_long_sequential_plan():
    class Agents(FakeOpenRouter):
        def plan(self, *_):
            return AgentPlan(summary='chain', creative_direction='', tasks=[
                AgentTask(id=f'task-{i:02d}', specialist='export' if i == 20 else 'editing',
                          goal='', depends_on=[f'task-{i-1:02d}'] if i > 1 else [])
                for i in range(1, 21)
            ])
        def specialist(self, task, *args):
            assert {item.task_id for item in args[-1]} == set(task.depends_on)
            return super().specialist(task)

    class Sandbox:
        def execute(self, _job, _inputs, output, code):
            assert len(code) == 20
            return SandboxResult(output=output, events=[], code='chain')

    make_graph(Agents(), Sandbox()).run(request())
