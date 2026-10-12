"""Configuration for the local Ficagent Flask service."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def site_path(value):
    """Resolve configuration paths against the website, regardless of the cwd."""
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else PROJECT_ROOT / path).resolve())


class Config:
    MAX_CONTENT_LENGTH = 35 * 1024 * 1024
    DESKTOP_PORT = int(os.environ.get("PORT", os.environ.get("DESKTOP_PORT", "8893")))
    DATA_DIR = site_path(os.environ.get("DATA_DIR", "data"))
    COZE_BASE_URL = os.environ.get("COZE_BASE_URL", "http://127.0.0.1:8888")
    FRONTEND_DIST = site_path(os.environ.get("FRONTEND_DIST", "frontend/dist"))
    CHAT_IDLE_TIMEOUT_SECONDS = 120
