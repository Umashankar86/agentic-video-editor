"""Restricted stdio MCP adapter for ffmpeg-skill.

MCP is optional. Existing native editing methods remain the default.
Only named media-transform tools can be invoked; generated code cannot
control the MCP process, input paths, output paths, or raw argv.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ALLOWED_MCP_TOOLS = frozenset({
    "cut", "fit", "caption", "audio", "loudness", "graphics",
    "silence", "export", "fade", "speed", "color", "crop",
    "join", "overlay", "transition", "trim", "subtitles",
})
FORBIDDEN_ARGUMENTS = frozenset({
    "argv", "input", "inputs", "output", "source", "destination",
    "infile", "outfile", "input_file", "output_file", "file",
    "path", "command", "shell", "script",
})


class MCPToolError(RuntimeError):
    pass


def validate_options(options: dict[str, Any]) -> None:
    if not isinstance(options, dict):
        raise MCPToolError("MCP options must be a dictionary")
    if len(options) > 24:
        raise MCPToolError("too many MCP arguments")
    for key, value in options.items():
        if not isinstance(key, str) or key.lower() in FORBIDDEN_ARGUMENTS:
            raise MCPToolError(f"MCP argument is reserved: {key!r}")
        if key.startswith("_") or key in {"json", "progress"}:
            raise MCPToolError(f"MCP argument is not allowed: {key}")
        if not isinstance(value, (str, int, float, bool, type(None), list)):
            raise MCPToolError(f"unsupported MCP argument type: {key}")
        if isinstance(value, str) and len(value) > 1000:
            raise MCPToolError("MCP string argument too long")
        if isinstance(value, list) and (len(value) > 30 or not all(isinstance(v, (str, int, float, bool)) for v in value)):
            raise MCPToolError("MCP list argument is invalid")


async def _invoke(name: str, options: dict[str, Any], source: Path, target: Path, server: Path) -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    command = os.getenv("FFMPEG_SKILL_PYTHON", sys.executable)
    params = StdioServerParameters(
        command=command,
        args=[str(server)],
        env={**os.environ, "FFMPEG_SKILL_MCP_FULL": "1"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            available = {item.name: item for item in listing.tools}
            if name not in available:
                raise MCPToolError(f"MCP tool not advertised: {name}")
            schema = available[name].inputSchema or {}
            properties = schema.get("properties", {})
            arguments = dict(options)
            # ffmpeg-skill contracts declare their input/output fields.
            # Never accept file paths from generated code.
            for field in ("inputs", "input", "source", "input_file"):
                if field in properties:
                    arguments[field] = [str(source)] if field == "inputs" else str(source)
                    break
            for field in ("output", "destination", "output_file"):
                if field in properties:
                    arguments[field] = str(target)
                    break
            required = set(schema.get("required", []))
            if not required.issubset(arguments):
                raise MCPToolError(f"MCP tool needs additional arguments: {sorted(required - arguments.keys())}")
            unknown = arguments.keys() - properties.keys()
            if unknown and properties.get("additionalProperties") is not True:
                raise MCPToolError(f"unknown MCP arguments: {sorted(unknown)}")
            result = await session.call_tool(name, arguments)
            if result.isError:
                raise MCPToolError(f"MCP tool {name} reported an error: {result.content}")
            payload: dict[str, Any] = {}
            for item in result.content:
                if getattr(item, "type", None) == "text":
                    try:
                        decoded = json.loads(item.text)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(decoded, dict):
                        payload = decoded
                        break
            if payload.get("status") in ("failed", "error"):
                raise MCPToolError(f"MCP tool failed: {payload.get('error')}")
            return payload


def execute_mcp_tool(
    name: str, options: dict[str, Any], source: Path, target: Path,
    *, server_path: str, timeout: int = 600,
) -> dict[str, Any]:
    if name not in ALLOWED_MCP_TOOLS:
        raise MCPToolError(f"MCP tool is not permitted: {name}")
    validate_options(options)
    server = Path(server_path).expanduser().resolve()
    if not server.is_file() or server.name != "server.py" or server.parent.name != "mcp":
        raise MCPToolError("FFMPEG_SKILL_MCP_SERVER must point to ffmpeg-skill/mcp/server.py")
    source = source.resolve(strict=True)
    target = target.resolve()
    if source == target or target.exists():
        raise MCPToolError("MCP output must be a fresh file distinct from the input")
    if target.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise MCPToolError("MCP output must be a supported video file")
    try:
        payload = asyncio.run(asyncio.wait_for(_invoke(name, options, source, target, server), timeout))
    except TimeoutError as exc:
        raise MCPToolError("MCP execution timed out") from exc
    if not target.is_file() or target.stat().st_size == 0:
        raise MCPToolError("MCP operation did not create the expected output")
    return payload
