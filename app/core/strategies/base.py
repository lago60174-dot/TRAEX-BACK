from abc import ABC, abstractmethod
from typing import List, Tuple
from app.models.schemas import Candle, TradingSignal, SignalType, StrategyRiskSettings, StrategyName


class BaseStrategy(ABC):
    """
    Abstract base class every strategy must implement.
    Enforces a single consistent interface across all strategies.
    """

    name: StrategyName
    description: str
    min_candles: int  # minimum candles required

    @abstractmethod
    def analyze(
        self,
        candles: List[Candle],
        symbol: str,
        swing_lookback: int = 10,
    ) -> Tuple[SignalType, dict]:
        """
        Analyze candles and return (signal, reasoning_dict).
        Must be pure and deterministic — no side effects.
        """
        ...

    @abstractmethod
    def build_signal(
        self,
        candles: List[Candle],
        symbol: str,
        account_balance: float,
        risk_settings: StrategyRiskSettings,
        pip_value_per_lot: float,
        timeframe: str,
    ) -> TradingSignal:
        """
        Full signal including entry, SL, TP, and position size.
        """
        ...

    def _pip_size(self, symbol: str) -> float:
        return 0.01 if "JPY" in symbol.upper() else 0.0001

    def _swing_high(self, highs, lookback: int) -> float:
        import numpy as np
        return float(np.max(highs[-lookback:]))

    def _swing_low(self, lows, lookback: int) -> float:
        import numpy as np
        return float(np.min(lows[-lookback:]))

    def _lot_size(
        self,
        balance: float,
        risk_percent: float,
        sl_pips: float,
        pip_value_per_lot: float,
    ) -> Tuple[float, float]:
        """Standard fixed-fractional position sizing."""
        risk_amount = balance * risk_percent
        if sl_pips <= 0 or pip_value_per_lot <= 0:
            return 0.01, risk_amount
        raw = risk_amount / (sl_pips * pip_value_per_lot)
        return round(max(0.01, round(raw, 2)), 2), round(risk_amount, 2)
