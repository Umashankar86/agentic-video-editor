import pytest

from worker.app.mcp_client import MCPToolError, validate_options, execute_mcp_tool
from worker.app.sandbox import SandboxExecutor, SandboxViolation


def test_mcp_options_allow_literal_effect_configuration():
    validate_options({"aspect": "9:16", "fit": "crop", "quality": 22})


@pytest.mark.parametrize("options", [
    {"argv": ["-i", "secret.mp4"]},
    {"input": "/etc/passwd"},
    {"output": "outside.mp4"},
    {"command": "rm -rf /"},
    {"custom": {"nested": "file"}},
])
def test_mcp_rejects_unsafe_options(options):
    with pytest.raises(MCPToolError):
        validate_options(options)


def test_generated_mcp_calls_remain_ast_validated():
    SandboxExecutor.validate("editor.mcp_effect(name='fit', options={'aspect': '9:16'})")
    with pytest.raises(SandboxViolation):
        SandboxExecutor.validate("editor.mcp_effect(name=__import__('os'), options={})")


def test_mcp_missing_server_fails_closed(tmp_path):
    original = tmp_path / "input.mp4"
    original.write_bytes(b"placeholder")
    with pytest.raises(MCPToolError):
        execute_mcp_tool(
            "fit", {"aspect": "9:16"}, original, tmp_path / "result.mp4",
            server_path=str(tmp_path / "nonexistent" / "server.py"),
        )
