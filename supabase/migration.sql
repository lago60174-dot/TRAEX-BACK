-- ═══════════════════════════════════════════════════════════════════════════
-- FOREX TRADING BACKEND — SUPABASE MIGRATION
-- Run this entire script once in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════

-- ── TRADES ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol        TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('BUY', 'SELL')),
    strategy      TEXT CHECK (strategy IN ('EMA_PULLBACK', 'RSI_MEAN_REVERSION', 'BREAKOUT_ATR')),
    entry_price   DECIMAL(18, 6) NOT NULL,
    stop_loss     DECIMAL(18, 6) NOT NULL,
    take_profit   DECIMAL(18, 6) NOT NULL,
    lot_size      DECIMAL(10, 4) NOT NULL,
    status        TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'CANCELLED')),
    pnl           DECIMAL(18, 4),
    pnl_pips      DECIMAL(10, 2),
    risk_amount   DECIMAL(18, 4),
    oanda_trade_id TEXT,
    timeframe     TEXT,
    close_reason  TEXT,
    opened_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at     TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trades_status     ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_opened_at  ON trades(opened_at);
CREATE INDEX IF NOT EXISTS idx_trades_closed_at  ON trades(closed_at);
CREATE INDEX IF NOT EXISTS idx_trades_strategy   ON trades(strategy);
CREATE INDEX IF NOT EXISTS idx_trades_symbol     ON trades(symbol);

-- ── RISK SETTINGS ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS risk_settings (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    max_concurrent_strategies INTEGER NOT NULL DEFAULT 2,
    max_open_trades           INTEGER NOT NULL DEFAULT 2,
    max_portfolio_daily_loss  DECIMAL(6, 4) NOT NULL DEFAULT 0.06,
    strategy_risk             JSONB NOT NULL DEFAULT '{
        "EMA_PULLBACK":       {"risk_percent": 0.01, "max_daily_loss": 0.03, "swing_lookback": 10, "rr_ratio": 2.0},
        "RSI_MEAN_REVERSION": {"risk_percent": 0.01, "max_daily_loss": 0.03, "swing_lookback": 10, "rr_ratio": 1.5},
        "BREAKOUT_ATR":       {"risk_percent": 0.01, "max_daily_loss": 0.03, "swing_lookback": 14, "rr_ratio": 2.5}
    }'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed default row
INSERT INTO risk_settings DEFAULT VALUES
ON CONFLICT DO NOTHING;

