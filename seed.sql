-- Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.
-- Author: Asaad Riaz
--
-- VB-OS Trading Reference Integration -- seed data.
--
-- Every monetary value is in cents and every proportion in basis points.
-- No floating-point arithmetic anywhere in the evaluation pipeline.
-- All pre-computed fields use 64-bit integers within i64 range.

-- ============================================================
-- Table: accounts
-- ============================================================
CREATE TABLE accounts (
    account_id         TEXT PRIMARY KEY,
    account_status     TEXT NOT NULL,
    buying_power_cents BIGINT NOT NULL
);

-- ============================================================
-- Table: instruments
-- ============================================================
CREATE TABLE instruments (
    instrument_id    TEXT PRIMARY KEY,
    symbol           TEXT NOT NULL UNIQUE,
    asset_class      TEXT NOT NULL,       -- equity or crypto
    quantity_unit    TEXT NOT NULL,        -- shares, satoshi, gwei
    quantity_divisor BIGINT NOT NULL       -- 1 for equities, 100000000 for BTC, 1000000000 for ETH
);

-- ============================================================
-- Table: positions
-- ============================================================
CREATE TABLE positions (
    account_id    TEXT NOT NULL REFERENCES accounts(account_id),
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    position_quantity BIGINT NOT NULL,
    PRIMARY KEY (account_id, instrument_id)
);

-- ============================================================
-- Table: market_state
-- ============================================================
CREATE TABLE market_state (
    instrument_id        TEXT PRIMARY KEY REFERENCES instruments(instrument_id),
    reference_price_cents BIGINT NOT NULL,
    market_session_open  INTEGER NOT NULL   -- 0 = closed, 1 = open
);

-- ============================================================
-- Table: order_proposals
-- Each row is a scenario-specific proposal with all evidence fields.
-- Pre-computed fields are derived inline using integer arithmetic.
-- ============================================================
CREATE TABLE order_proposals (
    order_ref                    TEXT PRIMARY KEY,
    account_id                   TEXT NOT NULL REFERENCES accounts(account_id),
    instrument_id                TEXT NOT NULL REFERENCES instruments(instrument_id),
    side                         TEXT NOT NULL,
    order_type                   TEXT NOT NULL,
    quantity                     BIGINT NOT NULL,
    limit_price_cents            BIGINT NOT NULL DEFAULT 0,
    -- Risk system fields
    trader_authority_cents       BIGINT NOT NULL,
    desk_exposure_cents          BIGINT NOT NULL,
    desk_limit_cents             BIGINT NOT NULL,
    price_tolerance_bps          BIGINT NOT NULL,
    margin_requirement_cents     BIGINT NOT NULL,
    available_margin_cents       BIGINT NOT NULL,
    authorization_valid_seconds  INTEGER NOT NULL DEFAULT 300,
    -- Compliance system fields
    mandate_match                INTEGER NOT NULL DEFAULT 1,
    concentration_bps_after      BIGINT NOT NULL DEFAULT 500,
    concentration_limit_bps      BIGINT NOT NULL DEFAULT 1500,
    projected_position_quantity  BIGINT NOT NULL DEFAULT 0,
    position_limit_quantity      BIGINT NOT NULL DEFAULT 100000,
    last_opposite_side_seconds   BIGINT NOT NULL DEFAULT 86400,
    cooldown_window_seconds      BIGINT NOT NULL DEFAULT 600,
    restricted_list_hit          INTEGER NOT NULL DEFAULT 0,
    -- Scenario metadata (not sent to boundaries)
    scenario_group               TEXT NOT NULL,
    scenario_label               TEXT NOT NULL
);


-- ============================================================
-- Seed data: accounts
-- ============================================================
INSERT INTO accounts VALUES
    ('ACC-ALPHA-1',      'active',    200000000),   -- $2M buying power
    ('ACC-ALPHA-2',      'active',    500000000),   -- $5M buying power
    ('ACC-BETA-1',       'active',     50000000),   -- $500K buying power
    ('ACC-SUSPENDED-1',  'suspended', 100000000),   -- suspended account
    ('ACC-THIN-1',       'active',      2000000),   -- $20K buying power (thin margin)
    ('ACC-DESK-B',       'active',    200000000);   -- $2M buying power (desk B)

