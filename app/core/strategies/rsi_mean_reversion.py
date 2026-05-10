from typing import List, Tuple
import numpy as np

from app.core.strategies.base import BaseStrategy
from app.models.schemas import (
    Candle, TradingSignal, SignalType, StrategyRiskSettings, StrategyName
)


class RsiMeanReversionStrategy(BaseStrategy):
    """
    Strategy 2 — RSI Mean Reversion + EMA Trend Filter

    Logic:
        The market is in trend (EMA200 direction).
        RSI reaches an extreme (oversold in uptrend, overbought in downtrend).
        Price then reverses — we enter in the direction of the main trend.

    Rules:
        BUY:
            - Price > EMA200 (uptrend context)
            - RSI(14) drops below 35 (oversold in uptrend = pullback opportunity)
            - RSI crosses back above 35 (momentum reversal confirmed)
            - Entry candle is bullish

        SELL:
            - Price < EMA200 (downtrend context)
            - RSI(14) rises above 65 (overbought in downtrend = pullback opportunity)
            - RSI crosses back below 65 (momentum reversal confirmed)
            - Entry candle is bearish

    SL: swing low/high over N candles
    TP: SL distance × 1.5 RR (tighter than trend-following)

    Timeframe: H4 (recommended) — filters noise, captures quality setups
    Best pairs: EUR_USD, USD_CHF, AUD_USD
    """

    name = StrategyName.RSI_MEAN_REVERSION
    description = "RSI oversold/overbought reversals filtered by EMA200 trend direction"
    min_candles = 215  # 200 EMA + 14 RSI + buffer

    def __init__(
        self,
        rsi_period: int = 14,
        ema_trend: int = 200,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 65.0,
    ):
        self.rsi_period = rsi_period
        self.ema_trend = ema_trend
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def _ema(self, prices: np.ndarray, period: int) -> np.ndarray:
        ema = np.zeros(len(prices))
        ema[period - 1] = np.mean(prices[:period])
        k = 2.0 / (period + 1)
        for i in range(period, len(prices)):
            ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
        return ema

    def _rsi(self, closes: np.ndarray, period: int = 14) -> np.ndarray:
        """Wilder's RSI — standard implementation."""
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)

        rsi = np.full(len(closes), 50.0)

        # Initial average
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi[i + 1] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))

        return rsi

    def analyze(self, candles: List[Candle], symbol: str, swing_lookback: int = 10) -> Tuple[SignalType, dict]:
        if len(candles) < self.min_candles:
            return SignalType.NONE, {"reason": f"Need {self.min_candles} candles, got {len(candles)}"}

        closes = np.array([c.close for c in candles])
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])

        ema200 = self._ema(closes, self.ema_trend)
        rsi = self._rsi(closes, self.rsi_period)

        last = candles[-1]
        price = last.close
        ema_val = ema200[-1]
        rsi_now = rsi[-1]
        rsi_prev = rsi[-2]  # previous RSI for crossover detection

        uptrend = price > ema_val
        downtrend = price < ema_val
        bullish_candle = last.close > last.open
        bearish_candle = last.close < last.open

        # RSI crossover detection (the actual entry trigger)
        rsi_crossed_up = rsi_prev <= self.rsi_oversold and rsi_now > self.rsi_oversold
        rsi_crossed_down = rsi_prev >= self.rsi_overbought and rsi_now < self.rsi_overbought

        swing_h = self._swing_high(highs, swing_lookback)
        swing_l = self._swing_low(lows, swing_lookback)

        reasoning = {
            "ema200": round(ema_val, 6),
            "price": round(price, 6),
            "rsi_current": round(rsi_now, 2),
            "rsi_previous": round(rsi_prev, 2),
            "uptrend": uptrend,
            "downtrend": downtrend,
            "rsi_crossed_above_oversold": rsi_crossed_up,
            "rsi_crossed_below_overbought": rsi_crossed_down,
            "bullish_candle": bullish_candle,
            "bearish_candle": bearish_candle,
            "swing_high": round(swing_h, 6),
            "swing_low": round(swing_l, 6),
        }

        # BUY: uptrend + RSI just bounced from oversold zone + bullish confirmation
        if uptrend and rsi_crossed_up and bullish_candle:
            reasoning["signal_reason"] = (
                f"Uptrend (price > EMA200) + RSI crossed above {self.rsi_oversold} "
                f"({round(rsi_prev,1)}→{round(rsi_now,1)}) + bullish candle"
            )
            return SignalType.BUY, reasoning

        # SELL: downtrend + RSI just rejected from overbought zone + bearish confirmation
        if downtrend and rsi_crossed_down and bearish_candle:
            reasoning["signal_reason"] = (
                f"Downtrend (price < EMA200) + RSI crossed below {self.rsi_overbought} "
                f"({round(rsi_prev,1)}→{round(rsi_now,1)}) + bearish candle"
            )
            return SignalType.SELL, reasoning

        # No signal — explain why
        if uptrend and not rsi_crossed_up:
            reason = f"Uptrend but RSI not in oversold zone (RSI={round(rsi_now,1)}, need crossover above {self.rsi_oversold})"
        elif downtrend and not rsi_crossed_down:
            reason = f"Downtrend but RSI not in overbought zone (RSI={round(rsi_now,1)}, need crossover below {self.rsi_overbought})"
        else:
            reason = "No clear trend or RSI conditions not met"

        reasoning["signal_reason"] = reason
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
