"""Configuration for the standalone Ficagent Flask service."""

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
    FRONTEND_DIST = site_path(os.environ.get("FRONTEND_DIST", "frontend/dist"))
    GITEE_AI_API_KEY = os.environ.get("GITEE_AI_API_KEY", "")
    GITEE_AI_BASE_URL = os.environ.get("GITEE_AI_BASE_URL", "https://ai.gitee.com/v1")
    GITEE_AI_MODEL = os.environ.get("GITEE_AI_MODEL", "GLM-5.3")
    GITEE_AI_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("GITEE_AI_CONNECT_TIMEOUT_SECONDS", "5"))
    GITEE_AI_READ_TIMEOUT_SECONDS = float(os.environ.get("GITEE_AI_READ_TIMEOUT_SECONDS", "120"))
    IMPORT_LEGACY_HISTORY = os.environ.get("IMPORT_LEGACY_HISTORY", "0").strip().lower() in {"1", "true", "yes", "on"}
    CHAT_IDLE_TIMEOUT_SECONDS = 120
