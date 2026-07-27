from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    moonshot_api_key: str = ""
    moonshot_base_url: str = "https://api.moonshot.cn/v1"
    moonshot_model: str = "moonshot-v1-8k"
    target_repo: str | None = None

    @property
    def standards_python_dir(self) -> Path:
        return PROJECT_ROOT / "standards" / "python"

    @property
    def standards_php_dir(self) -> Path:
        return PROJECT_ROOT / "standards" / "php"

    @property
    def reports_dir(self) -> Path:
        return PROJECT_ROOT / "reports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
