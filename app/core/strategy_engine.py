from app.core.strategies.ema_pullback import EMAPullbackStrategy
from app.core.strategies.breakout_atr import BreakoutATRStrategy
from app.core.strategies.rsi_mean_reversion import RSIMeanReversionStrategy


class StrategyEngine:
    def __init__(self):
        self.strategies = {
            "ema_pullback": EMAPullbackStrategy(),
            "breakout_atr": BreakoutATRStrategy(),
            "rsi_mean_reversion": RSIMeanReversionStrategy(),
        }

    def get_strategy(self, name: str):
        return self.strategies.get(name)
