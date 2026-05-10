from fastapi import APIRouter, Depends, HTTPException
import structlog

from app.core.portfolio_manager import PortfolioManager
from app.models.schemas import AccountInfo
from app.services.auth import verify_token

router = APIRouter(prefix="/account", tags=["Account"])
logger = structlog.get_logger()


@router.get("", response_model=AccountInfo)
async def get_account(_: dict = Depends(verify_token)):
    """Get full account info: balance, equity, margin, daily PnL."""
    try:
        pm = PortfolioManager()
        return await pm.get_account_info()
    except Exception as e:
        logger.error("get_account_error", error=str(e))
        raise HTTPException(status_code=502, detail=f"OANDA error: {str(e)}")


@router.get("/balance")
async def get_balance(_: dict = Depends(verify_token)):
    """Quick balance check."""
    try:
        pm = PortfolioManager()
        info = await pm.get_account_info()
        return {
            "balance": info.balance,
            "equity": info.equity,
            "currency": info.currency,
            "daily_pnl": info.daily_pnl,
            "daily_pnl_percent": info.daily_pnl_percent,
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
