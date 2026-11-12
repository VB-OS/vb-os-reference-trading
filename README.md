# VB-OS Trading Reference Integration

Reference implementation demonstrating VB-OS execution authority governance
for financial trading workflows across equities and crypto.

## What This Demonstrates

A four-boundary governance pipeline evaluates every order through 45 predicates
before any trade reaches a venue:

| Boundary | Predicates | Purpose |
|----------|-----------|---------|
| B1 Order Admissibility | 10 | Account status, buying power, market session, position coverage |
| B2 Compliance Authorization | 10 | Mandate, concentration, position limits, restricted securities, cooldown |
| B3 Execution Authorization | 16 | Trader authority, desk exposure, price tolerance, margin, authorization lifetime |
| B4 Execution Reconciliation | 9 | Post-trade: fill monotonicity, quantity authority, price ceiling, identity match |

The execution gateway independently verifies every sealed B3 authorization
before submitting to Alpaca, enforcing three operations:

1. **Authorization chain integrity** -- all three upstream evaluations ASSERT,
   material order fields match across the chain
2. **Arithmetic consistency** -- pre-computed notional, deviation, and threshold
   fields are re-derived and compared
3. **Submission binding** -- the proposed order matches the sealed authorization

## Architecture

```
Order Proposal
    |
    v
[B1 Admissibility] --> DEFER (stop) or ASSERT -->
[B2 Compliance]    --> DEFER (stop) or ASSERT -->
[B3 Authorization] --> DEFER (stop) or ASSERT -->
[Gateway]          --> verify -> claim -> submit -> reconcile
    |
    v
Alpaca Paper Trading (--live mode)
    |
    v
[B4 Reconciliation] <-- transactional outbox
```

## Quick Start

```bash
# 1. Copy and fill environment variables
cp .env.example .env
# Edit .env with your VB-OS Cloud credentials

# 2. Provision VB-OS Cloud boundaries, connectors, and flows
docker compose run --rm app bootstrap.py

# 3. List all scenarios
docker compose run --rm app demo.py --list

# 4. Run a single scenario
docker compose run --rm app demo.py --run A1

# 5. Run all governance scenarios
docker compose run --rm app demo.py --all

# 6. Run with live Alpaca paper trading
docker compose run --rm app demo.py --live E1

# 7. Run verification suite
docker compose run --rm app verify.py --all

# 8. Run timing benchmark
docker compose run --rm app demo.py --benchmark
```

## Scenarios

### Governance (A-E)

- **A1-A5**: Equity admissibility (clean, suspended, buying power, market closed, sell)
- **B1-B3**: Crypto admissibility (clean, exceeds position, session exemption)
- **C1-C6**: Compliance (clean, restricted, concentration, position, cooldown, domain)
- **D1-D5**: Execution authorization (clean, authority, desk limit, price, margin)
- **E1-E5**: Reconciliation (full fill, partial, over-execution, slippage, identity)

### Architectural (F-series)

- **F1**: Expired authorization
- **F2**: Duplicate execution event deduplication
- **F3**: Cumulative over-execution and regression detection
- **F4**: Parameter mutation (submission binding)
- **F5**: Autonomous strategy governance (centerpiece)
- **F7**: Boundary version staleness
- **F8**: Forged B2 lineage (cross-chain material mismatch)
- **F9**: Notional inconsistency (arithmetic verification)
- **F10**: UNCERTAIN broker recovery
- **F11**: Concurrent authorization (parallel pipeline race)
- **F12**: Stale market order evidence
- **F13**: Transactional outbox crash recovery
- **F14**: Price-only broker correction
- **F15**: UNCERTAIN terminal state resolution
- **F16**: Authorization consumption (exactly-once dispatch)
- **F17**: Cross-chain order type mismatch

## Files

| File | Purpose |
|------|---------|
| `boundaries/*.dsl` | VB-OS boundary definitions (VBL v2) |
| `seed.sql` | PostgreSQL schema and seed data for all scenarios |
| `seed_template.py` | Register template in VB-OS Cloud template registry |
| `bootstrap.py` | Idempotent VB-OS Cloud provisioning (boundaries, connectors, flows) |
| `execution_gateway.py` | Persistent gateway with SQLite state, verification, Alpaca submission |
| `demo.py` | Scenario orchestration and pipeline runner |
| `verify.py` | Verification suite (parity, integrity, counterfactual, expiration) |

## Integer Arithmetic

All monetary values are in cents (BIGINT). All proportions are in basis points.
No floating-point arithmetic anywhere in the evaluation pipeline.

- Buy-limit notional: `(quantity * limit_price_cents + divisor - 1) // divisor`
  (ceiling division, strict upper bound on gross execution cost)
- Sell-limit / market notional: `quantity * price_cents // divisor` (floor)
- Price deviation: `(limit - reference) * 10000` (scaled to avoid division)
- Tolerance threshold: `tolerance_bps * reference_price_cents`

## License

Apache 2.0. Copyright 2026 MNC Labs, Inc.
