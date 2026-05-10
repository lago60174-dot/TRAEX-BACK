from supabase import create_client, Client
from app.config import get_settings
from functools import lru_cache
import structlog

logger = structlog.get_logger()


@lru_cache()
def get_supabase() -> Client:
    settings = get_settings()
    client = create_client(
        settings.supabase_url,
        settings.supabase_service_role_key  # Service role for backend — bypasses RLS
    )
    return client


# ─────────────────────────────────────────────────────────────────────────────
# SQL MIGRATIONS — Run once in Supabase SQL Editor
# ─────────────────────────────────────────────────────────────────────────────

MIGRATIONS_SQL = """
-- ── TRADES TABLE ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    entry_price DECIMAL(18, 6) NOT NULL,
    stop_loss DECIMAL(18, 6) NOT NULL,
    take_profit DECIMAL(18, 6) NOT NULL,
    lot_size DECIMAL(10, 4) NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'CANCELLED')),
    pnl DECIMAL(18, 4),
    pnl_pips DECIMAL(10, 2),
    risk_amount DECIMAL(18, 4),
    oanda_trade_id TEXT,
    timeframe TEXT,
    close_reason TEXT,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_opened_at ON trades(opened_at);

-- ── RISK SETTINGS TABLE ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS risk_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    risk_percent DECIMAL(6, 4) NOT NULL DEFAULT 0.01,
    max_daily_loss DECIMAL(6, 4) NOT NULL DEFAULT 0.03,
    max_open_trades INTEGER NOT NULL DEFAULT 1,
    swing_lookback INTEGER NOT NULL DEFAULT 10,
    rr_ratio DECIMAL(5, 2) NOT NULL DEFAULT 2.0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert default risk settings if not exists
INSERT INTO risk_settings (risk_percent, max_daily_loss, max_open_trades, swing_lookback, rr_ratio)
SELECT 0.01, 0.03, 1, 10, 2.0
WHERE NOT EXISTS (SELECT 1 FROM risk_settings LIMIT 1);

-- ── STRATEGY SIGNALS LOG ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    entry_price DECIMAL(18, 6),
    stop_loss DECIMAL(18, 6),
    take_profit DECIMAL(18, 6),
    stop_loss_pips DECIMAL(10, 2),
    lot_size DECIMAL(10, 4),
    risk_amount DECIMAL(18, 4),
    reasoning TEXT,
    executed BOOLEAN DEFAULT FALSE,
    trade_id UUID REFERENCES trades(id),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── ACCOUNT SNAPSHOTS ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS account_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    balance DECIMAL(18, 4) NOT NULL,
    equity DECIMAL(18, 4) NOT NULL,
    daily_pnl DECIMAL(18, 4) DEFAULT 0,
    open_trades INTEGER DEFAULT 0,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Row Level Security (enable but bypass with service role key)
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_snapshots ENABLE ROW LEVEL SECURITY;
"""
