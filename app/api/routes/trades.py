from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
import structlog

from app.core.execution_engine import ExecutionEngine, TradeBlockedError, TradeNotFoundError
from app.core.portfolio_manager import PortfolioManager
from app.models.schemas import (
    TradeRecord, TradeOpenRequest, TradeCloseRequest,
    TradingSignal, SignalType, TradeDirection
)
from app.services.auth import verify_token
from app.data.oanda_client import OandaClient
from app.core.risk_engine import RiskEngine
from app.data.database import get_supabase

router = APIRouter(prefix="/trades", tags=["Trades"])
logger = structlog.get_logger()


@router.get("/open", response_model=List[TradeRecord])
async def get_open_trades(_: dict = Depends(verify_token)):
    """Retourne tous les trades actuellement ouverts."""
    db = get_supabase()
    result = db.table("trades").select("*").eq("status", "OPEN").order("opened_at", desc=True).execute()
    return [_row_to_trade(r) for r in (result.data or [])]


@router.get("/history", response_model=List[TradeRecord])
async def get_trade_history(
    limit: int = 50,
    strategy: Optional[str] = None,
    _: dict = Depends(verify_token),
):
    """Historique des trades fermés, optionnellement filtré par stratégie."""
    db = get_supabase()
    q = db.table("trades").select("*").eq("status", "CLOSED").order("closed_at", desc=True).limit(limit)
    if strategy:
        q = q.eq("strategy", strategy.upper())
    result = q.execute()
    return [_row_to_trade(r) for r in (result.data or [])]


@router.get("/{trade_id}", response_model=TradeRecord)
async def get_trade(trade_id: str, _: dict = Depends(verify_token)):
    db = get_supabase()
    result = db.table("trades").select("*").eq("id", trade_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
    return _row_to_trade(result.data[0])


@router.post("/open", response_model=TradeRecord, status_code=201)
async def open_trade(request: TradeOpenRequest, _: dict = Depends(verify_token)):
    """
    Ouvre un trade manuellement avec validation complète par le RiskEngine.
    Envoie l'ordre sur OANDA et persiste en DB.
    """
    try:
        pm = PortfolioManager()
        account = await pm.get_account_info()

        # Build signal from manual request
        signal = TradingSignal(
            signal=SignalType(request.direction.value),
            symbol=request.symbol,
            timeframe=request.timeframe.value,
            strategy=request.strategy,
            entry_price=request.entry_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            lot_size=request.lot_size,
            reasoning="manual",
        )

        engine = ExecutionEngine()
        trade = await engine.open_trade_from_signal(signal, account.balance, account.currency)

        # Send notification
        from app.services.notifications import NotificationService
        from app.core.risk_engine import RiskEngine
        risk = RiskEngine()
        total_risk = await _get_total_risk_pct(account.balance, risk)
        await NotificationService().notify_trade_opened(
            {
                "id": trade.id, "symbol": trade.symbol, "direction": trade.direction.value,
                "entry_price": trade.entry_price, "stop_loss": trade.stop_loss,
                "take_profit": trade.take_profit, "lot_size": trade.lot_size,
                "risk_amount": trade.risk_amount, "strategy": trade.strategy.value if trade.strategy else "",
            },
            account.balance,
        )

        return trade

    except TradeBlockedError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("open_trade_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/close", response_model=TradeRecord)
async def close_trade(request: TradeCloseRequest, _: dict = Depends(verify_token)):
    """Ferme un trade ouvert par son ID. Envoie notification avec PnL."""
    try:
        engine = ExecutionEngine()
        trade = await engine.close_trade(request.trade_id, reason=request.reason or "manual")

        # Notification
        from app.services.notifications import NotificationService
        from app.core.risk_engine import RiskEngine
        from app.core.portfolio_manager import PortfolioManager
        account = await PortfolioManager().get_account_info()
        risk = RiskEngine()
        total_risk = await _get_total_risk_pct(account.balance, risk)
        daily_pnl = await risk.get_daily_pnl()

        await NotificationService().notify_trade_closed(
            {
                "id": trade.id, "symbol": trade.symbol, "direction": trade.direction.value,
                "lot_size": trade.lot_size, "pnl": trade.pnl, "pnl_pips": trade.pnl_pips,
                "close_reason": trade.close_reason, "strategy": trade.strategy.value if trade.strategy else "",
            },
            account.balance, daily_pnl, total_risk,
        )

        return trade

    except TradeNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error("close_trade_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats/summary")
async def get_trade_stats(_: dict = Depends(verify_token)):
    """Statistiques agrégées : win rate, PnL moyen, meilleure stratégie."""
    db = get_supabase()
    closed = db.table("trades").select("pnl, pnl_pips, strategy, direction, opened_at, closed_at") \
        .eq("status", "CLOSED").execute().data or []

    total = len(closed)
    if total == 0:
        return {"total_trades": 0, "win_rate": 0, "total_pnl": 0, "avg_pnl": 0,
                "avg_pips": 0, "best_strategy": None, "per_strategy": {}}

    wins = [t for t in closed if float(t.get("pnl") or 0) > 0]
    total_pnl = sum(float(t.get("pnl") or 0) for t in closed)
    total_pips = sum(float(t.get("pnl_pips") or 0) for t in closed)

    # Per strategy
    from app.models.schemas import StrategyName
    per_strategy = {}
    for strat in StrategyName:
        st = [t for t in closed if t.get("strategy") == strat.value]
        if not st:
            continue
        sw = [t for t in st if float(t.get("pnl") or 0) > 0]
        per_strategy[strat.value] = {
            "trades": len(st),
            "wins": len(sw),
            "win_rate": round(len(sw) / len(st) * 100, 1),
            "total_pnl": round(sum(float(t.get("pnl") or 0) for t in st), 2),
            "total_pips": round(sum(float(t.get("pnl_pips") or 0) for t in st), 1),
        }

    best = max(per_strategy, key=lambda k: per_strategy[k]["total_pnl"]) if per_strategy else None

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": total - len(wins),
        "win_rate": round(len(wins) / total * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / total, 2),
        "avg_pips": round(total_pips / total, 1),
        "best_strategy": best,
        "per_strategy": per_strategy,
    }


