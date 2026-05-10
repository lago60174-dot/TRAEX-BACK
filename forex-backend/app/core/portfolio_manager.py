from datetime import datetime, timezone, timedelta
from typing import Optional
import structlog

from app.data.oanda_client import OandaClient
from app.data.database import get_supabase
from app.models.schemas import AccountInfo

logger = structlog.get_logger()


class PortfolioManager:
    def __init__(self):
        self.oanda = OandaClient()
        self.db = get_supabase()

    async def get_account_info(self) -> AccountInfo:
        account = await self.oanda.get_account()
        balance = float(account["balance"])
        equity = float(account["NAV"])
        currency = account.get("currency", "USD")

        daily_pnl = await self._period_pnl(hours=24)
        weekly_pnl = await self._period_pnl(days=7)
        monthly_pnl = await self._period_pnl(days=30)

        return AccountInfo(
            account_id=self.oanda.account_id,
            balance=round(balance, 2),
            equity=round(equity, 2),
            margin_used=round(float(account.get("marginUsed", 0)), 2),
            margin_available=round(float(account.get("marginAvailable", balance)), 2),
            open_trade_count=int(account.get("openTradeCount", 0)),
            currency=currency,
            daily_pnl=round(daily_pnl, 2),
            daily_pnl_percent=round((daily_pnl / balance * 100) if balance > 0 else 0, 3),
            weekly_pnl=round(weekly_pnl, 2),
            monthly_pnl=round(monthly_pnl, 2),
        )

    async def _period_pnl(self, hours: int = 0, days: int = 0) -> float:
        delta = timedelta(hours=hours, days=days)
        since = (datetime.now(timezone.utc) - delta).isoformat()
        result = (
            self.db.table("trades").select("pnl")
            .eq("status", "CLOSED").gte("closed_at", since).execute()
        )
        return sum(float(r["pnl"] or 0) for r in result.data) if result.data else 0.0

    async def get_annual_pnl(self) -> float:
        year_start = datetime.now(timezone.utc).replace(
            month=1, day=1, hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        result = (
            self.db.table("trades").select("pnl")
            .eq("status", "CLOSED").gte("closed_at", year_start).execute()
        )
        return sum(float(r["pnl"] or 0) for r in result.data) if result.data else 0.0

    async def snapshot_account(self, account_info: Optional[AccountInfo] = None) -> None:
        if account_info is None:
            account_info = await self.get_account_info()
        self.db.table("account_snapshots").insert({
            "balance": account_info.balance,
            "equity": account_info.equity,
            "daily_pnl": account_info.daily_pnl,
            "open_trades": account_info.open_trade_count,
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
        }).execute()

    async def get_performance_summary(self) -> dict:
        """Full performance stats for notification reports."""
        account = await self.get_account_info()
        annual_pnl = await self.get_annual_pnl()

        # Win rate
        result = self.db.table("trades").select("pnl").eq("status", "CLOSED").execute()
        trades = result.data or []
        total = len(trades)
        winners = sum(1 for t in trades if float(t.get("pnl") or 0) > 0)
        win_rate = round((winners / total * 100) if total > 0 else 0, 1)

        # Best/worst trade
        pnls = [float(t.get("pnl") or 0) for t in trades]
        best = max(pnls) if pnls else 0
        worst = min(pnls) if pnls else 0

        return {
            "balance": account.balance,
            "equity": account.equity,
            "currency": account.currency,
            "daily_pnl": account.daily_pnl,
            "daily_pnl_percent": account.daily_pnl_percent,
            "weekly_pnl": account.weekly_pnl,
            "monthly_pnl": account.monthly_pnl,
            "annual_pnl": round(annual_pnl, 2),
            "win_rate": win_rate,
            "total_trades": total,
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2),
        }
