import httpx
from typing import List, Optional, Dict, Any
from datetime import datetime
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import get_settings
from app.models.schemas import Candle, Timeframe

logger = structlog.get_logger()

# OANDA timeframe mapping
OANDA_GRANULARITY = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H4": "H4",
    "D": "D",
}


class OandaClient:
    """
    OANDA v20 REST API client.
    Handles market data, account info, and order execution.
    """

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.oanda_base_url
        self.account_id = self.settings.oanda_account_id
        self.headers = {
            "Authorization": f"Bearer {self.settings.oanda_api_key}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=httpx.Timeout(30.0, connect=10.0),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # MARKET DATA
    # ─────────────────────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        count: int = 250,
    ) -> List[Candle]:
        """Fetch OHLC candles from OANDA."""
        granularity = OANDA_GRANULARITY.get(timeframe, "H1")

        async with self._client() as client:
            resp = await client.get(
                f"/v3/instruments/{symbol}/candles",
                params={
                    "granularity": granularity,
                    "count": count,
                    "price": "M",  # Midpoint prices
                },
            )
            resp.raise_for_status()
            data = resp.json()

        candles = []
        for c in data.get("candles", []):
            if not c.get("complete", True):
                continue  # Skip incomplete (current) candle
            mid = c["mid"]
            candles.append(
                Candle(
                    time=datetime.fromisoformat(c["time"].replace("Z", "+00:00")),
                    open=float(mid["o"]),
                    high=float(mid["h"]),
                    low=float(mid["l"]),
                    close=float(mid["c"]),
                    volume=int(c.get("volume", 0)),
                )
            )

        logger.info("candles_fetched", symbol=symbol, timeframe=timeframe, count=len(candles))
        return candles

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_current_price(self, symbol: str) -> Dict[str, float]:
        """Get current bid/ask/mid price for a symbol."""
        async with self._client() as client:
            resp = await client.get(
                f"/v3/accounts/{self.account_id}/pricing",
                params={"instruments": symbol},
            )
            resp.raise_for_status()
            data = resp.json()

        prices = data["prices"][0]
        bid = float(prices["bids"][0]["price"])
        ask = float(prices["asks"][0]["price"])
        mid = (bid + ask) / 2

        return {"bid": bid, "ask": ask, "mid": mid, "spread": round(ask - bid, 6)}

    # ─────────────────────────────────────────────────────────────────────────
    # ACCOUNT
    # ─────────────────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def get_account(self) -> Dict[str, Any]:
        """Get full account summary from OANDA."""
        async with self._client() as client:
            resp = await client.get(f"/v3/accounts/{self.account_id}/summary")
            resp.raise_for_status()
            data = resp.json()

        return data["account"]

    # ─────────────────────────────────────────────────────────────────────────
    # ORDER EXECUTION
    # ─────────────────────────────────────────────────────────────────────────

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=5))
    async def open_market_order(
        self,
        symbol: str,
        direction: str,
        lot_size: float,
        stop_loss: float,
        take_profit: float,
    ) -> Dict[str, Any]:
        """
        Open a market order on OANDA with SL/TP attached.

        direction: "BUY" | "SELL"
        lot_size: in standard lots (1 lot = 100,000 units)
        """
        # OANDA uses units: positive = buy, negative = sell
        units = int(lot_size * 100_000)
        if direction == "SELL":
            units = -units

        order_body = {
            "order": {
                "type": "MARKET",
                "instrument": symbol,
                "units": str(units),
                "stopLossOnFill": {
                    "price": f"{stop_loss:.5f}",
                    "timeInForce": "GTC",
                },
                "takeProfitOnFill": {
                    "price": f"{take_profit:.5f}",
                    "timeInForce": "GTC",
                },
                "timeInForce": "FOK",  # Fill or Kill — no partial fills
            }
        }

        async with self._client() as client:
            resp = await client.post(
                f"/v3/accounts/{self.account_id}/orders",
                json=order_body,
            )

            if resp.status_code not in (200, 201):
                error_detail = resp.json()
                logger.error("oanda_order_failed", status=resp.status_code, detail=error_detail)
                raise OandaOrderError(
                    f"Order rejected by OANDA: {error_detail.get('errorMessage', 'Unknown error')}"
                )

            data = resp.json()

        trade_opened = data.get("orderFillTransaction", {})
        logger.info(
            "order_opened",
            symbol=symbol,
            direction=direction,
            units=units,
            oanda_trade_id=trade_opened.get("tradeOpened", {}).get("tradeID"),
        )
        return data

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=5))
    async def close_trade(self, oanda_trade_id: str) -> Dict[str, Any]:
        """Close an open trade by OANDA trade ID."""
        async with self._client() as client:
            resp = await client.put(
                f"/v3/accounts/{self.account_id}/trades/{oanda_trade_id}/close"
            )
            resp.raise_for_status()
            data = resp.json()

        logger.info("trade_closed_oanda", oanda_trade_id=oanda_trade_id)
        return data

    async def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all open trades from OANDA."""
        async with self._client() as client:
            resp = await client.get(f"/v3/accounts/{self.account_id}/openTrades")
            resp.raise_for_status()
            data = resp.json()
        return data.get("trades", [])

    # ─────────────────────────────────────────────────────────────────────────
    # PIP VALUE CALCULATION
    # ─────────────────────────────────────────────────────────────────────────

    def calculate_pip_value(
        self,
        symbol: str,
        lot_size: float,
        account_currency: str,
        current_price: Optional[float] = None,
    ) -> float:
        """
        Dynamically calculate pip value per lot for a given symbol.

        Rules:
        - Standard pair (e.g. EUR/USD, GBP/USD): pip = 0.0001
        - JPY pair (e.g. USD/JPY): pip = 0.01
        - Account currency adjustment applied.
        """
        # Determine pip size
        if "JPY" in symbol.upper():
            pip_size = 0.01
        else:
            pip_size = 0.0001

        # Units per lot
        units_per_lot = 100_000
        total_units = lot_size * units_per_lot

        # Base pip value in quote currency
        pip_value_quote = pip_size * total_units

        # If account currency matches quote currency → direct
        symbol_clean = symbol.replace("_", "").replace("/", "")
        quote_currency = symbol_clean[3:6].upper()

        if quote_currency == account_currency.upper():
            return pip_value_quote

        # If account currency matches base currency
        base_currency = symbol_clean[0:3].upper()
        if base_currency == account_currency.upper() and current_price:
            return pip_value_quote / current_price

        # Cross pair: approximate using current price
        if current_price:
            return pip_value_quote / current_price

        # Fallback: return quote currency value (slight approximation)
        logger.warning(
            "pip_value_approximation",
            symbol=symbol,
            account_currency=account_currency,
        )
        return pip_value_quote


class OandaOrderError(Exception):
    """Raised when OANDA rejects an order."""
    pass