-- ============================================================
-- Seed data: instruments
-- HALBT is a fictional ticker for restricted-security scenarios.
-- ============================================================
INSERT INTO instruments VALUES
    ('INST-AAPL',   'AAPL',     'equity', 'shares',  1),
    ('INST-MSFT',   'MSFT',     'equity', 'shares',  1),
    ('INST-TSLA',   'TSLA',     'equity', 'shares',  1),
    ('INST-BTCUSD', 'BTC/USD',  'crypto', 'satoshi', 100000000),
    ('INST-ETHUSD', 'ETH/USD',  'crypto', 'gwei',    1000000000),
    ('INST-HALBT',  'HALBT',    'equity', 'shares',  1),
    ('INST-DOGEUSD','DOGE/USD', 'crypto', 'satoshi', 100000000),
    ('INST-SOLUSD', 'SOL/USD',  'crypto', 'satoshi', 100000000);

-- ============================================================
-- Seed data: positions
-- ============================================================
INSERT INTO positions VALUES
    ('ACC-ALPHA-1', 'INST-AAPL',   500),
    ('ACC-ALPHA-1', 'INST-MSFT',   300),
    ('ACC-ALPHA-1', 'INST-BTCUSD', 200000000),  -- 2 BTC in satoshi
    ('ACC-ALPHA-1', 'INST-ETHUSD', 5000000000), -- 5 ETH in gwei
    ('ACC-ALPHA-2', 'INST-AAPL',   1000),
    ('ACC-BETA-1',  'INST-TSLA',   200),
    ('ACC-DESK-B',  'INST-AAPL',   800);

-- ============================================================
-- Seed data: market_state
-- ============================================================
INSERT INTO market_state VALUES
    ('INST-AAPL',    22700, 1),     -- $227.00, market open
    ('INST-MSFT',    42500, 1),     -- $425.00, market open
    ('INST-TSLA',    25000, 1),     -- $250.00, market open
    ('INST-BTCUSD',  6800000, 1),   -- $68,000.00, always open (crypto)
    ('INST-ETHUSD',  350000, 1),    -- $3,500.00, always open (crypto)
    ('INST-HALBT',   5000, 1),      -- $50.00, market open
    ('INST-DOGEUSD', 1500, 1),      -- $15.00, always open (crypto)
    ('INST-SOLUSD',  15000, 1);     -- $150.00, always open (crypto)


