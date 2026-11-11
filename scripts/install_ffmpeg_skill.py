"""Install the pinned ffmpeg-skill checkout inside this project."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "vendor" / "ffmpeg-skill"
COMMIT = "0317ef8c8f2ee300783844afc8f99ca421a0b508"

if (TARGET / "mcp" / "server.py").is_file():
    print(f"ffmpeg-skill already installed: {TARGET}")
else:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "https://github.com/kajisho5/ffmpeg-skill.git", str(TARGET)], check=True)
    subprocess.run(["git", "-C", str(TARGET), "checkout", COMMIT], check=True)
    print(f"Installed ffmpeg-skill in {TARGET}")
print(f"Set FFMPEG_SKILL_MCP_SERVER={TARGET / 'mcp' / 'server.py'}")
