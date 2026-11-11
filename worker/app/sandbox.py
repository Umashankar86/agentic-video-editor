from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .models import SpecialistCode


ALLOWED_METHODS = {
    "merge", "trim", "cut", "transition", "crop", "speed", "color_grade",
    "add_music", "select_music", "transcribe_subtitles", "add_subtitles",
    "add_text", "zoom", "fade", "export", "mcp_effect",
}


@dataclass
class SandboxResult:
    output: str
    events: list[dict[str, Any]]
    code: str


class SandboxViolation(ValueError):
    pass


class SandboxExecutor:
    """Runs model-generated editor calls after a strict AST allow-list check.

    This is a capability sandbox: model code can only call methods on the supplied
    VideoEditor object with literal arguments. Docker adds the OS/container boundary.
    """

    def __init__(self, config: Settings) -> None:
        self.config = config

    def execute(
        self,
        job_id: str,
        inputs: list[str],
        output: str,
        specialist_outputs: list[SpecialistCode],
    ) -> SandboxResult:
        job_dir = self.config.work_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        body: list[str] = []
        for specialist in specialist_outputs:
            self.validate(specialist.python_code)
            body.append(f"editor.set_task({specialist.task_id!r})")
            body.append(specialist.python_code.strip())

        script = "\n".join(
            [
                "import json",
                "from worker.app.agent_runtime import VideoEditor",
                f"editor = VideoEditor({job_id!r}, {inputs!r}, {output!r})",
                *body,
                "result = editor.finish()",
                "print('__AGENT_RESULT__' + json.dumps(result))",
                "",
            ]
        )
        script_path = job_dir / "generated_pipeline.py"
        script_path.write_text(script, encoding="utf-8")

        environment = os.environ.copy()
        project_root = Path(__file__).resolve().parents[2]
        environment["PYTHONPATH"] = str(project_root)
        environment["MEDIA_ROOT"] = str(self.config.media_root.resolve())
        command = [sys.executable, "-I", str(script_path)]
        # -I ignores PYTHONPATH, so put the project root into the trusted wrapper.
        trusted_paths = [str(project_root), *[path for path in sys.path if path and Path(path).is_dir()]]
        bootstrap = (
            "import runpy,sys;"
            f"[sys.path.insert(0,p) for p in {trusted_paths!r}];"
            f"runpy.run_path({str(script_path)!r},run_name='__main__')"
        )
        command = [sys.executable, "-I", "-c", bootstrap]
        try:
            completed = subprocess.run(
                command,
                cwd=project_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=self.config.sandbox_timeout_seconds,
                check=False,
                **self._resource_limits(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("generated edit program exceeded sandbox timeout") from exc
        if completed.returncode != 0:
            detail = "\n".join(completed.stderr.splitlines()[-20:])
            raise RuntimeError(f"generated edit program failed:\n{detail}")
        marker = next(
            (line[len("__AGENT_RESULT__") :] for line in completed.stdout.splitlines() if line.startswith("__AGENT_RESULT__")),
            None,
        )
        if marker is None:
            raise RuntimeError("sandbox returned no structured result")
        payload = json.loads(marker)
        output_path = self.config.outputs_dir / payload["output"]
        if not output_path.is_file():
            raise RuntimeError("sandbox reported an output that does not exist")
        return SandboxResult(output=payload["output"], events=payload["events"], code=script)

    @staticmethod
    def validate(code: str) -> None:
        if len(code) > 8_000:
            raise SandboxViolation("generated code is too large")
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            raise SandboxViolation(f"generated code is invalid Python: {exc}") from exc
        if not tree.body:
            raise SandboxViolation("generated code is empty")
        for statement in tree.body:
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                raise SandboxViolation("only editor method call statements are allowed")
            call = statement.value
            if call.args:
                raise SandboxViolation("use literal keyword arguments only")
            if not isinstance(call.func, ast.Attribute) or not isinstance(call.func.value, ast.Name):
                raise SandboxViolation("only calls on editor are allowed")
            if call.func.value.id != "editor" or call.func.attr not in ALLOWED_METHODS:
                raise SandboxViolation(f"editor method is not allowed: {call.func.attr}")
            for keyword in call.keywords:
                if keyword.arg is None or not _literal_node(keyword.value):
                    raise SandboxViolation("arguments must be finite literal values")

    @staticmethod
    def _resource_limits() -> dict[str, Any]:
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NO_WINDOW}

        def limit() -> None:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (1_200, 1_200))
            resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
            # BLIP/Whisper run outside this subprocess; editing gets an 8 GiB ceiling.
            resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))

        return {"preexec_fn": limit}


def _literal_node(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, int, float, bool, type(None)))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return isinstance(node.operand, ast.Constant) and isinstance(node.operand.value, (int, float))
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_literal_node(value) for value in node.elts)
    if isinstance(node, ast.Dict):
        return all(_literal_node(key) and _literal_node(value) for key, value in zip(node.keys, node.values))
    return False

