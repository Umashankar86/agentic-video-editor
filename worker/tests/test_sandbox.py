import pytest

from worker.app.sandbox import SandboxExecutor, SandboxViolation


def test_sandbox_accepts_editor_calls_with_literals() -> None:
    SandboxExecutor.validate(
        "editor.trim(start=1.5, end=8)\neditor.fade(kind='both', duration=0.5)"
    )


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "open('secret.txt').read()",
        "editor.trim(start=__import__('os'), end=8)",
        "for x in [1]:\n    editor.trim(start=0, end=x)",
        "editor.__class__()",
    ],
)
def test_sandbox_rejects_arbitrary_python(code: str) -> None:
    with pytest.raises(SandboxViolation):
        SandboxExecutor.validate(code)

