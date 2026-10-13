"""Allowlisted registry: no dynamic imports or user-supplied source execution."""
from hashlib import sha256
from pathlib import Path

from .strategies import (
    sma_trend, ema_trend, macd_momentum, roc_momentum, donchian_breakout,
    bollinger_reversion, rsi_reversion, keltner_breakout,
    chandelier_trend, stochastic_reversal,
)

STRATEGIES = {module.SPEC.key: module for module in (
    sma_trend, ema_trend, macd_momentum, roc_momentum, donchian_breakout,
    bollinger_reversion, rsi_reversion, keltner_breakout,
    chandelier_trend, stochastic_reversal,
)}
FALLBACK_ID = "sma_trend"


def get_strategy(strategy_id: str):
    try:
        return STRATEGIES[strategy_id]
    except KeyError as exc:
        raise ValueError(f"Unknown curated strategy: {strategy_id}") from exc


def source_code(strategy_id: str) -> str:
    return Path(get_strategy(strategy_id).__file__).read_text(encoding="utf-8")


def implementation_hash() -> str:
    digest = sha256()
    root = Path(__file__).parent
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()

