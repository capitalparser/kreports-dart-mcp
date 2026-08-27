from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import os
import sys

BASE_DIR = Path(__file__).parent.parent


def _resolve_default_db_url() -> str:
    """dev: project-root DB if present; installed via pip: platform user-data dir."""
    dev_db = BASE_DIR / "kreports.db"
    if dev_db.exists():
        return f"sqlite:///{dev_db}"
    legacy_dev_db = BASE_DIR / "dart_platform.db"
    if legacy_dev_db.exists():
        return f"sqlite:///{legacy_dev_db}"
    if sys.platform == "darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "kreports"
    elif sys.platform == "win32":
        data_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "kreports"
    else:
        data_home = os.environ.get("XDG_DATA_HOME")
        data_dir = (
            Path(data_home) / "kreports"
            if data_home
            else Path.home() / ".local" / "share" / "kreports"
        )
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

    # 원문 저장소. inline은 DB raw_content에 보관, file/gcs는 storage_uri만 남김.
    raw_storage_backend: str = "inline"
    raw_storage_bucket: str = ""
    raw_storage_prefix: str = ""
    raw_storage_keep_inline: bool = False
    raw_storage_drive_remote: str = ""
    raw_storage_spool_dir: str = ""


settings = Settings()
