from datetime import datetime, timezone
from typing import Optional
import uuid
import structlog

from app.data.oanda_client import OandaClient, OandaOrderError
from app.data.database import get_supabase
from app.models.schemas import TradingSignal, TradeRecord, TradeStatus, TradeDirection, SignalType, StrategyName
from app.core.risk_engine import RiskEngine

logger = structlog.get_logger()


class ExecutionEngine:
    def __init__(self):
        self.oanda = OandaClient()
        self.db = get_supabase()
        self.risk_engine = RiskEngine()

    async def open_trade_from_signal(
        self,
        signal: TradingSignal,
        account_balance: float,
        account_currency: str = "USD",
    ) -> TradeRecord:
        """
        Execute a validated signal.
        Flow: risk gate → validate SL/TP → OANDA → Supabase → notify
        """
        if signal.signal == SignalType.NONE:
            raise ValueError("Cannot execute NONE signal")

        risk_settings = await self.risk_engine.get_risk_settings()
        allowed, reason = await self.risk_engine.can_open_trade(
            account_balance, signal.strategy, risk_settings
        )
        if not allowed:
            raise TradeBlockedError(f"Trade blocked: {reason}")

        valid, msg = self.risk_engine.validate_trade_parameters(
            signal.signal.value, signal.entry_price, signal.stop_loss, signal.take_profit
        )
        if not valid:
            raise ValueError(f"Invalid parameters: {msg}")

        # Send to OANDA
        try:
            oanda_response = await self.oanda.open_market_order(
                symbol=signal.symbol,
                direction=signal.signal.value,
                lot_size=signal.lot_size,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            )
        except OandaOrderError as e:
            logger.error("oanda_rejected", error=str(e))
            raise

        fill_tx = oanda_response.get("orderFillTransaction", {})
        oanda_trade_id = fill_tx.get("tradeOpened", {}).get("tradeID")
        actual_entry = float(fill_tx.get("price", signal.entry_price))

        trade_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        trade_row = {
            "id": trade_id,
            "symbol": signal.symbol,
            "direction": signal.signal.value,
            "entry_price": actual_entry,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "lot_size": signal.lot_size,
            "status": "OPEN",
            "strategy": signal.strategy.value,
            "risk_amount": signal.risk_amount,
            "oanda_trade_id": oanda_trade_id,
            "timeframe": signal.timeframe,
            "opened_at": now,
        }
        self.db.table("trades").insert(trade_row).execute()

        self.db.table("strategy_signals").insert({
            "signal": signal.signal.value,
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "strategy": signal.strategy.value,
            "entry_price": signal.entry_price,
            "stop_loss": signal.stop_loss,
            "take_profit": signal.take_profit,
            "stop_loss_pips": signal.stop_loss_pips,
            "lot_size": signal.lot_size,
            "risk_amount": signal.risk_amount,
            "reasoning": signal.reasoning,
            "executed": True,
            "trade_id": trade_id,
            "generated_at": now,
        }).execute()

        logger.info("trade_opened", trade_id=trade_id, strategy=signal.strategy.value,
                    symbol=signal.symbol, direction=signal.signal.value,
                    entry=actual_entry, sl=signal.stop_loss, tp=signal.take_profit)

        # Trigger push notification via Supabase (handled by DB trigger on insert)
        # The notification is sent automatically when the trade row is inserted.

        return TradeRecord(
            id=trade_id, symbol=signal.symbol,
            direction=TradeDirection(signal.signal.value),
            entry_price=actual_entry, stop_loss=signal.stop_loss,
            take_profit=signal.take_profit, lot_size=signal.lot_size,
            status=TradeStatus.OPEN, strategy=signal.strategy,
            risk_amount=signal.risk_amount, oanda_trade_id=oanda_trade_id,
            timeframe=signal.timeframe,
            opened_at=datetime.fromisoformat(now),
        )

    async def close_trade(
        self,
        trade_id: str,
        reason: str = "manual",
        current_price: Optional[float] = None,
    ) -> TradeRecord:
        result = self.db.table("trades").select("*").eq("id", trade_id).execute()
        if not result.data:
            raise TradeNotFoundError(f"Trade {trade_id} not found")

        trade = result.data[0]
        if trade["status"] != "OPEN":
            raise ValueError(f"Trade {trade_id} is already {trade['status']}")

        oanda_trade_id = trade.get("oanda_trade_id")
        close_price = current_price

        if oanda_trade_id:
            close_response = await self.oanda.close_trade(oanda_trade_id)
            close_tx = close_response.get("orderFillTransaction", {})
            close_price = float(close_tx.get("price", close_price or trade["entry_price"]))
            realized_pnl = float(close_tx.get("pl", 0))
        else:
            close_price = close_price or float(trade["entry_price"])
            realized_pnl = self._calculate_pnl(trade, close_price)

        pip_size = 0.01 if "JPY" in trade["symbol"].upper() else 0.0001
        entry = float(trade["entry_price"])
        pips = (close_price - entry) / pip_size
        if trade["direction"] == "SELL":
            pips = -pips

        now = datetime.now(timezone.utc).isoformat()

        self.db.table("trades").update({
            "status": "CLOSED",
            "pnl": round(realized_pnl, 4),
            "pnl_pips": round(pips, 1),
            "closed_at": now,
            "close_reason": reason,
        }).eq("id", trade_id).execute()

        logger.info("trade_closed", trade_id=trade_id, reason=reason,
                    pnl=realized_pnl, pips=pips, strategy=trade.get("strategy"))

        # Notification triggered automatically by Supabase DB trigger on update.

        return TradeRecord(
            id=trade["id"], symbol=trade["symbol"],
            direction=TradeDirection(trade["direction"]),
            entry_price=float(trade["entry_price"]),
            stop_loss=float(trade["stop_loss"]),
            take_profit=float(trade["take_profit"]),
            lot_size=float(trade["lot_size"]),
            status=TradeStatus.CLOSED,
            strategy=StrategyName(trade["strategy"]) if trade.get("strategy") else None,
            pnl=round(realized_pnl, 4),
            pnl_pips=round(pips, 1),
            oanda_trade_id=oanda_trade_id,
            opened_at=datetime.fromisoformat(trade["opened_at"]),
            closed_at=datetime.fromisoformat(now),
            close_reason=reason,
        )

    async def monitor_open_trades(self) -> None:
        """
        Sync open trades between Supabase and OANDA.
        If OANDA closed a trade (SL/TP hit), update our DB.
        """
        result = self.db.table("trades").select("*").eq("status", "OPEN").execute()
        if not result.data:
            return

        oanda_open = await self.oanda.get_open_trades()
        oanda_trade_ids = {t["id"] for t in oanda_open}

        for trade in result.data:
            oanda_id = trade.get("oanda_trade_id")
            if oanda_id and oanda_id not in oanda_trade_ids:
                logger.info("syncing_closed_trade", trade_id=trade["id"], oanda_id=oanda_id)
                try:
                    price_data = await self.oanda.get_current_price(trade["symbol"])
                    await self.close_trade(trade["id"], reason="sl_tp_hit",
                                           current_price=price_data["mid"])
                except Exception as e:
                    logger.error("monitor_sync_error", trade_id=trade["id"], error=str(e))

    def _calculate_pnl(self, trade: dict, close_price: float) -> float:
        entry = float(trade["entry_price"])
        units = float(trade["lot_size"]) * 100_000
        pip_size = 0.01 if "JPY" in trade["symbol"].upper() else 0.0001
        pips = (close_price - entry) / pip_size if trade["direction"] == "BUY" else (entry - close_price) / pip_size
        return round(pips * pip_size * units, 2)


class TradeBlockedError(Exception):
    pass

class TradeNotFoundError(Exception):
    pass
