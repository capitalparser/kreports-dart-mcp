from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dart_api_key: str = ""
    db_url: str = f"sqlite:///{BASE_DIR}/kreports.db"

    # 수집 제어
    request_delay: float = 0.5   # API 요청 간 대기 (초)
    max_retries: int = 3
    collect_years: int = 5       # 최근 N개년


settings = Settings()
