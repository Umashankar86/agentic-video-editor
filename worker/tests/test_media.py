from pathlib import Path

from worker.app.media import MediaProcessor


def test_srt_time() -> None:
    assert MediaProcessor._srt_time(65.432) == "00:01:05,432"


def test_filter_path_escapes_windows_drive() -> None:
    escaped = MediaProcessor._filter_path(Path("C:/media/captions.srt"))
    assert "\\:" in escaped

