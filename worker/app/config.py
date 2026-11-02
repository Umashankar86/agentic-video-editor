from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    media_root: Path = Path(".")
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    openrouter_api_key: str = ""
    openrouter_model: str = "nvidia/nemotron-3.5-super-vl:free"
    openrouter_fallback_models: str = (
        "xingchen-agi/xing4.0-29b-a4b:free,"
        "dots-studio/dots-3-note-preview:free,"
        "openrouter/free"
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "http://localhost:8080"
    openrouter_app_name: str = "Agentic Video Editor"
    blip_model: str = "Salesforce/blip-image-captioning-base"
    frame_sample_seconds: int = 30
    whisper_model: str = "small"
    sandbox_timeout_seconds: int = 1800

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def uploads_dir(self) -> Path:
        return self.media_root / "uploads"

    @property
    def work_dir(self) -> Path:
        return self.media_root / "work"

    @property
    def outputs_dir(self) -> Path:
        return self.media_root / "outputs"

    @property
    def music_dir(self) -> Path:
        return self.media_root / "assets" / "music"

    @property
    def fallback_model_list(self) -> list[str]:
        return [
            model.strip()
            for model in self.openrouter_fallback_models.split(",")
            if model.strip() and model.strip() != self.openrouter_model
        ]


settings = Settings()

