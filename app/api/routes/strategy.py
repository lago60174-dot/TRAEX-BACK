from fastapi import APIRouter, Depends, HTTPException
import structlog

from app.core.strategy_engine import StrategyEngine
from app.core.risk_engine import RiskEngine
from app.core.execution_engine import ExecutionEngine, TradeBlockedError
from app.core.portfolio_manager import PortfolioManager
from app.data.oanda_client import OandaClient
from app.models.schemas import StrategyRunRequest, TradingSignal, SignalType
from app.services.auth import verify_token

router = APIRouter(prefix="/strategy", tags=["Strategy"])
logger = structlog.get_logger()


@router.post("/run", response_model=TradingSignal)
async def run_strategy(
    request: StrategyRunRequest,
    _: dict = Depends(verify_token),
):
    """
    Analyze market and generate a trading signal.

    If auto_execute=True and signal is valid, opens a trade automatically.
    Otherwise returns signal for manual review.
    """
    oanda = OandaClient()
    risk_engine = RiskEngine()
    strategy = StrategyEngine()

    try:
        # 1. Fetch candles
        candles = await oanda.get_candles(
            symbol=request.symbol,
            timeframe=request.timeframe.value,
            count=250,
        )

        if len(candles) < 210:
            raise HTTPException(
                status_code=422,
                detail=f"Insufficient data: only {len(candles)} candles available",
            )

        # 2. Get account state
        pm = PortfolioManager()
        account = await pm.get_account_info()

        # 3. Get pip value
        price_data = await oanda.get_current_price(request.symbol)
        pip_value = oanda.calculate_pip_value(
            symbol=request.symbol,
            lot_size=1.0,  # per 1 lot
            account_currency=account.currency,
            current_price=price_data["mid"],
        )

        # 4. Get risk settings
        risk_settings = await risk_engine.get_risk_settings()

        # 5. Build signal
        signal = strategy.build_signal(
            candles=candles,
            symbol=request.symbol,
            account_balance=account.balance,
            risk_settings=risk_settings,
            pip_value_per_lot=pip_value,
            timeframe=request.timeframe.value,
        )

        logger.info(
            "strategy_run",
            symbol=request.symbol,
            timeframe=request.timeframe,
            signal=signal.signal.value,
            auto_execute=request.auto_execute,
        )

        # 6. Auto-execute if requested and signal is valid
        if request.auto_execute and signal.signal != SignalType.NONE:
            engine = ExecutionEngine()
            try:
                trade = await engine.open_trade_from_signal(signal, account.balance, account.currency)
                logger.info("auto_executed", trade_id=trade.id)
            except TradeBlockedError as e:
                logger.warning("auto_execute_blocked", reason=str(e))
                signal.reasoning = f"{signal.reasoning} | Auto-execute blocked: {str(e)}"

        return signal

    except HTTPException:
        raise
    except Exception as e:
        logger.error("strategy_run_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_strategy_status(_: dict = Depends(verify_token)):
    """Get current strategy configuration."""
    risk_engine = RiskEngine()
    risk_settings = await risk_engine.get_risk_settings()

    return {
        "strategy": "EMA Trend + Pullback",
        "ema_fast": 50,
        "ema_slow": 200,
        "risk_percent": risk_settings.risk_percent,
        "rr_ratio": risk_settings.rr_ratio,
        "swing_lookback": risk_settings.swing_lookback,
        "status": "active",
    }
