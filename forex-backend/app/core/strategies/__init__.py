from typing import Dict, Optional
from app.core.strategies.base import BaseStrategy
from app.core.strategies.ema_pullback import EmaPullbackStrategy
from app.core.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from app.core.strategies.breakout_atr import BreakoutAtrStrategy
from app.models.schemas import StrategyName


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRY — single source of truth for all strategies
# ─────────────────────────────────────────────────────────────────────────────

STRATEGY_REGISTRY: Dict[StrategyName, BaseStrategy] = {
    StrategyName.EMA_PULLBACK: EmaPullbackStrategy(),
    StrategyName.RSI_MEAN_REVERSION: RsiMeanReversionStrategy(),
    StrategyName.BREAKOUT_ATR: BreakoutAtrStrategy(),
}

# Default strategy configurations (timeframe + symbol recommendations)
STRATEGY_DEFAULTS = {
    StrategyName.EMA_PULLBACK: {
        "timeframe": "H1",
        "recommended_symbols": ["EUR_USD", "GBP_USD", "USD_JPY"],
        "description": "EMA 50/200 trend-following with pullback entry. Best in trending markets.",
    },
    StrategyName.RSI_MEAN_REVERSION: {
        "timeframe": "H4",
        "recommended_symbols": ["EUR_USD", "USD_CHF", "AUD_USD"],
        "description": "RSI oversold/overbought reversals filtered by EMA200 trend direction. Best in ranging-to-trending markets.",
    },
    StrategyName.BREAKOUT_ATR: {
        "timeframe": "H4",
        "recommended_symbols": ["GBP_JPY", "USD_CAD", "GBP_USD"],
        "description": "ATR volatility breakout with strong momentum confirmation. Best during session opens (London, NY).",
    },
}


def get_strategy(name: StrategyName) -> BaseStrategy:
    """Get strategy instance by name. Raises if not found."""
    strategy = STRATEGY_REGISTRY.get(name)
    if strategy is None:
        raise ValueError(f"Unknown strategy: {name}")
    return strategy


def get_all_strategies() -> Dict[StrategyName, BaseStrategy]:
    return STRATEGY_REGISTRY
