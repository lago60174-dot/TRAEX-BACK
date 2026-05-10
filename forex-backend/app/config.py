from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    # ── OANDA ──────────────────────────────────
    oanda_api_key: str = Field(..., env="OANDA_API_KEY")
    oanda_account_id: str = Field(..., env="OANDA_ACCOUNT_ID")
    oanda_environment: str = Field("practice", env="OANDA_ENVIRONMENT")
    oanda_base_url_practice: str = Field(
        "https://api-fxpractice.oanda.com/v3", env="OANDA_BASE_URL_PRACTICE"
    )
    oanda_base_url_live: str = Field(
        "https://api-fxtrade.oanda.com/v3", env="OANDA_BASE_URL_LIVE"
    )

    # ── SUPABASE ───────────────────────────────
    supabase_url: str = Field(..., env="SUPABASE_URL")
    supabase_service_role_key: str = Field(..., env="SUPABASE_SERVICE_ROLE_KEY")
    supabase_anon_key: str = Field(..., env="SUPABASE_ANON_KEY")

    # ── SECURITY ───────────────────────────────
    jwt_secret: str = Field(..., env="JWT_SECRET")
    jwt_algorithm: str = Field("HS256", env="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(1440, env="JWT_EXPIRE_MINUTES")

    # ── APP ────────────────────────────────────
    app_env: str = Field("development", env="APP_ENV")
    app_host: str = Field("0.0.0.0", env="APP_HOST")
    app_port: int = Field(8000, env="APP_PORT")
    log_level: str = Field("INFO", env="LOG_LEVEL")

    # ── RISK DEFAULTS ──────────────────────────
    default_risk_percent: float = Field(0.01, env="DEFAULT_RISK_PERCENT")
    default_max_daily_loss: float = Field(0.03, env="DEFAULT_MAX_DAILY_LOSS")
    default_max_open_trades: int = Field(1, env="DEFAULT_MAX_OPEN_TRADES")
    default_swing_lookback: int = Field(10, env="DEFAULT_SWING_LOOKBACK")

    # ── AUTH ───────────────────────────────────
    admin_password_hash: str = Field("", env="ADMIN_PASSWORD_HASH")

    # ── MONITORING ─────────────────────────────
    monitor_interval_seconds: int = Field(30, env="MONITOR_INTERVAL_SECONDS")

    @property
    def oanda_base_url(self) -> str:
        if self.oanda_environment == "live":
            return self.oanda_base_url_live
        return self.oanda_base_url_practice

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    return Settings()
