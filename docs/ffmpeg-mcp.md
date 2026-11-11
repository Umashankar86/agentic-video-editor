# ffmpeg-skill MCP in the Agentic Video Editor

The ffmpeg-skill server runs **inside this project**, under `vendor/ffmpeg-skill/mcp/server.py`.
It uses stdio, so no separate HTTP server or port is needed.

## Docker (recommended)

```bash
docker compose up --build
```

The worker Dockerfile clones a pinned ffmpeg-skill revision into
`/app/vendor/ffmpeg-skill` and configures
`FFMPEG_SKILL_MCP_SERVER=/app/vendor/ffmpeg-skill/mcp/server.py`.
No separate checkout or volume mount is needed. Docker image builds require
access to GitHub to download the dependency.

## Native Windows/Linux

From the project root:

```bash
python scripts/install_ffmpeg_skill.py
python -m pip install -r worker/requirements.txt
```

The installer clones the same pinned revision into `vendor/ffmpeg-skill`.
Set `FFMPEG_SKILL_MCP_SERVER` to that path (already the default setting),
ensure ffmpeg/ffprobe and Python are installed, then start the worker as usual.
`vendor/` is ignored by Git to avoid copying the whole third-party project
into this repository.

## Architecture

The supervisor and five specialist agents are unchanged. Specialists may
generate either native operations or `editor.mcp_effect(...)` calls:

```python
editor.mcp_effect(name="fit", options={"aspect": "9:16", "fit": "crop"})
```

The trusted adapter sets the input/output paths and connects to the local
ffmpeg-skill MCP server via stdio. It restricts supported transforming
tools to: `cut`, `fit`, `silence`, `loudness`, `caption`, and `export`.
The original native tools remain available. All edits still execute
sequentially on a shared timeline, while independent specialist **plans**
can be created concurrently.

## Testing

```bash
python -m pytest worker/tests/test_mcp_client.py -q
python -m pytest worker/tests -q
```

Then test a real short clip with `editor.mcp_effect(name="fit", ...)`.
MCP errors fail closed rather than silently substituting a native operation.

**Current limitation:** This integration has not been verified with a real-media
end-to-end run in this environment. Only a subset of ffmpeg-skill's tool
contracts is supported. Input/output names are controlled by the adapter;
arbitrary tool arguments or external file paths must not be accepted.
Treat the child-process limits as defense-in-depth, not a hardened sandbox.
