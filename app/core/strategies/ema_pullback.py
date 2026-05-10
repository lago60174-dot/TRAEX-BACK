from typing import List, Tuple
import numpy as np

from app.core.strategies.base import BaseStrategy
from app.models.schemas import (
    Candle, TradingSignal, SignalType, StrategyRiskSettings, StrategyName
)


class EmaPullbackStrategy(BaseStrategy):
    """
    Strategy 1 — EMA Trend + Pullback

    Rules:
        BUY:  EMA50 > EMA200  AND  price pulls back to EMA50  AND  bullish candle
        SELL: EMA50 < EMA200  AND  price pulls back to EMA50  AND  bearish candle

    SL: swing low/high over N candles
    TP: SL distance × RR ratio

    Timeframe: H1 (recommended)
    Best pairs: EUR_USD, GBP_USD, USD_JPY
    """

    name = StrategyName.EMA_PULLBACK
    description = "EMA 50/200 trend-following with pullback entry confirmation"
    min_candles = 210

    def __init__(self, ema_fast: int = 50, ema_slow: int = 200, pullback_tolerance: float = 0.002):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.pullback_tolerance = pullback_tolerance  # 0.2% tolerance

    def _ema(self, prices: np.ndarray, period: int) -> np.ndarray:
        ema = np.zeros(len(prices))
        ema[period - 1] = np.mean(prices[:period])
        k = 2.0 / (period + 1)
        for i in range(period, len(prices)):
            ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
        return ema

    def analyze(self, candles: List[Candle], symbol: str, swing_lookback: int = 10) -> Tuple[SignalType, dict]:
        if len(candles) < self.min_candles:
            return SignalType.NONE, {"reason": f"Need {self.min_candles} candles, got {len(candles)}"}

        closes = np.array([c.close for c in candles])
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])

        ema_fast = self._ema(closes, self.ema_fast)
        ema_slow = self._ema(closes, self.ema_slow)

        last = candles[-1]
        ef = ema_fast[-1]
        es = ema_slow[-1]
        price = last.close

        uptrend = ef > es
        downtrend = ef < es
        near_ema = abs(price - ef) / ef <= self.pullback_tolerance
        bullish = last.close > last.open
        bearish = last.close < last.open

        swing_h = self._swing_high(highs, swing_lookback)
        swing_l = self._swing_low(lows, swing_lookback)

        reasoning = {
            "ema_fast": round(ef, 6),
            "ema_slow": round(es, 6),
            "price": round(price, 6),
            "uptrend": uptrend,
            "downtrend": downtrend,
            "near_ema50": near_ema,
            "bullish_candle": bullish,
            "bearish_candle": bearish,
            "swing_high": round(swing_h, 6),
            "swing_low": round(swing_l, 6),
        }

        if uptrend and near_ema and bullish:
            reasoning["signal_reason"] = "EMA uptrend + pullback to EMA50 + bullish candle"
            return SignalType.BUY, reasoning

        if downtrend and near_ema and bearish:
            reasoning["signal_reason"] = "EMA downtrend + pullback to EMA50 + bearish candle"
            return SignalType.SELL, reasoning

        reasoning["signal_reason"] = (
            "No pullback" if (uptrend or downtrend) and not near_ema
            else "No trend" if not uptrend and not downtrend
            else "No candle confirmation"
        )
        return SignalType.NONE, reasoning

    def build_signal(
        self,
        candles: List[Candle],
        symbol: str,
        account_balance: float,
        risk_settings: StrategyRiskSettings,
        pip_value_per_lot: float,
        timeframe: str,
    ) -> TradingSignal:
        signal_type, reasoning = self.analyze(candles, symbol, risk_settings.swing_lookback)

        if signal_type == SignalType.NONE:
            return TradingSignal(
                signal=SignalType.NONE, symbol=symbol, timeframe=timeframe,
                strategy=self.name, reasoning=reasoning.get("signal_reason", "No signal"),
            )

        last = candles[-1]
        entry = last.close
        pip = self._pip_size(symbol)
        swing_h = reasoning["swing_high"]
        swing_l = reasoning["swing_low"]

        if signal_type == SignalType.BUY:
            sl = swing_l
            sl_dist = entry - sl
            tp = entry + sl_dist * risk_settings.rr_ratio
        else:
            sl = swing_h
            sl_dist = sl - entry
            tp = entry - sl_dist * risk_settings.rr_ratio

        sl_pips = round(sl_dist / pip, 1)
        lots, risk_amt = self._lot_size(account_balance, risk_settings.risk_percent, sl_pips, pip_value_per_lot)

        return TradingSignal(
            signal=signal_type, symbol=symbol, timeframe=timeframe, strategy=self.name,
            entry_price=round(entry, 6), stop_loss=round(sl, 6), take_profit=round(tp, 6),
            stop_loss_pips=sl_pips, lot_size=lots, risk_amount=risk_amt,
            reasoning=reasoning.get("signal_reason", ""),
        )
