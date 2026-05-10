from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import structlog

from app.data.database import get_supabase
from app.services.notifications import NotificationService
from app.services.auth import verify_token

router = APIRouter(prefix="/notifications", tags=["Notifications"])
logger = structlog.get_logger()


class SubscriptionPayload(BaseModel):
    endpoint: str
    auth_key: str
    p256dh_key: str
    user_agent: str = ""


class TestNotificationRequest(BaseModel):
    type: str = "TEST"


@router.post("/subscribe", status_code=201)
async def subscribe(_: dict = Depends(verify_token), payload: SubscriptionPayload = ...):
    """
    Enregistre ou met à jour la subscription push (ton appareil).
    Un seul enregistrement actif à la fois — remplace l'ancien si présent.
    """
    db = get_supabase()
    try:
        # Désactiver les anciennes subscriptions
        db.table("push_subscriptions").update({"active": False}).eq("active", True).execute()

        # Insérer la nouvelle
        db.table("push_subscriptions").insert({
            "endpoint":    payload.endpoint,
            "auth_key":    payload.auth_key,
            "p256dh_key":  payload.p256dh_key,
            "user_agent":  payload.user_agent,
            "active":      True,
        }).execute()

        logger.info("push_subscription_saved", endpoint=payload.endpoint[:60])
        return {"success": True, "message": "Subscription enregistrée"}
    except Exception as e:
        logger.error("subscribe_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def subscription_status(_: dict = Depends(verify_token)):
    """Vérifie si une subscription active est enregistrée."""
    db = get_supabase()
    result = db.table("push_subscriptions").select("id, endpoint, active, created_at") \
        .eq("active", True).limit(1).execute()
    if not result.data:
        return {"active": False, "message": "Aucune subscription enregistrée"}
    sub = result.data[0]
    return {
        "active": True,
        "endpoint_prefix": sub["endpoint"][:60] + "...",
        "created_at": sub["created_at"],
    }


@router.post("/test")
async def send_test_notification(_: dict = Depends(verify_token)):
    """Envoie une notification de test sur ton appareil."""
    notif = NotificationService()
    ok = await notif._send(
        notification_type="TEST",
        title="✅ Notifications actives",
        body="Ton système de trading est prêt. Les alertes sont opérationnelles.",
        data={"test": True},
    )
    if ok:
        return {"success": True, "message": "Notification envoyée"}
    raise HTTPException(status_code=500, detail="Notification échouée — vérifie la subscription et les clés VAPID")


@router.get("/logs")
async def get_notification_logs(limit: int = 50, _: dict = Depends(verify_token)):
    """Historique des notifications envoyées."""
    db = get_supabase()
    result = db.table("notification_logs").select("*") \
        .order("sent_at", desc=True).limit(limit).execute()
    return result.data or []
