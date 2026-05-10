from datetime import datetime, timezone
from typing import Tuple, Optional, Dict
import structlog

from app.config import get_settings
from app.models.schemas import RiskSettings, StrategyRiskSettings, StrategyName
from app.data.database import get_supabase

logger = structlog.get_logger()

DEFAULT_STRATEGY_RISK = {
    "EMA_PULLBACK":       {"risk_percent": 0.01, "max_daily_loss": 0.03, "swing_lookback": 10, "rr_ratio": 2.0},
    "RSI_MEAN_REVERSION": {"risk_percent": 0.01, "max_daily_loss": 0.03, "swing_lookback": 10, "rr_ratio": 1.5},
    "BREAKOUT_ATR":       {"risk_percent": 0.01, "max_daily_loss": 0.03, "swing_lookback": 14, "rr_ratio": 2.5},
}


class RiskEngine:
    """
    Master risk layer.

    Portfolio rules:
    - Max 2 strategies active simultaneously
    - Each strategy: 1% risk/trade, 3% max daily loss cap
    - Portfolio total daily loss cap: 6% (2 × 3%)
    - Max 2 open trades globally (1 per strategy)
    """

    def __init__(self):
        self.settings = get_settings()
        self.db = get_supabase()

    async def get_risk_settings(self) -> RiskSettings:
        result = self.db.table("risk_settings").select("*").limit(1).execute()
        if not result.data:
            return RiskSettings()
        row = result.data[0]
        return RiskSettings(
            max_concurrent_strategies=int(row.get("max_concurrent_strategies", 2)),
            max_open_trades=int(row.get("max_open_trades", 2)),
            max_portfolio_daily_loss=float(row.get("max_portfolio_daily_loss", 0.06)),
            strategy_risk=row.get("strategy_risk", DEFAULT_STRATEGY_RISK),
        )

    def get_strategy_risk(self, risk_settings: RiskSettings, strategy: StrategyName) -> StrategyRiskSettings:
        params = risk_settings.strategy_risk.get(
            strategy.value, DEFAULT_STRATEGY_RISK.get(strategy.value, {})
        )
        return StrategyRiskSettings(
            risk_percent=params.get("risk_percent", 0.01),
            max_daily_loss=params.get("max_daily_loss", 0.03),
            swing_lookback=params.get("swing_lookback", 10),
            rr_ratio=params.get("rr_ratio", 2.0),
        )

    async def update_risk_settings(self, updates: dict) -> RiskSettings:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = self.db.table("risk_settings").select("id").limit(1).execute()
        if result.data:
            self.db.table("risk_settings").update(updates).eq("id", result.data[0]["id"]).execute()
        else:
            self.db.table("risk_settings").insert(updates).execute()
        return await self.get_risk_settings()

    async def get_daily_pnl(self, strategy: Optional[StrategyName] = None) -> float:
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        q = self.db.table("trades").select("pnl").eq("status", "CLOSED").gte("closed_at", today_start)
        if strategy:
            q = q.eq("strategy", strategy.value)
        result = q.execute()
        return sum(float(r["pnl"] or 0) for r in result.data) if result.data else 0.0

    async def get_period_pnl(self, start_iso: str) -> float:
        result = (
            self.db.table("trades").select("pnl")
            .eq("status", "CLOSED").gte("closed_at", start_iso).execute()
        )
        return sum(float(r["pnl"] or 0) for r in result.data) if result.data else 0.0

    async def get_open_trade_count(self, strategy: Optional[StrategyName] = None) -> int:
        q = self.db.table("trades").select("id").eq("status", "OPEN")
        if strategy:
            q = q.eq("strategy", strategy.value)
        result = q.execute()
        return len(result.data) if result.data else 0

    async def get_active_strategy_count(self) -> int:
        result = self.db.table("trades").select("strategy").eq("status", "OPEN").execute()
        if not result.data:
            return 0
        return len({r["strategy"] for r in result.data if r["strategy"]})

    async def can_open_trade(
        self,
        account_balance: float,
        strategy: StrategyName,
        risk_settings: Optional[RiskSettings] = None,
    ) -> Tuple[bool, str]:
        """
        Master gate. Checks in order:
        1. Portfolio max open trades
        2. Strategy already has an open trade (1 per strategy max)
        3. Max concurrent strategies
        4. Portfolio daily loss limit
        5. Strategy daily loss limit
        """
        if risk_settings is None:
            risk_settings = await self.get_risk_settings()

        strat_risk = self.get_strategy_risk(risk_settings, strategy)

        total_open = await self.get_open_trade_count()
        if total_open >= risk_settings.max_open_trades:
            return False, f"Portfolio: max open trades reached ({total_open}/{risk_settings.max_open_trades})"

        strat_open = await self.get_open_trade_count(strategy)
        if strat_open >= 1:
            return False, f"{strategy.value}: already has an open trade"

        active_strats = await self.get_active_strategy_count()
        if active_strats >= risk_settings.max_concurrent_strategies:
            return False, f"Max concurrent strategies reached ({active_strats}/{risk_settings.max_concurrent_strategies})"

        portfolio_pnl = await self.get_daily_pnl()
        portfolio_limit = account_balance * risk_settings.max_portfolio_daily_loss
        if portfolio_pnl <= -portfolio_limit:
            return False, f"Portfolio daily loss limit hit: {portfolio_pnl:.2f} / -{portfolio_limit:.2f}"

        strat_pnl = await self.get_daily_pnl(strategy)
        strat_limit = account_balance * strat_risk.max_daily_loss
        if strat_pnl <= -strat_limit:
            return False, f"{strategy.value} daily loss limit hit: {strat_pnl:.2f} / -{strat_limit:.2f}"

        logger.info("risk_gate_passed", strategy=strategy.value, open=total_open, pnl=portfolio_pnl)
        return True, "OK"

    def calculate_position_size(
        self,
        account_balance: float,
        stop_loss_pips: float,
        pip_value_per_lot: float,
        risk_percent: float,
    ) -> Tuple[float, float]:
        if stop_loss_pips <= 0 or pip_value_per_lot <= 0 or account_balance <= 0:
            raise ValueError("Invalid position sizing inputs")
        risk_amount = account_balance * risk_percent
        raw = risk_amount / (stop_loss_pips * pip_value_per_lot)
        lot_size = round(max(0.01, round(raw, 2)), 2)
        return lot_size, round(risk_amount, 2)

    def calculate_stop_loss_pips(self, entry: float, sl: float, symbol: str) -> float:
        pip = 0.01 if "JPY" in symbol.upper() else 0.0001
        return round(abs(entry - sl) / pip, 1)

    def validate_trade_parameters(self, direction: str, entry: float, sl: float, tp: float) -> Tuple[bool, str]:
        if direction == "BUY":
            if sl >= entry:
                return False, "BUY: stop_loss must be below entry"
            if tp <= entry:
                return False, "BUY: take_profit must be above entry"
        elif direction == "SELL":
            if sl <= entry:
                return False, "SELL: stop_loss must be above entry"
            if tp >= entry:
                return False, "SELL: take_profit must be below entry"
        else:
            return False, f"Unknown direction: {direction}"
        return True, "OK"

    async def get_risk_status(self, account_balance: float) -> dict:
        risk_settings = await self.get_risk_settings()
        portfolio_pnl = await self.get_daily_pnl()
        portfolio_limit = account_balance * risk_settings.max_portfolio_daily_loss
        open_trades = await self.get_open_trade_count()
        active_strats = await self.get_active_strategy_count()

        per_strategy = {}
        for strat in StrategyName:
            strat_risk = self.get_strategy_risk(risk_settings, strat)
            strat_pnl = await self.get_daily_pnl(strat)
            strat_limit = account_balance * strat_risk.max_daily_loss
            q = self.db.table("trades").select("id").eq("strategy", strat.value).eq("status", "OPEN").execute()
            per_strategy[strat.value] = {
                "daily_pnl": round(strat_pnl, 2),
                "daily_loss_limit": round(strat_limit, 2),
                "daily_loss_remaining": round(max(0, strat_limit + strat_pnl), 2),
                "open_trades": len(q.data) if q.data else 0,
                "risk_percent": strat_risk.risk_percent,
                "max_daily_loss_pct": strat_risk.max_daily_loss,
            }

        return {
            "trading_allowed": portfolio_pnl > -portfolio_limit and open_trades < risk_settings.max_open_trades,
            "portfolio_daily_loss": round(portfolio_pnl, 2),
            "portfolio_daily_loss_limit": round(portfolio_limit, 2),
            "portfolio_daily_loss_remaining": round(max(0, portfolio_limit + portfolio_pnl), 2),
            "active_strategies": active_strats,
            "max_concurrent_strategies": risk_settings.max_concurrent_strategies,
            "open_trades": open_trades,
            "max_open_trades": risk_settings.max_open_trades,
            "per_strategy": per_strategy,
        }
