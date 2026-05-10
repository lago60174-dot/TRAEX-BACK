"""
Scheduler — tous les jobs background :
  - 30s   : monitor trades (sync OANDA ↔ DB)
  - 5min  : snapshot account
  - 1min  : vérification alertes risque
  - Daily : rapport journalier (00:05 UTC)
  - Fri   : rapports hebdo/mensuel/annuel (21:05 UTC, fermeture NY)
  - Cron  : alertes sessions forex
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timezone, timedelta
import structlog

from app.config import get_settings

logger = structlog.get_logger()
scheduler = AsyncIOScheduler(timezone="UTC")


# ─── MONITOR ──────────────────────────────────────────────────────────────────

async def _monitor_trades_job():
    try:
        from app.core.execution_engine import ExecutionEngine
        await ExecutionEngine().monitor_open_trades()
    except Exception as e:
        logger.error("monitor_trades_error", error=str(e))


async def _snapshot_account_job():
    try:
        from app.core.portfolio_manager import PortfolioManager
        await PortfolioManager().snapshot_account()
    except Exception as e:
        logger.error("snapshot_error", error=str(e))


async def _risk_alert_check_job():
    """Drawdown > 5%, marge faible, limite journalière à 75%."""
    try:
        from app.core.portfolio_manager import PortfolioManager
        from app.core.risk_engine import RiskEngine
        from app.services.notifications import NotificationService

        account = await PortfolioManager().get_account_info()
        notif = NotificationService()
        risk = RiskEngine()

        # Drawdown vs pic 30 jours
        ago30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        snap = risk.db.table("account_snapshots").select("balance") \
            .gte("snapshot_at", ago30).order("balance", desc=True).limit(1).execute()
        if snap.data:
            peak = float(snap.data[0]["balance"])
            dd = (peak - account.balance) / peak * 100 if peak > 0 else 0
            if dd >= 5.0:
                await notif.notify_drawdown_alert(dd, account.balance, peak)

        # Marge
        if account.margin_used > 0:
            ml = account.equity / account.margin_used * 100
            if ml < 200:
                await notif.notify_margin_warning(ml, account.margin_used, account.margin_available)

        # 75% limite journalière
        rs = await risk.get_risk_settings()
        limit = account.balance * rs.max_portfolio_daily_loss
        if account.daily_pnl < 0 and limit > 0:
            ratio = abs(account.daily_pnl) / limit
            if ratio >= 0.75:
                await notif.notify_risk_alert(
                    "Limite journalière proche",
                    f"75% de la limite utilisée",
                    abs(account.daily_pnl / account.balance * 100),
                    rs.max_portfolio_daily_loss * 100,
                )
    except Exception as e:
        logger.error("risk_alert_error", error=str(e))


# ─── STATS HELPER ─────────────────────────────────────────────────────────────

async def _period_stats(start_iso: str) -> dict:
    from app.core.risk_engine import RiskEngine
    from app.models.schemas import StrategyName
    db = RiskEngine().db
    rows = db.table("trades").select("pnl, strategy").eq("status", "CLOSED") \
        .gte("closed_at", start_iso).execute().data or []
    total = len(rows)
    wins = sum(1 for r in rows if float(r.get("pnl") or 0) > 0)
    pnl = sum(float(r.get("pnl") or 0) for r in rows)
    breakdown = {s.value: round(sum(float(r.get("pnl") or 0) for r in rows if r.get("strategy") == s.value), 2) for s in StrategyName}
    best = max(breakdown, key=breakdown.get) if breakdown else "N/A"
    return {"total": total, "win_rate": wins / total * 100 if total else 0,
            "pnl": pnl, "breakdown": breakdown, "best": best}


# ─── REPORTS ──────────────────────────────────────────────────────────────────

async def _daily_report_job():
    try:
        from app.core.portfolio_manager import PortfolioManager
        from app.services.notifications import NotificationService
        from app.core.risk_engine import RiskEngine
        account = await PortfolioManager().get_account_info()
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        stats = await _period_stats(today)
        sigs = RiskEngine().db.table("strategy_signals").select("strategy") \
            .gte("generated_at", today).eq("executed", True).execute().data or []
        active = list({r["strategy"] for r in sigs if r.get("strategy")}) or ["Aucune"]
        await NotificationService().notify_daily_report(
            account.balance, account.daily_pnl, stats["total"],
            stats["win_rate"], abs(account.daily_pnl / account.balance * 100) if account.balance else 0, active)
        logger.info("daily_report_sent", pnl=account.daily_pnl)
    except Exception as e:
        logger.error("daily_report_error", error=str(e))


async def _weekly_report_job():
    try:
        from app.core.portfolio_manager import PortfolioManager
        from app.services.notifications import NotificationService
        account = await PortfolioManager().get_account_info()
        start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        stats = await _period_stats(start)
        await NotificationService().notify_weekly_report(
            account.balance, stats["pnl"], stats["total"], stats["win_rate"],
            stats["best"], abs(stats["pnl"] / account.balance * 100) if account.balance else 0)
        logger.info("weekly_report_sent", pnl=stats["pnl"])
    except Exception as e:
        logger.error("weekly_report_error", error=str(e))


async def _monthly_report_job():
    """Seulement le dernier vendredi du mois."""
    try:
        now = datetime.now(timezone.utc)
        if (now + timedelta(days=7)).month == now.month:
            return
        from app.core.portfolio_manager import PortfolioManager
        from app.services.notifications import NotificationService
        account = await PortfolioManager().get_account_info()
        start = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
        stats = await _period_stats(start)
        await NotificationService().notify_monthly_report(
            account.balance, stats["pnl"], stats["total"], stats["win_rate"],
            2.0, abs(stats["pnl"] / account.balance * 100) if account.balance else 0, stats["breakdown"])
        logger.info("monthly_report_sent", pnl=stats["pnl"])
    except Exception as e:
        logger.error("monthly_report_error", error=str(e))


async def _annual_report_job():
    """Seulement le dernier vendredi de décembre."""
    try:
        now = datetime.now(timezone.utc)
        if now.month != 12 or (now + timedelta(days=7)).month == 12:
            return
        from app.core.portfolio_manager import PortfolioManager
        from app.services.notifications import NotificationService
        from app.core.risk_engine import RiskEngine
        account = await PortfolioManager().get_account_info()
        year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0).isoformat()
        stats = await _period_stats(year_start)
        db = RiskEngine().db
        first = db.table("account_snapshots").select("balance").gte("snapshot_at", year_start) \
            .order("snapshot_at").limit(1).execute().data
        starting = float(first[0]["balance"]) if first else account.balance
        snaps = db.table("account_snapshots").select("balance").gte("snapshot_at", year_start) \
            .order("snapshot_at").execute().data or []
        balances = [float(r["balance"]) for r in snaps]
        max_dd = 0.0
        if balances:
            peak = balances[0]
            for b in balances:
                peak = max(peak, b)
                dd = (peak - b) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)
        await NotificationService().notify_annual_report(
            account.balance, stats["pnl"], starting, stats["total"],
            stats["win_rate"], max_dd, abs(stats["pnl"] / starting * 100) if starting else 0)
        logger.info("annual_report_sent", pnl=stats["pnl"])
    except Exception as e:
        logger.error("annual_report_error", error=str(e))


async def _session_alert(session: str, action: str, pairs: list):
    try:
        from app.services.notifications import NotificationService
        await NotificationService().notify_market_session(session, action, pairs)
    except Exception as e:
        logger.error("session_alert_error", session=session, error=str(e))


# ─── STARTUP ──────────────────────────────────────────────────────────────────

def start_scheduler():
    s = get_settings()
    scheduler.add_job(_monitor_trades_job,  IntervalTrigger(seconds=s.monitor_interval_seconds), id="monitor_trades",  max_instances=1, replace_existing=True)
    scheduler.add_job(_snapshot_account_job, IntervalTrigger(minutes=5),  id="snapshot_account", max_instances=1, replace_existing=True)
    scheduler.add_job(_risk_alert_check_job, IntervalTrigger(minutes=1),  id="risk_alerts",      max_instances=1, replace_existing=True)
    scheduler.add_job(_daily_report_job,  CronTrigger(hour=0,  minute=5),                       id="daily_report",  replace_existing=True)
    scheduler.add_job(_weekly_report_job, CronTrigger(day_of_week="fri", hour=21, minute=5),    id="weekly_report", replace_existing=True)
    scheduler.add_job(_monthly_report_job,CronTrigger(day_of_week="fri", hour=21, minute=10),   id="monthly_report",replace_existing=True)
    scheduler.add_job(_annual_report_job, CronTrigger(day_of_week="fri", hour=21, minute=15),   id="annual_report", replace_existing=True)
    # Sessions forex (UTC)
    scheduler.add_job(lambda: _session_alert("Sydney",   "open",  ["AUD_USD","NZD_USD"]), CronTrigger(hour=22, minute=0), id="sydney_open",    replace_existing=True)
    scheduler.add_job(lambda: _session_alert("Tokyo",    "open",  ["USD_JPY","EUR_JPY"]), CronTrigger(hour=0,  minute=0), id="tokyo_open",     replace_existing=True)
    scheduler.add_job(lambda: _session_alert("London",   "open",  ["GBP_USD","EUR_USD"]), CronTrigger(hour=8,  minute=0), id="london_open",    replace_existing=True)
    scheduler.add_job(lambda: _session_alert("New York", "open",  ["USD_CAD","GBP_JPY"]), CronTrigger(hour=13, minute=0), id="newyork_open",   replace_existing=True)
    scheduler.add_job(lambda: _session_alert("New York", "close", ["EUR_USD","USD_JPY"]), CronTrigger(hour=21, minute=0), id="newyork_close",  replace_existing=True)
    scheduler.start()
    logger.info("scheduler_started", monitor_interval=s.monitor_interval_seconds)


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
