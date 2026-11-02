from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


OperationType = Literal["trim", "cut", "merge", "subtitles", "transition", "export"]
Specialist = Literal["editing", "music", "subtitles", "animation", "export"]


class Operation(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    type: OperationType
    params: dict[str, Any] = Field(default_factory=dict)


class ExecuteOperationRequest(BaseModel):
    job_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    operation: Operation
    inputs: list[str] = Field(min_length=1)

    @field_validator("inputs")
    @classmethod
    def validate_artifact_references(cls, values: list[str]) -> list[str]:
        return _validate_artifacts(values)


class ExecuteOperationResponse(BaseModel):
    artifact: str
    output: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunRequest(BaseModel):
    job_id: str = Field(pattern=r"^[a-f0-9]{16}$")
    prompt: str = Field(min_length=3, max_length=10_000)
    inputs: list[str] = Field(min_length=1)
    output: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, values: list[str]) -> list[str]:
        return _validate_artifacts(values)


class SceneCaption(BaseModel):
    input: str
    timestamp_seconds: float
    caption: str


class VideoUnderstanding(BaseModel):
    duration_seconds: float
    width: int
    height: int
    has_audio: bool
    scenes: list[SceneCaption]


class AgentTask(BaseModel):
    id: str = Field(pattern=r"^task-[0-9]{2}$")
    specialist: Specialist
    goal: str
    preferred_tools: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class AgentPlan(BaseModel):
    summary: str
    creative_direction: str
    tasks: list[AgentTask] = Field(min_length=1)


class SpecialistCode(BaseModel):
    task_id: str
    specialist: Specialist
    rationale: str
    python_code: str = Field(description="Only editor.<allowed_tool>(...) call statements")


class ReviewDecision(BaseModel):
    approved: bool
    score: int = Field(ge=1, le=10)
    notes: str


class OperationResult(BaseModel):
    operation_id: str
    type: str
    artifact: str | None = None
    duration_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class GoOperation(BaseModel):
    id: str
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class GoPlan(BaseModel):
    version: str = "agentic-1.0"
    summary: str
    operations: list[GoOperation]


class AgentRunResponse(BaseModel):
    plan: GoPlan
    results: list[OperationResult]
    output: str
    metadata: dict[str, Any] = Field(default_factory=dict)


def _validate_artifacts(values: list[str]) -> list[str]:
    for value in values:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("artifact paths must be safe paths relative to MEDIA_ROOT")
        if not normalized.startswith(("uploads/", "work/")):
            raise ValueError("input artifacts must come from uploads/ or work/")
    return values

