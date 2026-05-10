from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
import structlog

from app.services.auth import create_access_token, verify_password, get_password_hash
from app.config import get_settings

router = APIRouter(prefix="/auth", tags=["Auth"])
logger = structlog.get_logger()


class LoginRequest(BaseModel):
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    Authentification par mot de passe unique (système mono-utilisateur).
    Le hash du mot de passe est stocké dans la variable d'environnement ADMIN_PASSWORD_HASH.
    Pour générer : python -c "from passlib.context import CryptContext; print(CryptContext(['bcrypt']).hash('TON_MDP'))"
    """
    settings = get_settings()

    if not settings.admin_password_hash:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_PASSWORD_HASH not configured. Set it in .env",
        )

    if not verify_password(request.password, settings.admin_password_hash):
        logger.warning("login_failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mot de passe incorrect",
        )

    token = create_access_token({"sub": "admin"})
    logger.info("login_success")

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=settings.jwt_expire_minutes,
    )


@router.post("/refresh")
async def refresh_token():
    """
    Renouvelle le token depuis le frontend sans re-saisir le mot de passe.
    Appeler depuis le frontend avant expiration (< 60 min restantes).
    """
    # Le token actuel est validé par verify_token dans le middleware
    # Génère un nouveau token fraîchement daté
    settings = get_settings()
    token = create_access_token({"sub": "admin"})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=settings.jwt_expire_minutes,
    )
