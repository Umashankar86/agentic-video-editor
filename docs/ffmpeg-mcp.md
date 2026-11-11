## Optional ffmpeg-skill MCP backend

This branch adds a shared Python MCP client in `worker/app/mcp_client.py` and
an optional `editor.mcp_effect(...)` capability available to all five specialist
agents. **Native editing is still available and is the default**. LangGraph
dependencies, agent count, rendering order and AST validation are unchanged.

1. Install FFmpeg and clone [kajisho5/ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill)
   to a location accessible to the **worker process**.
2. Install Python dependencies: `pip install -r worker/requirements.txt`.
3. Configure the absolute path to that checkout's `mcp/server.py`:
   `FFMPEG_SKILL_MCP_SERVER=/absolute/path/ffmpeg-skill/mcp/server.py`.
   Optionally set `FFMPEG_SKILL_PYTHON` to the Python executable and
   `FFMPEG_SKILL_TIMEOUT_SECONDS=600`.
4. On Docker, mount the checkout into the worker container, e.g. add
   `- /your/host/ffmpeg-skill:/opt/ffmpeg-skill:ro` under worker volumes and
   use `FFMPEG_SKILL_MCP_SERVER=/opt/ffmpeg-skill/mcp/server.py`.
   A Windows-host path inside a Linux container will not work.
5. Run `python -m pytest worker/tests/test_mcp_client.py -q` and then test
   a representative short input clip with an MCP-capable operation.

Specialists can choose native methods such as `editor.trim(start=2, end=5)`
or invoke a restricted MCP transformation:

```python
editor.mcp_effect(name="fit", options={"aspect": "9:16", "fit": "crop"})
```

The wrapper supplies the current video as input and the next numbered
intermediate video as output. Input/output path overrides, raw `argv`,
shell commands, and arbitrary MCP tool names are rejected. Each successful
step is recorded as an `mcp_<name>` event. MCP failure fails the job rather
than silently running a different edit.

**Limitations:** This is an initial experimental integration. The MCP server
must be installed separately; ffmpeg-skill tools accept different argument
schemas and not every option or operation is supported by this adapter.
The integration has not been exercised end-to-end against real media here.
Review the actual ffmpeg-skill tool contract before enabling more operations.
The subprocess sandbox is not a hardened untrusted-code or filesystem boundary:
run it in an isolated container and do not expose arbitrary filesystem paths
to untrusted callers.
