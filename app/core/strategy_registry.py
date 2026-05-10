from typing import Dict
from app.core.strategies.base import BaseStrategy
from app.core.strategies.ema_pullback import EmaPullbackStrategy
from app.core.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from app.core.strategies.breakout_atr import BreakoutAtrStrategy
from app.models.schemas import StrategyName


STRATEGY_REGISTRY: Dict[StrategyName, BaseStrategy] = {
    StrategyName.EMA_PULLBACK: EmaPullbackStrategy(),
    StrategyName.RSI_MEAN_REVERSION: RsiMeanReversionStrategy(),
    StrategyName.BREAKOUT_ATR: BreakoutAtrStrategy(),
}


STRATEGY_DEFAULTS = {
    StrategyName.EMA_PULLBACK: {
        "timeframe": "H1",
        "recommended_symbols": ["EUR_USD", "GBP_USD", "USD_JPY"],
        "description": "EMA 50/200 trend-following with pullback entry.",
    },
    StrategyName.RSI_MEAN_REVERSION: {
        "timeframe": "H4",
        "recommended_symbols": ["EUR_USD", "USD_CHF", "AUD_USD"],
        "description": "RSI mean reversion strategy.",
    },
    StrategyName.BREAKOUT_ATR: {
        "timeframe": "H4",
        "recommended_symbols": ["GBP_JPY", "USD_CAD", "GBP_USD"],
        "description": "ATR breakout strategy.",
    },
}


def get_strategy(name: StrategyName) -> BaseStrategy:
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy: {name}")
    return STRATEGY_REGISTRY[name]


def get_all_strategies() -> Dict[StrategyName, BaseStrategy]:
    return STRATEGY_REGISTRY