# ─── helpers ──────────────────────────────────────────────────────────────────

async def _get_total_risk_pct(balance: float, risk) -> float:
    if balance <= 0:
        return 0.0
    pnl = await risk.get_daily_pnl()
    rs = await risk.get_risk_settings()
    limit = balance * rs.max_portfolio_daily_loss
    return round(abs(pnl) / limit * 100, 1) if limit > 0 else 0.0


def _row_to_trade(r: dict) -> TradeRecord:
    from app.models.schemas import TradeDirection, TradeStatus, StrategyName
    from datetime import datetime
    return TradeRecord(
        id=r["id"], symbol=r["symbol"],
        direction=TradeDirection(r["direction"]),
        entry_price=float(r["entry_price"]),
        stop_loss=float(r["stop_loss"]),
        take_profit=float(r["take_profit"]),
        lot_size=float(r["lot_size"]),
        status=TradeStatus(r["status"]),
        strategy=StrategyName(r["strategy"]) if r.get("strategy") else None,
        pnl=float(r["pnl"]) if r.get("pnl") is not None else None,
        pnl_pips=float(r["pnl_pips"]) if r.get("pnl_pips") is not None else None,
        risk_amount=float(r["risk_amount"]) if r.get("risk_amount") is not None else None,
        oanda_trade_id=r.get("oanda_trade_id"),
        timeframe=r.get("timeframe"),
        opened_at=datetime.fromisoformat(r["opened_at"]) if isinstance(r["opened_at"], str) else r["opened_at"],
        closed_at=datetime.fromisoformat(r["closed_at"]) if r.get("closed_at") and isinstance(r["closed_at"], str) else r.get("closed_at"),
        close_reason=r.get("close_reason"),
    )