-- ============================================================
-- Seed data: order_proposals
-- Group A: Equity Admissibility (5 scenarios)
-- ============================================================
INSERT INTO order_proposals VALUES
    -- A1: Clean equity buy -- all valid, market open, sufficient buying power
    ('ORD-A1', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'A', 'Clean equity buy'),

    -- A2: Account suspended
    ('ORD-A2', 'ACC-SUSPENDED-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'A', 'Account suspended'),

    -- A3: Exceeds buying power (buy)
    ('ORD-A3', 'ACC-THIN-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'A', 'Exceeds buying power'),

    -- A4: Market closed (equity)
    ('ORD-A4', 'ACC-ALPHA-1', 'INST-MSFT', 'buy', 'limit', 50, 42500,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 50, 100000, 86400, 600, 0,
     'A', 'Market closed for equity'),

    -- A5: Equity sell within position
    ('ORD-A5', 'ACC-ALPHA-1', 'INST-AAPL', 'sell', 'limit', 200, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 300, 100000, 86400, 600, 0,
     'A', 'Equity sell within position');


-- ============================================================
-- Group B: Crypto Admissibility (3 scenarios)
-- ============================================================
INSERT INTO order_proposals VALUES
    -- B1: Clean crypto buy
    ('ORD-B1', 'ACC-ALPHA-1', 'INST-BTCUSD', 'buy', 'limit', 50000000, 6800000,
     500000000, 40000000, 500000000, 500, 5000000, 50000000, 300,
     1, 500, 1500, 50000000, 500000000, 86400, 600, 0,
     'B', 'Clean crypto buy'),

    -- B2: Crypto sell exceeds position
    ('ORD-B2', 'ACC-ALPHA-1', 'INST-BTCUSD', 'sell', 'limit', 500000000, 6800000,
     500000000, 40000000, 500000000, 500, 5000000, 50000000, 300,
     1, 500, 1500, 0, 500000000, 86400, 600, 0,
     'B', 'Crypto sell exceeds position'),

    -- B3: Crypto during equity market close
    ('ORD-B3', 'ACC-ALPHA-1', 'INST-ETHUSD', 'buy', 'limit', 2000000000, 350000,
     500000000, 40000000, 500000000, 500, 5000000, 50000000, 300,
     1, 500, 1500, 2000000000, 5000000000, 86400, 600, 0,
     'B', 'Crypto during equity market close');


-- ============================================================
-- Group C: Compliance (6 scenarios)
-- ============================================================
INSERT INTO order_proposals VALUES
    -- C1: Clean compliance pass
    ('ORD-C1', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'C', 'Clean compliance pass'),

    -- C2: Restricted security (prohibition)
    ('ORD-C2', 'ACC-ALPHA-1', 'INST-HALBT', 'buy', 'limit', 100, 5000,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 1,
     'C', 'Restricted security'),

    -- C3: Exceeds concentration limit
    ('ORD-C3', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 1600, 1500, 100, 100000, 86400, 600, 0,
     'C', 'Exceeds concentration limit'),

    -- C4: Exceeds position limit
    ('ORD-C4', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100001, 100000, 86400, 600, 0,
     'C', 'Exceeds position limit'),

    -- C5: Opposite-side cooldown violation
    ('ORD-C5', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 300, 600, 0,
     'C', 'Opposite-side cooldown violation'),

    -- C6: Negative concentration limit (domain validity)
    ('ORD-C6', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, -100, 100, 100000, 86400, 600, 0,
     'C', 'Negative concentration limit');


-- ============================================================
-- Group D: Execution Authorization (5 scenarios)
-- ============================================================
INSERT INTO order_proposals VALUES
    -- D1: Clean authorization (limit order)
    ('ORD-D1', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'D', 'Clean authorization'),

    -- D2: Exceeds trader authority
    ('ORD-D2', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     3000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'D', 'Exceeds trader authority'),

    -- D3: Exceeds desk exposure limit
    ('ORD-D3', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 97000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'D', 'Exceeds desk exposure limit'),

    -- D4: Price beyond tolerance
    ('ORD-D4', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 24000,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'D', 'Price beyond tolerance'),

    -- D5: Insufficient margin
    ('ORD-D5', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 40000000, 100000000, 500, 6000000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'D', 'Insufficient margin');


-- ============================================================
-- Group E: Execution Reconciliation (5 scenarios)
-- These are run by demo.py, not by seed data directly.
-- E1/E2 use live Alpaca data with --live.
-- E3-E5 use simulated evidence constructed in demo.py.
-- We include the base authorization proposals here.
-- ============================================================
INSERT INTO order_proposals VALUES
    -- E1: Full fill, conformant (live with --live)
    ('ORD-E1', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 10, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 10, 100000, 86400, 600, 0,
     'E', 'Full fill conformant'),

    -- E2: Partial fill, conformant (live with --live)
    ('ORD-E2', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 50, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 50, 100000, 86400, 600, 0,
     'E', 'Partial fill conformant'),

    -- E3: Over-execution (simulated)
    ('ORD-E3', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'E', 'Over-execution simulated'),

    -- E4: Price slippage exceeds ceiling (simulated)
    ('ORD-E4', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'E', 'Price slippage exceeds ceiling'),

    -- E5: Authorization identity mismatch (simulated)
    ('ORD-E5', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'E', 'Authorization identity mismatch');


-- ============================================================
-- F-scenario base proposals
-- F1-F17 architectural and operational demos.
-- Many F scenarios construct modified evidence in demo.py;
-- these rows provide the base authorization proposals.
-- ============================================================
INSERT INTO order_proposals VALUES
    -- F1: Expired authorization
    ('ORD-F1', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 30,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'F', 'Expired authorization'),

    -- F2: Duplicate execution event (base)
    ('ORD-F2', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'F', 'Duplicate execution event'),

    -- F3: Cumulative over-execution and regression (base)
    ('ORD-F3', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'F', 'Cumulative over-execution and regression'),

    -- F4: Parameter mutation
    ('ORD-F4', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'F', 'Parameter mutation'),

    -- F5: Centerpiece -- autonomous strategy governance
    --     Run 1: authorized (ample desk capacity)
    ('ORD-F5-R1', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 40000000, 200000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'F', 'Autonomous strategy - authorized'),

    --     Run 2: blocked (desk capacity consumed)
    ('ORD-F5-R2', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 197000000, 200000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'F', 'Autonomous strategy - blocked'),

    -- F7: Boundary version change (base -- authorized, then version changes)
    ('ORD-F7', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'F', 'Boundary version change'),

    -- F8: Forged B2 lineage (B2 uses different account)
    ('ORD-F8-B1B3', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'F', 'Forged B2 lineage - real order'),

    ('ORD-F8-B2', 'ACC-ALPHA-2', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'F', 'Forged B2 lineage - different account'),

    -- F9: Notional inconsistency (quantity=1000, price=$200, but notional claims $10K)
    ('ORD-F9', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 1000, 20000,
     25000000, 40000000, 100000000, 500, 500000, 25000000, 300,
     1, 500, 1500, 1000, 100000, 86400, 600, 0,
     'F', 'Notional inconsistency'),

    -- F10: Ambiguous broker submission (UNCERTAIN recovery)
    ('ORD-F10', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 10, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 10, 100000, 86400, 600, 0,
     'F', 'Ambiguous broker submission'),

    -- F11: Concurrent authorization (Strategy Alpha)
    ('ORD-F11-ALPHA', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 265, 22700,
     100000000, 40000000, 100000000, 500, 500000, 100000000, 300,
     1, 500, 1500, 265, 100000, 86400, 600, 0,
     'F', 'Concurrent authorization - Alpha'),

    -- F11: Concurrent authorization (Strategy Beta)
    ('ORD-F11-BETA', 'ACC-DESK-B', 'INST-AAPL', 'buy', 'limit', 265, 22700,
     100000000, 40000000, 100000000, 500, 500000, 100000000, 300,
     1, 500, 1500, 265, 100000, 86400, 600, 0,
     'F', 'Concurrent authorization - Beta'),

    -- F12: Stale evidence (simulated market order, reference price stale)
    ('ORD-F12', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'market', 500, 0,
     100000000, 40000000, 100000000, 500, 500000, 100000000, 300,
     1, 500, 1500, 500, 100000, 86400, 600, 0,
     'F', 'Stale evidence market order'),

    -- F13: Outbox crash recovery (base)
    ('ORD-F13', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 50, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 50, 100000, 86400, 600, 0,
     'F', 'Outbox crash recovery'),

    -- F14: Price-only broker correction (base)
    ('ORD-F14', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'F', 'Price-only broker correction'),

    -- F15: UNCERTAIN recovery without auto-ABANDONED (base)
    ('ORD-F15', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 10, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 10, 100000, 86400, 600, 0,
     'F', 'UNCERTAIN recovery'),

    -- F16: Concurrent dispatch same B3 (base)
    ('ORD-F16', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 100, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 100, 100000, 86400, 600, 0,
     'F', 'Concurrent dispatch same B3'),

    -- F17: Cross-chain order type mismatch (B1 = market, B3 = limit)
    ('ORD-F17-MKT', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'market', 150, 0,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'F', 'Cross-chain order type mismatch - market'),

    ('ORD-F17-LMT', 'ACC-ALPHA-1', 'INST-AAPL', 'buy', 'limit', 150, 22700,
     5000000, 40000000, 100000000, 500, 500000, 5000000, 300,
     1, 500, 1500, 150, 100000, 86400, 600, 0,
     'F', 'Cross-chain order type mismatch - limit');
