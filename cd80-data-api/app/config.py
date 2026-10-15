"""Environment based service configuration."""

from dataclasses import dataclass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    admin_api_key: str | None
    read_api_key: str | None
    cors_origins: tuple[str, ...]
    query_timeout_seconds: float


def get_settings() -> Settings:
    configured_path = os.getenv("CD80_DB_PATH", "data/cd80.sqlite3")
    db_path = Path(configured_path).expanduser()
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path

    raw_origins = os.getenv("CD80_CORS_ORIGINS")
    origins = (
        tuple(origin.strip() for origin in raw_origins.split(",") if origin.strip())
        if raw_origins is not None
        else DEFAULT_ORIGINS
    )
    try:
        timeout = float(os.getenv("CD80_QUERY_TIMEOUT_SECONDS", "30"))
    except ValueError as exc:
        raise ValueError("CD80_QUERY_TIMEOUT_SECONDS 必须是正数") from exc
    if not 0 < timeout <= 300:
        raise ValueError("CD80_QUERY_TIMEOUT_SECONDS 必须在 0 到 300 秒之间")

    return Settings(
        db_path=db_path.resolve(),
        admin_api_key=os.getenv("CD80_ADMIN_API_KEY") or None,
        read_api_key=os.getenv("CD80_READ_API_KEY") or None,
        cors_origins=origins,
        query_timeout_seconds=timeout,
    )
