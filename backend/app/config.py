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
    AGENT_RUNTIME_DIR = site_path(os.environ.get("AGENT_RUNTIME_DIR", "data/agent-runtime"))
    AGENT_EXECUTION_TIMEOUT = float(os.environ.get("AGENT_EXECUTION_TIMEOUT", "15"))
    AGENT_MEMORY_MB = int(os.environ.get("AGENT_MEMORY_MB", "128"))
    AGENT_MODEL_MAX_TOKENS = int(os.environ.get("AGENT_MODEL_MAX_TOKENS", "8192"))
    AGENT_MODEL_REASONING_EFFORT = os.environ.get("AGENT_MODEL_REASONING_EFFORT", "low")
    STRATEGY_WORKSHOP_MARKET_DB = site_path(os.environ.get("STRATEGY_WORKSHOP_MARKET_DB", "data/strategy-workshop-market/derived/normalized.sqlite3"))
    STRATEGY_WORKSHOP_SOURCE_DB = site_path(os.environ.get("STRATEGY_WORKSHOP_SOURCE_DB", "data/strategy-workshop-market/cd80_source_snapshot.sqlite3"))
    STRATEGY_WORKSHOP_MAPPING_DIR = site_path(os.environ.get("STRATEGY_WORKSHOP_MAPPING_DIR", "data/strategy-workshop-market/derived"))
    STRATEGY_WORKSHOP_RUN_TIMEOUT = float(os.environ.get("STRATEGY_WORKSHOP_RUN_TIMEOUT", "180"))
    STRATEGY_WORKSHOP_MATCH_TIMEOUT = float(os.environ.get("STRATEGY_WORKSHOP_MATCH_TIMEOUT", "30"))
    STRATEGY_WORKSHOP_MAX_RUNS = int(os.environ.get("STRATEGY_WORKSHOP_MAX_RUNS", "2"))
    STRATEGY_WORKSHOP_INSTRUMENTS = os.environ.get("STRATEGY_WORKSHOP_INSTRUMENTS", "{}")
