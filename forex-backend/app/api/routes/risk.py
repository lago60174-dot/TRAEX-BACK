from fastapi import APIRouter, Depends, HTTPException
import structlog

from app.core.risk_engine import RiskEngine
from app.core.portfolio_manager import PortfolioManager
from app.models.schemas import RiskSettingsUpdate
from app.services.auth import verify_token

router = APIRouter(prefix="/risk", tags=["Risk"])
logger = structlog.get_logger()


@router.get("/status")
async def get_risk_status(_: dict = Depends(verify_token)):
    """
    État complet du risk management :
    - Limites portfolio
    - Limites par stratégie
    - Trading autorisé ou non
    """
    try:
        account = await PortfolioManager().get_account_info()
        return await RiskEngine().get_risk_status(account.balance)
    except Exception as e:
        logger.error("risk_status_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings")
async def get_risk_settings(_: dict = Depends(verify_token)):
    """Paramètres actuels du risk management."""
    try:
        rs = await RiskEngine().get_risk_settings()
        return rs.dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings")
async def update_risk_settings(updates: RiskSettingsUpdate, _: dict = Depends(verify_token)):
    """
    Met à jour les paramètres de risque.
    Seuls les champs fournis sont modifiés.
    """
    try:
        update_dict = {k: v for k, v in updates.dict().items() if v is not None}
        if not update_dict:
            raise HTTPException(status_code=422, detail="No fields to update")
        new_settings = await RiskEngine().update_risk_settings(update_dict)
        return {"success": True, "settings": new_settings.dict()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
