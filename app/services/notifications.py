"""
Push Notification Service

Architecture:
  Backend (FastAPI) → calls Supabase Edge Function (Deno/TypeScript)
  Edge Function → reads VAPID subscription from DB → sends Web Push

The single subscription (your device) is stored once in push_subscriptions table.
All notification types are built here with full trade/portfolio context.
"""

import httpx
from datetime import datetime, timezone
from typing import Optional
import structlog

from app.config import get_settings
from app.models.schemas import NotificationType

logger = structlog.get_logger()


class NotificationService:
    """
    Sends push notifications by calling the Supabase Edge Function.
    The Edge Function holds VAPID private key and handles actual Web Push delivery.
    """

    def __init__(self):
        self.settings = get_settings()
        # Edge Function URL: https://<project>.supabase.co/functions/v1/send-push-notification
        self.edge_function_url = (
            f"{self.settings.supabase_url}/functions/v1/send-push-notification"
        )
        self.headers = {
            "Authorization": f"Bearer {self.settings.supabase_anon_key}",
            "Content-Type": "application/json",
        }

    async def _send(self, notification_type: str, title: str, body: str, data: dict) -> bool:
        """Internal: POST to Supabase Edge Function."""
        payload = {
            "type": notification_type,
            "title": title,
            "body": body,
            "data": data,
            "icon": "/icon-192.png",
            "badge": "/badge-72.png",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.edge_function_url,
                    json=payload,
                    headers=self.headers,
                )
                if resp.status_code == 200:
                    logger.info("push_sent", type=notification_type, title=title)
                    return True
                else:
                    logger.warning(
                        "push_failed",
                        status=resp.status_code,
                        body=resp.text[:200],
                    )
                    return False
        except Exception as e:
            logger.error("push_error", error=str(e), type=notification_type)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # TRADE NOTIFICATIONS
    # ─────────────────────────────────────────────────────────────────────────

    async def notify_trade_opened(self, trade: dict, account_balance: float) -> None:
        direction_emoji = "🟢" if trade["direction"] == "BUY" else "🔴"
        symbol = trade["symbol"].replace("_", "/")
        risk_pct = round((trade.get("risk_amount", 0) / account_balance) * 100, 2) if account_balance else 0

        await self._send(
            notification_type=NotificationType.TRADE_OPENED,
            title=f"{direction_emoji} Trade Ouvert — {symbol}",
            body=(
                f"{trade['direction']} {trade['lot_size']} lot @ {trade['entry_price']}\n"
                f"SL: {trade['stop_loss']} | TP: {trade['take_profit']}\n"
                f"Risque: {risk_pct}% (${trade.get('risk_amount', 0):.2f})"
            ),
            data={
                "trade_id": trade["id"],
                "symbol": trade["symbol"],
                "direction": trade["direction"],
                "entry": trade["entry_price"],
                "sl": trade["stop_loss"],
                "tp": trade["take_profit"],
                "lots": trade["lot_size"],
                "strategy": trade.get("strategy", ""),
                "risk_amount": trade.get("risk_amount", 0),
                "risk_percent": risk_pct,
            },
        )

    async def notify_trade_closed(
        self,
        trade: dict,
        account_balance: float,
        daily_pnl: float,
        total_risk_percent: float,
    ) -> None:
        pnl = trade.get("pnl", 0) or 0
        pips = trade.get("pnl_pips", 0) or 0
        emoji = "✅" if pnl >= 0 else "❌"
        sign = "+" if pnl >= 0 else ""
        symbol = trade["symbol"].replace("_", "/")
        reason = trade.get("close_reason", "manual")
        daily_sign = "+" if daily_pnl >= 0 else ""

        await self._send(
            notification_type=NotificationType.TRADE_CLOSED,
            title=f"{emoji} Trade Fermé — {symbol}  {sign}{pnl:.2f}$",
            body=(
                f"{trade['direction']} {trade['lot_size']} lot | {sign}{pips:.1f} pips\n"
                f"Raison: {reason}\n"
                f"PnL jour: {daily_sign}{daily_pnl:.2f}$ | Risque portfolio: {total_risk_percent:.1f}%"
            ),
            data={
                "trade_id": trade["id"],
                "symbol": trade["symbol"],
                "pnl": pnl,
                "pnl_pips": pips,
                "close_reason": reason,
                "daily_pnl": daily_pnl,
                "total_risk_percent": total_risk_percent,
                "strategy": trade.get("strategy", ""),
            },
        )

    # ─────────────────────────────────────────────────────────────────────────
    # PERIODIC REPORTS
    # ─────────────────────────────────────────────────────────────────────────

    async def notify_daily_report(
        self,
        balance: float,
        daily_pnl: float,
        trades_count: int,
        win_rate: float,
        total_risk_percent: float,
        strategies_active: list,
    ) -> None:
        sign = "+" if daily_pnl >= 0 else ""
        daily_pct = (daily_pnl / balance * 100) if balance else 0
        emoji = "📈" if daily_pnl >= 0 else "📉"

        await self._send(
            notification_type=NotificationType.DAILY_REPORT,
            title=f"{emoji} Rapport Journalier — {sign}{daily_pnl:.2f}$ ({sign}{daily_pct:.2f}%)",
            body=(
                f"Balance: ${balance:,.2f}\n"
                f"Trades: {trades_count} | Win rate: {win_rate:.0f}%\n"
                f"Risque total portfolio: {total_risk_percent:.1f}%\n"
                f"Stratégies actives: {', '.join(strategies_active)}"
            ),
            data={
                "period": "daily",
                "balance": balance,
                "pnl": daily_pnl,
                "pnl_percent": daily_pct,
                "trades": trades_count,
                "win_rate": win_rate,
                "risk_percent": total_risk_percent,
                "strategies": strategies_active,
            },
        )

    async def notify_weekly_report(
        self,
        balance: float,
        weekly_pnl: float,
        trades_count: int,
        win_rate: float,
        best_strategy: str,
        total_risk_percent: float,
    ) -> None:
        sign = "+" if weekly_pnl >= 0 else ""
        weekly_pct = (weekly_pnl / balance * 100) if balance else 0
        emoji = "📊" if weekly_pnl >= 0 else "📉"

        await self._send(
            notification_type=NotificationType.WEEKLY_REPORT,
            title=f"{emoji} Rapport Hebdomadaire — {sign}{weekly_pnl:.2f}$ ({sign}{weekly_pct:.2f}%)",
            body=(
                f"Balance: ${balance:,.2f}\n"
                f"Trades: {trades_count} | Win rate: {win_rate:.0f}%\n"
                f"Meilleure stratégie: {best_strategy}\n"
                f"Risque total portfolio: {total_risk_percent:.1f}%"
            ),
            data={
                "period": "weekly",
                "balance": balance,
                "pnl": weekly_pnl,
                "pnl_percent": weekly_pct,
                "trades": trades_count,
                "win_rate": win_rate,
                "best_strategy": best_strategy,
                "risk_percent": total_risk_percent,
            },
        )

    async def notify_monthly_report(
        self,
        balance: float,
        monthly_pnl: float,
        trades_count: int,
        win_rate: float,
        avg_rr: float,
        total_risk_percent: float,
        strategies_breakdown: dict,
    ) -> None:
        sign = "+" if monthly_pnl >= 0 else ""
        monthly_pct = (monthly_pnl / balance * 100) if balance else 0
        emoji = "🗓️" if monthly_pnl >= 0 else "📉"

        breakdown_text = " | ".join(
            f"{k}: {'+' if v>=0 else ''}{v:.0f}$"
            for k, v in strategies_breakdown.items()
        )

        await self._send(
            notification_type=NotificationType.MONTHLY_REPORT,
            title=f"{emoji} Rapport Mensuel — {sign}{monthly_pnl:.2f}$ ({sign}{monthly_pct:.2f}%)",
            body=(
                f"Balance: ${balance:,.2f}\n"
                f"Trades: {trades_count} | Win rate: {win_rate:.0f}% | RR moyen: {avg_rr:.2f}\n"
                f"Risque total: {total_risk_percent:.1f}%\n"
                f"{breakdown_text}"
            ),
            data={
                "period": "monthly",
                "balance": balance,
                "pnl": monthly_pnl,
                "pnl_percent": monthly_pct,
                "trades": trades_count,
                "win_rate": win_rate,
                "avg_rr": avg_rr,
                "risk_percent": total_risk_percent,
                "strategies_breakdown": strategies_breakdown,
            },
        )

    async def notify_annual_report(
        self,
        balance: float,
        annual_pnl: float,
        starting_balance: float,
        trades_count: int,
        win_rate: float,
        max_drawdown: float,
        total_risk_percent: float,
    ) -> None:
        sign = "+" if annual_pnl >= 0 else ""
        annual_pct = (annual_pnl / starting_balance * 100) if starting_balance else 0
        emoji = "🏆" if annual_pnl >= 0 else "📉"

        await self._send(
            notification_type=NotificationType.ANNUAL_REPORT,
            title=f"{emoji} Rapport Annuel — {sign}{annual_pnl:.2f}$ ({sign}{annual_pct:.2f}%)",
            body=(
                f"Balance: ${balance:,.2f} (départ: ${starting_balance:,.2f})\n"
                f"Trades: {trades_count} | Win rate: {win_rate:.0f}%\n"
                f"Max drawdown: {max_drawdown:.2f}%\n"
                f"Risque total portfolio: {total_risk_percent:.1f}%"
            ),
            data={
                "period": "annual",
                "balance": balance,
                "starting_balance": starting_balance,
                "pnl": annual_pnl,
                "pnl_percent": annual_pct,
                "trades": trades_count,
                "win_rate": win_rate,
                "max_drawdown": max_drawdown,
                "risk_percent": total_risk_percent,
            },
        )

    # ─────────────────────────────────────────────────────────────────────────
    # RISK & SYSTEM ALERTS
    # ─────────────────────────────────────────────────────────────────────────

    async def notify_risk_alert(
        self,
        alert_type: str,
        message: str,
        current_loss_pct: float,
        limit_pct: float,
    ) -> None:
        await self._send(
            notification_type=NotificationType.RISK_ALERT,
            title=f"⚠️ Alerte Risque — {alert_type}",
            body=f"{message}\nPerte actuelle: {current_loss_pct:.2f}% / Limite: {limit_pct:.2f}%",
            data={
                "alert_type": alert_type,
                "current_loss_pct": current_loss_pct,
                "limit_pct": limit_pct,
            },
        )

    async def notify_drawdown_alert(
        self,
        drawdown_pct: float,
        balance: float,
        peak_balance: float,
    ) -> None:
        await self._send(
            notification_type=NotificationType.DRAWDOWN_ALERT,
            title=f"🚨 Drawdown Élevé — {drawdown_pct:.1f}%",
            body=(
                f"Balance actuelle: ${balance:,.2f}\n"
                f"Pic: ${peak_balance:,.2f}\n"
                f"Drawdown: -{drawdown_pct:.2f}%"
            ),
            data={
                "drawdown_pct": drawdown_pct,
                "balance": balance,
                "peak_balance": peak_balance,
            },
        )

    async def notify_strategy_disabled(
        self,
        strategy_name: str,
        reason: str,
        daily_loss: float,
        limit: float,
    ) -> None:
        await self._send(
            notification_type=NotificationType.STRATEGY_DISABLED,
            title=f"🛑 Stratégie Désactivée — {strategy_name}",
            body=f"Raison: {reason}\nPerte journalière: ${daily_loss:.2f} / Limite: ${limit:.2f}",
            data={
                "strategy": strategy_name,
                "reason": reason,
                "daily_loss": daily_loss,
                "limit": limit,
            },
        )

    async def notify_streak_alert(
        self,
        streak_type: str,
        count: int,
        total_pnl: float,
    ) -> None:
        """Win/loss streak alerts."""
        if streak_type == "WIN":
            emoji = "🔥"
            title = f"{emoji} Série gagnante — {count} trades consécutifs!"
        else:
            emoji = "❄️"
            title = f"{emoji} Série perdante — {count} trades consécutifs"

        sign = "+" if total_pnl >= 0 else ""
        await self._send(
            notification_type=NotificationType.STREAK_ALERT,
            title=title,
            body=f"PnL série: {sign}{total_pnl:.2f}$",
            data={"streak_type": streak_type, "count": count, "pnl": total_pnl},
        )

    async def notify_market_session(
        self,
        session: str,
        action: str,
        high_volatility_pairs: list,
    ) -> None:
        """Forex market session open/close alerts."""
        emoji_map = {"London": "🇬🇧", "New York": "🇺🇸", "Tokyo": "🇯🇵", "Sydney": "🇦🇺"}
        emoji = emoji_map.get(session, "🌍")
        notif_type = NotificationType.MARKET_OPEN if action == "open" else NotificationType.MARKET_CLOSE
        action_label = "Ouverture" if action == "open" else "Fermeture"

        await self._send(
            notification_type=notif_type,
            title=f"{emoji} Session {session} — {action_label}",
            body=f"Paires volatiles: {', '.join(high_volatility_pairs)}",
            data={"session": session, "action": action, "pairs": high_volatility_pairs},
        )

    async def notify_margin_warning(
        self,
        margin_level_pct: float,
        margin_used: float,
        margin_available: float,
    ) -> None:
        await self._send(
            notification_type="MARGIN_WARNING",
            title=f"⚠️ Alerte Marge — Niveau {margin_level_pct:.0f}%",
            body=(
                f"Marge utilisée: ${margin_used:,.2f}\n"
                f"Marge disponible: ${margin_available:,.2f}"
            ),
            data={
                "margin_level": margin_level_pct,
                "margin_used": margin_used,
                "margin_available": margin_available,
            },
        )

    async def notify_new_signal(
        self,
        signal: dict,
        auto_executed: bool,
    ) -> None:
        """Notify when a strategy generates a signal (even if not auto-executed)."""
        direction_emoji = "🟢" if signal["signal"] == "BUY" else "🔴"
        symbol = signal["symbol"].replace("_", "/")
        executed_label = "✅ Exécuté" if auto_executed else "⏳ En attente"

        await self._send(
            notification_type="SIGNAL_GENERATED",
            title=f"{direction_emoji} Signal {signal['strategy']} — {symbol}",
            body=(
                f"{signal['signal']} @ {signal.get('entry_price', 'market')}\n"
                f"SL: {signal.get('stop_loss')} | TP: {signal.get('take_profit')}\n"
                f"{executed_label}"
            ),
            data=signal,
        )
