from pydantic import BaseModel, Field, validator
from typing import Optional, List
from enum import Enum
from datetime import datetime


# ─────────────────────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────────────────────

class TradeDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class Timeframe(str, Enum):
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D = "D"


class StrategyName(str, Enum):
    EMA_PULLBACK = "EMA_PULLBACK"
    RSI_MEAN_REVERSION = "RSI_MEAN_REVERSION"
    BREAKOUT_ATR = "BREAKOUT_ATR"


# ─────────────────────────────────────────────────────────────────────────────
# MARKET DATA
# ─────────────────────────────────────────────────────────────────────────────

class Candle(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None


class MarketDataRequest(BaseModel):
    symbol: str = Field(..., example="EUR_USD")
    timeframe: Timeframe = Field(Timeframe.H1)
    count: int = Field(250, ge=50, le=500)


# ─────────────────────────────────────────────────────────────────────────────
# RISK SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

class StrategyRiskSettings(BaseModel):
    risk_percent: float = Field(0.01, ge=0.001, le=0.05)
    max_daily_loss: float = Field(0.03, ge=0.005, le=0.10)
    swing_lookback: int = Field(10, ge=5, le=50)
    rr_ratio: float = Field(2.0, ge=1.0, le=5.0)


class RiskSettings(BaseModel):
    max_concurrent_strategies: int = Field(2, ge=1, le=3)
    max_open_trades: int = Field(2, ge=1, le=6)
    max_portfolio_daily_loss: float = Field(0.06, ge=0.01, le=0.20)
    strategy_risk: dict = Field(default_factory=lambda: {
        "EMA_PULLBACK": {"risk_percent": 0.01, "max_daily_loss": 0.03, "swing_lookback": 10, "rr_ratio": 2.0},
        "RSI_MEAN_REVERSION": {"risk_percent": 0.01, "max_daily_loss": 0.03, "swing_lookback": 10, "rr_ratio": 1.5},
        "BREAKOUT_ATR": {"risk_percent": 0.01, "max_daily_loss": 0.03, "swing_lookback": 14, "rr_ratio": 2.5},
    })


class RiskSettingsUpdate(BaseModel):
    max_concurrent_strategies: Optional[int] = Field(None, ge=1, le=3)
    max_open_trades: Optional[int] = Field(None, ge=1, le=6)
    max_portfolio_daily_loss: Optional[float] = Field(None, ge=0.01, le=0.20)


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL
# ─────────────────────────────────────────────────────────────────────────────

class TradingSignal(BaseModel):
    signal: SignalType
    symbol: str
    timeframe: str
    strategy: StrategyName = StrategyName.EMA_PULLBACK
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    stop_loss_pips: Optional[float] = None
    lot_size: Optional[float] = None
    risk_amount: Optional[float] = None
    reasoning: str = ""
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class StrategyRunRequest(BaseModel):
    symbol: str = Field(..., example="EUR_USD")
    timeframe: Timeframe = Field(Timeframe.H1)
    strategy: StrategyName = Field(StrategyName.EMA_PULLBACK)
    auto_execute: bool = Field(False)


class StrategyStatus(BaseModel):
    name: StrategyName
    enabled: bool
    description: str
    timeframe: str
    symbol: str
    trades_today: int
    daily_pnl: float
    daily_pnl_percent: float
    daily_loss_remaining: float


# ─────────────────────────────────────────────────────────────────────────────
# TRADES
# ─────────────────────────────────────────────────────────────────────────────

class TradeOpenRequest(BaseModel):
    symbol: str = Field(..., example="EUR_USD")
    direction: TradeDirection
    entry_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    lot_size: float = Field(..., gt=0, le=100)
    strategy: StrategyName = StrategyName.EMA_PULLBACK
    timeframe: Timeframe = Field(Timeframe.H1)

    @validator("stop_loss")
    def sl_valid(cls, v, values):
        if "direction" in values and "entry_price" in values:
            if values["direction"] == TradeDirection.BUY and v >= values["entry_price"]:
                raise ValueError("BUY: SL must be below entry")
            if values["direction"] == TradeDirection.SELL and v <= values["entry_price"]:
                raise ValueError("SELL: SL must be above entry")
        return v

    @validator("take_profit")
    def tp_valid(cls, v, values):
        if "direction" in values and "entry_price" in values:
            if values["direction"] == TradeDirection.BUY and v <= values["entry_price"]:
                raise ValueError("BUY: TP must be above entry")
            if values["direction"] == TradeDirection.SELL and v >= values["entry_price"]:
                raise ValueError("SELL: TP must be below entry")
        return v


class TradeCloseRequest(BaseModel):
    trade_id: str
    reason: Optional[str] = "manual"


class TradeRecord(BaseModel):
    id: str
    symbol: str
    direction: TradeDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    status: TradeStatus
    strategy: Optional[StrategyName] = None
    pnl: Optional[float] = None
    pnl_pips: Optional[float] = None
    risk_amount: Optional[float] = None
    oanda_trade_id: Optional[str] = None
    timeframe: Optional[str] = None
    opened_at: datetime
    closed_at: Optional[datetime] = None
    close_reason: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNT
# ─────────────────────────────────────────────────────────────────────────────

class AccountInfo(BaseModel):
    account_id: str
    balance: float
    equity: float
    margin_used: float
    margin_available: float
    open_trade_count: int
    currency: str
    daily_pnl: float
    daily_pnl_percent: float
    weekly_pnl: Optional[float] = None
    monthly_pnl: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# RISK STATUS
# ─────────────────────────────────────────────────────────────────────────────

class RiskStatus(BaseModel):
    trading_allowed: bool
    reason: Optional[str] = None
    portfolio_daily_loss: float
    portfolio_daily_loss_limit: float
    portfolio_daily_loss_remaining: float
    active_strategies: int
    max_concurrent_strategies: int
    open_trades: int
    max_open_trades: int
    per_strategy: dict


# ─────────────────────────────────────────────────────────────────────────────
# NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

class NotificationType(str, Enum):
    TRADE_OPENED = "TRADE_OPENED"
    TRADE_CLOSED = "TRADE_CLOSED"
    DAILY_REPORT = "DAILY_REPORT"
    WEEKLY_REPORT = "WEEKLY_REPORT"
    MONTHLY_REPORT = "MONTHLY_REPORT"
    ANNUAL_REPORT = "ANNUAL_REPORT"
    RISK_ALERT = "RISK_ALERT"
    DRAWDOWN_ALERT = "DRAWDOWN_ALERT"
    STRATEGY_DISABLED = "STRATEGY_DISABLED"
    STREAK_ALERT = "STREAK_ALERT"
    MARKET_OPEN = "MARKET_OPEN"
    MARKET_CLOSE = "MARKET_CLOSE"


class PushSubscription(BaseModel):
    endpoint: str
    keys: dict


# ─────────────────────────────────────────────────────────────────────────────
# API RESPONSES
# ─────────────────────────────────────────────────────────────────────────────

class SuccessResponse(BaseModel):
    success: bool = True
    message: str
    data: Optional[dict] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None
