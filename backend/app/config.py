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
    SKILLS_DIR = site_path(os.environ.get("SKILLS_DIR", "skills"))
    GITEE_AI_API_KEY = os.environ.get("GITEE_AI_API_KEY", "")
    GITEE_AI_BASE_URL = os.environ.get("GITEE_AI_BASE_URL", "https://ai.gitee.com/v1")
    GITEE_AI_MODEL = os.environ.get("GITEE_AI_MODEL", "GLM-5.3")
    GITEE_AI_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("GITEE_AI_CONNECT_TIMEOUT_SECONDS", "5"))
    GITEE_AI_READ_TIMEOUT_SECONDS = float(os.environ.get("GITEE_AI_READ_TIMEOUT_SECONDS", "120"))
    GITEE_AI_SEARCH_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("GITEE_AI_SEARCH_CONNECT_TIMEOUT_SECONDS", "3"))
    GITEE_AI_SEARCH_READ_TIMEOUT_SECONDS = float(os.environ.get("GITEE_AI_SEARCH_READ_TIMEOUT_SECONDS", "8"))
    SFC_TOKEN = os.environ.get("SFC_TOKEN", "")
    # No implicit dependency on the local Codex account or its private files.
    SFC_KEY_FILE = site_path(os.environ["SFC_KEY_FILE"]) if os.environ.get("SFC_KEY_FILE") else ""
    SFC_REQUEST_TIMEOUT = float(os.environ.get("SFC_REQUEST_TIMEOUT", "20"))
    IMPORT_LEGACY_HISTORY = os.environ.get("IMPORT_LEGACY_HISTORY", "0").strip().lower() in {"1", "true", "yes", "on"}
    CHAT_IDLE_TIMEOUT_SECONDS = 120
    AGENT_RUNTIME_DIR = site_path(os.environ.get("AGENT_RUNTIME_DIR", "data/agent-runtime"))
    AGENT_EXECUTION_TIMEOUT = float(os.environ.get("AGENT_EXECUTION_TIMEOUT", "15"))
    AGENT_MEMORY_MB = int(os.environ.get("AGENT_MEMORY_MB", "128"))
    AGENT_MODEL_MAX_TOKENS = int(os.environ.get("AGENT_MODEL_MAX_TOKENS", "8192"))
    AGENT_MODEL_REASONING_EFFORT = os.environ.get("AGENT_MODEL_REASONING_EFFORT", "low")
    CD80_DATA_API_URL = os.environ.get("CD80_DATA_API_URL", "http://127.0.0.1:8897")
    CD80_ADMIN_API_KEY = os.environ.get("CD80_ADMIN_API_KEY", "")
    CD80_READ_API_KEY = os.environ.get("CD80_READ_API_KEY", "")
    CD80_DATA_ADMIN_USERS = os.environ.get("CD80_DATA_ADMIN_USERS", "")
    NEWS_CACHE_TTL_SECONDS = float(os.environ.get("NEWS_CACHE_TTL_SECONDS", "60"))
    NEWS_FETCH_TIMEOUT_SECONDS = float(os.environ.get("NEWS_FETCH_TIMEOUT_SECONDS", "15"))
    NEWS_MAX_STALE_SECONDS = float(os.environ.get("NEWS_MAX_STALE_SECONDS", "900"))
    NEWS_RETRY_SECONDS = float(os.environ.get("NEWS_RETRY_SECONDS", "30"))
    EXPLORE_RUN_TIMEOUT_SECONDS = float(os.environ.get("EXPLORE_RUN_TIMEOUT_SECONDS", "600"))
    EXPLORE_MAX_CONCURRENT_RUNS = int(os.environ.get("EXPLORE_MAX_CONCURRENT_RUNS", "2"))
    EXPLORE_NEWS_WINDOW = int(os.environ.get("EXPLORE_NEWS_WINDOW", "100"))
    EXPLORE_MODEL_MAX_TOKENS = int(os.environ.get("EXPLORE_MODEL_MAX_TOKENS", "8192"))
    EXPLORE_MODEL_REASONING_EFFORT = os.environ.get("EXPLORE_MODEL_REASONING_EFFORT", "low")

    EXPLORE_PUBLIC_DNS_MODE = os.environ.get("EXPLORE_PUBLIC_DNS_MODE", "system")
    EXPLORE_PUBLIC_ENTRY_CATALOG = os.environ.get("EXPLORE_PUBLIC_ENTRY_CATALOG", "[]")
    EXPLORE_RESEARCH_ROUNDS = int(os.environ.get("EXPLORE_RESEARCH_ROUNDS", "5"))
    EXPLORE_TOOL_CALLS = int(os.environ.get("EXPLORE_TOOL_CALLS", "12"))
