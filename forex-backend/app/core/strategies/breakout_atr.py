from typing import List, Tuple
import numpy as np

from app.core.strategies.base import BaseStrategy
from app.models.schemas import (
    Candle, TradingSignal, SignalType, StrategyRiskSettings, StrategyName
)


class BreakoutAtrStrategy(BaseStrategy):
    """
    Strategy 3 — ATR Volatility Breakout + Momentum

    Logic:
        The market consolidates (low ATR range), then breaks out with conviction.
        We enter on the breakout candle when momentum (ADX proxy) confirms direction.
        ATR is used both for breakout detection AND for stop-loss sizing.

    Rules:
        BUY:
            - Price breaks above the highest high of the last N candles (excluding last)
            - The breakout candle range > ATR × multiplier (strong move, not noise)
            - Price > EMA50 (directional filter — trade with macro direction)
            - Entry candle closes near its high (strong close = bullish momentum)

        SELL:
            - Price breaks below the lowest low of the last N candles (excluding last)
            - The breakout candle range > ATR × multiplier
            - Price < EMA50
            - Entry candle closes near its low

    SL: 1.5 × ATR below/above entry (volatility-based stop — adapts to market conditions)
    TP: 2.5 × ATR (RR ≈ 1:1.67, compensated by high win-rate on real breakouts)

    Note: This strategy uses ATR for both signal detection AND stop placement,
    which means it adapts to current volatility. During high-volatility sessions
    (London open, NY open), ATR is larger so SL/TP are wider — this is correct behavior.

    Timeframe: H4 (recommended) — minimizes false breakouts
    Best pairs: GBP_JPY, USD_CAD, GBP_USD (volatile, trending pairs)
    """

    name = StrategyName.BREAKOUT_ATR
    description = "ATR volatility breakout with momentum confirmation and adaptive stops"
    min_candles = 60  # 14 ATR + 50 EMA + buffer

    def __init__(
        self,
        atr_period: int = 14,
        ema_period: int = 50,
        breakout_lookback: int = 20,  # candles to define the range
        atr_breakout_multiplier: float = 1.2,  # breakout candle must be > 1.2x ATR
        atr_sl_multiplier: float = 1.5,        # SL = 1.5 × ATR
        atr_tp_multiplier: float = 3.5,        # TP = 3.5 × ATR → ~2.33 RR
        close_strength_threshold: float = 0.65, # candle must close in top/bottom 35%
    ):
        self.atr_period = atr_period
        self.ema_period = ema_period
        self.breakout_lookback = breakout_lookback
        self.atr_breakout_multiplier = atr_breakout_multiplier
        self.atr_sl_multiplier = atr_sl_multiplier
        self.atr_tp_multiplier = atr_tp_multiplier
        self.close_strength = close_strength_threshold

    def _ema(self, prices: np.ndarray, period: int) -> np.ndarray:
        ema = np.zeros(len(prices))
        ema[period - 1] = np.mean(prices[:period])
        k = 2.0 / (period + 1)
        for i in range(period, len(prices)):
            ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
        return ema

    def _atr(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
        """Average True Range — Wilder smoothing."""
        n = len(closes)
        tr = np.zeros(n)

        for i in range(1, n):
            hl = highs[i] - lows[i]
            hc = abs(highs[i] - closes[i - 1])
            lc = abs(lows[i] - closes[i - 1])
            tr[i] = max(hl, hc, lc)

        atr = np.zeros(n)
        atr[period] = np.mean(tr[1:period + 1])
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        return atr

    def analyze(self, candles: List[Candle], symbol: str, swing_lookback: int = 14) -> Tuple[SignalType, dict]:
        if len(candles) < self.min_candles:
            return SignalType.NONE, {"reason": f"Need {self.min_candles} candles, got {len(candles)}"}

        closes = np.array([c.close for c in candles])
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])
        opens = np.array([c.open for c in candles])

        ema50 = self._ema(closes, self.ema_period)
        atr = self._atr(highs, lows, closes, self.atr_period)

        last = candles[-1]
        current_atr = atr[-1]
        ema_val = ema50[-1]

        # Range defined by N candles BEFORE the current one (excludes last)
        lookback_highs = highs[-(self.breakout_lookback + 1):-1]
        lookback_lows = lows[-(self.breakout_lookback + 1):-1]
        range_high = float(np.max(lookback_highs))
        range_low = float(np.min(lookback_lows))

        # Current candle metrics
        candle_range = last.high - last.low
        price = last.close

        # Breakout conditions
        broke_above = last.close > range_high  # closed above range
        broke_below = last.close < range_low   # closed below range

        # Strong candle: range > ATR × multiplier
        strong_candle = candle_range > (current_atr * self.atr_breakout_multiplier)

        # Close strength: for BUY, close must be in top portion of candle; for SELL, bottom
        if candle_range > 0:
            close_pos = (last.close - last.low) / candle_range  # 0 = closed at low, 1 = at high
        else:
            close_pos = 0.5

        strong_bull_close = close_pos >= self.close_strength
        strong_bear_close = close_pos <= (1 - self.close_strength)

        # Directional filter
        above_ema = price > ema_val
        below_ema = price < ema_val

        reasoning = {
            "atr": round(current_atr, 6),
            "ema50": round(ema_val, 6),
            "range_high": round(range_high, 6),
            "range_low": round(range_low, 6),
            "candle_range": round(candle_range, 6),
            "close_position_pct": round(close_pos * 100, 1),
            "strong_candle": strong_candle,
            "broke_above": broke_above,
            "broke_below": broke_below,
            "above_ema50": above_ema,
            "below_ema50": below_ema,
        }

        # BUY signal
        if broke_above and strong_candle and above_ema and strong_bull_close:
            reasoning["signal_reason"] = (
                f"Bullish breakout above {round(range_high, 5)} | "
                f"Candle range {round(candle_range/current_atr, 2)}x ATR | "
                f"Close strength {round(close_pos*100,1)}% | Price above EMA50"
            )
            reasoning["swing_high"] = self._swing_high(highs, swing_lookback)
            reasoning["swing_low"] = self._swing_low(lows, swing_lookback)
            return SignalType.BUY, reasoning

        # SELL signal
        if broke_below and strong_candle and below_ema and strong_bear_close:
            reasoning["signal_reason"] = (
                f"Bearish breakout below {round(range_low, 5)} | "
                f"Candle range {round(candle_range/current_atr, 2)}x ATR | "
                f"Close strength {round((1-close_pos)*100,1)}% (bear) | Price below EMA50"
            )
            reasoning["swing_high"] = self._swing_high(highs, swing_lookback)
            reasoning["swing_low"] = self._swing_low(lows, swing_lookback)
            return SignalType.SELL, reasoning

        # Determine specific failure reason for transparency
        if not strong_candle:
            reason = f"No strong candle: range {round(candle_range,5)} < ATR×{self.atr_breakout_multiplier} ({round(current_atr*self.atr_breakout_multiplier,5)})"
        elif not broke_above and not broke_below:
            reason = f"No breakout: price {round(price,5)} within range [{round(range_low,5)}, {round(range_high,5)}]"
        elif broke_above and not above_ema:
            reason = "Broke above range but price below EMA50 — no directional conviction"
        elif broke_below and not below_ema:
            reason = "Broke below range but price above EMA50 — no directional conviction"
        elif not strong_bull_close and not strong_bear_close:
            reason = f"Candle close not strong enough: position {round(close_pos*100,1)}%"
        else:
            reason = "Conditions partially met but not all confirmed"

        reasoning["signal_reason"] = reason
        reasoning["swing_high"] = self._swing_high(highs, swing_lookback)
        reasoning["swing_low"] = self._swing_low(lows, swing_lookback)
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
        # Re-compute ATR for SL/TP placement
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])
        closes = np.array([c.close for c in candles])
        atr_arr = self._atr(highs, lows, closes, self.atr_period)
        current_atr = atr_arr[-1]

        signal_type, reasoning = self.analyze(candles, symbol, risk_settings.swing_lookback)

        if signal_type == SignalType.NONE:
            return TradingSignal(
                signal=SignalType.NONE, symbol=symbol, timeframe=timeframe,
                strategy=self.name, reasoning=reasoning.get("signal_reason", "No signal"),
            )

        last = candles[-1]
        entry = last.close
        pip = self._pip_size(symbol)

        # ATR-based SL/TP
        if signal_type == SignalType.BUY:
            sl = entry - (current_atr * self.atr_sl_multiplier)
            tp = entry + (current_atr * self.atr_tp_multiplier)
        else:
            sl = entry + (current_atr * self.atr_sl_multiplier)
            tp = entry - (current_atr * self.atr_tp_multiplier)

        sl_dist = abs(entry - sl)
        sl_pips = round(sl_dist / pip, 1)
        lots, risk_amt = self._lot_size(account_balance, risk_settings.risk_percent, sl_pips, pip_value_per_lot)

        return TradingSignal(
            signal=signal_type, symbol=symbol, timeframe=timeframe, strategy=self.name,
            entry_price=round(entry, 6), stop_loss=round(sl, 6), take_profit=round(tp, 6),
            stop_loss_pips=sl_pips, lot_size=lots, risk_amount=risk_amt,
            reasoning=reasoning.get("signal_reason", ""),
        )
