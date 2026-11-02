import logging

from fastapi import FastAPI, HTTPException

from .config import settings
from .media import MediaError, MediaProcessor
from .models import (
    AgentRunRequest,
    AgentRunResponse,
    ExecuteOperationRequest,
    ExecuteOperationResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="Agentic Video Editor Media Worker",
    version="1.0.0",
    description="Executes validated video editing operations with FFmpeg and OpenCV.",
)
processor = MediaProcessor(settings)
agent_graph = None


def get_agent_graph():
    global agent_graph
    if agent_graph is None:
        from .graph import AgenticVideoGraph

        agent_graph = AgenticVideoGraph(settings, processor)
    return agent_graph


@app.get("/healthz")
def health() -> dict[str, object]:
    try:
        dependencies = processor.check_dependencies()
    except MediaError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "ok",
        "dependencies": dependencies,
        "ai": {
            "provider": "openrouter",
            "model": settings.openrouter_model,
            "fallback_models": settings.fallback_model_list,
            "base_url": settings.openrouter_base_url,
            "configured": bool(settings.openrouter_api_key),
            "vision": settings.blip_model,
        },
    }


@app.post("/v1/operations/execute", response_model=ExecuteOperationResponse)
def execute_operation(request: ExecuteOperationRequest) -> ExecuteOperationResponse:
    try:
        return processor.execute(request)
    except MediaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/agent/run", response_model=AgentRunResponse)
def run_agent(request: AgentRunRequest) -> AgentRunResponse:
    try:
        return get_agent_graph().run(request)
    except (MediaError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

