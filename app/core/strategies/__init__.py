from typing import Dict
from app.core.strategies.base import BaseStrategy
from app.models.schemas import StrategyName

# Import lazy (évite les circular imports)
from app.core.strategies.ema_pullback import EMAPullbackStrategy
from app.core.strategies.rsi_mean_reversion import RsiMeanReversionStrategy
from app.core.strategies.breakout_atr import BreakoutAtrStrategy


STRATEGY_REGISTRY: Dict[StrategyName, BaseStrategy] = {
    StrategyName.EMA_PULLBACK: EMAPullbackStrategy(),
    StrategyName.RSI_MEAN_REVERSION: RsiMeanReversionStrategy(),
    StrategyName.BREAKOUT_ATR: BreakoutAtrStrategy(),
}


def get_strategy(name: StrategyName) -> BaseStrategy:
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy: {name}")
    return STRATEGY_REGISTRY[name]


def get_all_strategies() -> Dict[StrategyName, BaseStrategy]:
    return STRATEGY_REGISTRY
