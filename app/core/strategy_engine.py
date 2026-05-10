from app.core.strategies.ema_pullback import EMAPullbackStrategy
from app.core.strategies.breakout_atr import BreakoutAtrStrategy
from app.core.strategies.rsi_mean_reversion import RsiMeanReversionStrategy


class StrategyEngine:
    def __init__(self):
        self.strategies = {
            "ema_pullback": EMAPullbackStrategy(),
            "breakout_atr": BreakoutATRStrategy(),
            "rsi_mean_reversion": RSIMeanReversionStrategy(),
        }

    def get_strategy(self, name: str):
        return self.strategies.get(name)
