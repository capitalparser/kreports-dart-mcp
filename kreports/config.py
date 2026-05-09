from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import sys

BASE_DIR = Path(__file__).parent.parent


def _resolve_default_db_url() -> str:
    """dev: project-root DB if present; installed via pip: platform user-data dir."""
    dev_db = BASE_DIR / "kreports.db"
    if dev_db.exists():
        return f"sqlite:///{dev_db}"
    if sys.platform == "darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "kreports"
    elif sys.platform == "win32":
        import os
        data_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "kreports"
    else:
        data_dir = Path.home() / ".local" / "share" / "kreports"
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'kreports.db'}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    dart_api_key: str = ""
    db_url: str = _resolve_default_db_url()

    # 수집 제어
    request_delay: float = 0.5   # API 요청 간 대기 (초)
    max_retries: int = 3
    collect_years: int = 5       # 최근 N개년


settings = Settings()
