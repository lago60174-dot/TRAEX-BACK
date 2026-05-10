from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog

from app.config import get_settings
from app.utils.logging import configure_logging
from app.services.scheduler import start_scheduler, stop_scheduler
from app.api.routes import account, trades, strategy, risk, notifications, auth
from app.core.strategy_registry import get_strategy, get_all_strategies

configure_logging()
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("startup", env=get_settings().app_env)
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("shutdown")


settings = get_settings()

app = FastAPI(
    title="Forex Trading Backend",
    description="Production-grade rule-based trading system with OANDA integration",
    version="1.0.0",
    lifespan=lifespan,
    # Disable docs in production
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# In production, replace with your Vercel/Netlify/Lovable frontend URL
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else [
        "https://traex.vercel.app",  # Replace with your actual frontend URL
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── GLOBAL ERROR HANDLER ──────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("unhandled_exception", path=str(request.url), error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "Internal server error", "detail": str(exc)},
    )


# ── ROUTES ────────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(account.router)
app.include_router(trades.router)
app.include_router(strategy.router)
app.include_router(risk.router)
app.include_router(notifications.router)


@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "env": settings.app_env, "oanda_env": settings.oanda_environment}


@app.get("/", tags=["System"])
async def root():
    return {"name": "Forex Trading Backend", "version": "1.0.0", "status": "running"}