-- ── STRATEGY SIGNALS LOG ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_signals (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal        TEXT NOT NULL CHECK (signal IN ('BUY', 'SELL', 'NONE')),
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    strategy      TEXT,
    entry_price   DECIMAL(18, 6),
    stop_loss     DECIMAL(18, 6),
    take_profit   DECIMAL(18, 6),
    stop_loss_pips DECIMAL(10, 2),
    lot_size      DECIMAL(10, 4),
    risk_amount   DECIMAL(18, 4),
    reasoning     TEXT,
    executed      BOOLEAN DEFAULT FALSE,
    trade_id      UUID REFERENCES trades(id),
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_generated ON strategy_signals(generated_at);
CREATE INDEX IF NOT EXISTS idx_signals_strategy  ON strategy_signals(strategy);

-- ── ACCOUNT SNAPSHOTS ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS account_snapshots (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    balance     DECIMAL(18, 4) NOT NULL,
    equity      DECIMAL(18, 4) NOT NULL,
    daily_pnl   DECIMAL(18, 4) DEFAULT 0,
    open_trades INTEGER DEFAULT 0,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_snapshots_at ON account_snapshots(snapshot_at);

-- ── PUSH SUBSCRIPTIONS ────────────────────────────────────────────────────────
-- Single row: your device subscription. Active by default.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint        TEXT NOT NULL UNIQUE,
    auth_key        TEXT NOT NULL,
    p256dh_key      TEXT NOT NULL,
    user_agent      TEXT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deactivated_at  TIMESTAMPTZ
);

-- ── NOTIFICATION LOGS ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notification_logs (
    id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type     TEXT NOT NULL,
    title    TEXT NOT NULL,
    body     TEXT,
    sent     BOOLEAN NOT NULL DEFAULT FALSE,
    error    TEXT,
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notif_logs_sent_at ON notification_logs(sent_at);
CREATE INDEX IF NOT EXISTS idx_notif_logs_type    ON notification_logs(type);

-- ═══════════════════════════════════════════════════════════════════════════
-- ROW LEVEL SECURITY — Service role key bypasses all RLS
-- ═══════════════════════════════════════════════════════════════════════════
ALTER TABLE trades              ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_settings       ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_signals    ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_snapshots   ENABLE ROW LEVEL SECURITY;
ALTER TABLE push_subscriptions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_logs   ENABLE ROW LEVEL SECURITY;

-- ═══════════════════════════════════════════════════════════════════════════
-- SUPABASE DATABASE TRIGGER → Edge Function
--
-- When a trade is INSERTED (opened) or UPDATED to CLOSED,
-- Supabase calls the Edge Function via pg_net (HTTP).
-- This is the bridge between DB events and push notifications.
-- ═══════════════════════════════════════════════════════════════════════════

-- Enable pg_net extension (for HTTP calls from triggers)
CREATE EXTENSION IF NOT EXISTS pg_net;

-- ── Trigger function: trade opened ───────────────────────────────────────────
CREATE OR REPLACE FUNCTION notify_trade_opened()
RETURNS TRIGGER AS $$
DECLARE
    payload JSONB;
    edge_url TEXT;
BEGIN
    -- Only fire on INSERT of OPEN trades
    IF TG_OP != 'INSERT' OR NEW.status != 'OPEN' THEN
        RETURN NEW;
    END IF;

    edge_url := current_setting('app.supabase_url', true) || '/functions/v1/send-push-notification';

    payload := jsonb_build_object(
        'type',  'TRADE_OPENED',
        'title', CASE NEW.direction
                   WHEN 'BUY'  THEN '🟢 Trade Ouvert — ' || replace(NEW.symbol, '_', '/')
                   WHEN 'SELL' THEN '🔴 Trade Ouvert — ' || replace(NEW.symbol, '_', '/')
                 END,
        'body',  NEW.direction || ' ' || NEW.lot_size || ' lot @ ' || NEW.entry_price ||
                 ' | SL: ' || NEW.stop_loss || ' | TP: ' || NEW.take_profit,
        'data',  jsonb_build_object(
            'trade_id',  NEW.id,
            'symbol',    NEW.symbol,
            'direction', NEW.direction,
            'strategy',  NEW.strategy,
            'entry',     NEW.entry_price,
            'sl',        NEW.stop_loss,
            'tp',        NEW.take_profit,
            'lots',      NEW.lot_size,
            'risk',      NEW.risk_amount
        )
    );

    PERFORM net.http_post(
        url     := edge_url,
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'Authorization', 'Bearer ' || current_setting('app.supabase_anon_key', true)
        ),
        body    := payload::text
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS trg_notify_trade_opened ON trades;
CREATE TRIGGER trg_notify_trade_opened
    AFTER INSERT ON trades
    FOR EACH ROW EXECUTE FUNCTION notify_trade_opened();

-- ── Trigger function: trade closed ───────────────────────────────────────────
CREATE OR REPLACE FUNCTION notify_trade_closed()
RETURNS TRIGGER AS $$
DECLARE
    payload JSONB;
    edge_url TEXT;
    pnl_val  DECIMAL;
    emoji    TEXT;
BEGIN
    -- Only fire when status changes to CLOSED
    IF TG_OP != 'UPDATE' OR NEW.status != 'CLOSED' OR OLD.status = 'CLOSED' THEN
        RETURN NEW;
    END IF;

    pnl_val  := COALESCE(NEW.pnl, 0);
    emoji    := CASE WHEN pnl_val >= 0 THEN '✅' ELSE '❌' END;
    edge_url := current_setting('app.supabase_url', true) || '/functions/v1/send-push-notification';

    payload := jsonb_build_object(
        'type',  'TRADE_CLOSED',
        'title', emoji || ' Trade Fermé — ' || replace(NEW.symbol, '_', '/') ||
                 ' ' || CASE WHEN pnl_val >= 0 THEN '+' ELSE '' END || round(pnl_val, 2)::text || '$',
        'body',  NEW.direction || ' ' || NEW.lot_size || ' lot | ' ||
                 COALESCE(NEW.pnl_pips::text, '0') || ' pips | Raison: ' || COALESCE(NEW.close_reason, 'manual'),
        'data',  jsonb_build_object(
            'trade_id',    NEW.id,
            'symbol',      NEW.symbol,
            'direction',   NEW.direction,
            'strategy',    NEW.strategy,
            'pnl',         NEW.pnl,
            'pnl_pips',    NEW.pnl_pips,
            'close_reason', NEW.close_reason
        )
    );

    PERFORM net.http_post(
        url     := edge_url,
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'Authorization', 'Bearer ' || current_setting('app.supabase_anon_key', true)
        ),
        body    := payload::text
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS trg_notify_trade_closed ON trades;
CREATE TRIGGER trg_notify_trade_closed
    AFTER UPDATE ON trades
    FOR EACH ROW EXECUTE FUNCTION notify_trade_closed();

-- ── Set app config (replace with your actual values) ─────────────────────────
-- Run these separately after deployment:
--
-- ALTER DATABASE postgres SET app.supabase_url = 'https://YOUR_PROJECT.supabase.co';
-- ALTER DATABASE postgres SET app.supabase_anon_key = 'YOUR_ANON_KEY';
