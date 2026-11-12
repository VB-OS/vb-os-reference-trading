# VB-OS Trading Reference Integration

A reference implementation demonstrating governed financial trading workflows
through [VB-OS Cloud](https://vb-os.org). This integration exercises all four
governance boundaries -- order admissibility (B1), compliance authorization (B2),
execution authorization (B3), and execution reconciliation (B4) -- with 45
predicates evaluated before any trade reaches a venue.

VB-OS holds no trading or risk authority. It verifies provenance, not truth:
was this order authorized, on what evidence, acquired from where, and can it be
proven six months from now.

## Quick Start

See [SETUP.md](SETUP.md) for complete setup instructions -- from forking the
template in VB-OS Cloud to running the full integration locally.

### Docker (recommended)

After completing VB-OS Cloud setup (SETUP.md Part 1) and creating your `.env`:

```bash
docker compose --profile app build
docker compose up -d
docker compose --profile app run --rm trading-app demo.py --pipeline
```

No local Python installation required. See SETUP.md Part 6 for details.

## Architecture

```
Order Proposal (PostgreSQL)
    |
    v
[B1 Order Admissibility]  --> DEFER (stop) or ASSERT -->
[B2 Compliance Auth]       --> DEFER (stop) or ASSERT -->
[B3 Execution Auth]        --> DEFER (stop) or ASSERT -->
[Execution Gateway]        --> verify -> claim -> submit -> reconcile
    |
    v
Alpaca Paper Trading (--live mode)
    |
    v
[B4 Execution Reconciliation] <-- transactional outbox
```

## Acquisition Modes

All four connectors use **caller-supplied payload**: the local scripts read
from the trading database (accounts, instruments, market state), build evidence,
and push it to VB-OS Cloud. VB-OS extracts evidence, evaluates the boundary,
and records the provenance. The platform never touches the source system
directly.

## Boundaries

| Boundary | Predicates | Purpose |
|----------|-----------|---------|
| B1 Order Admissibility | 10 | Account status, buying power, market session, position coverage |
| B2 Compliance Authorization | 10 | Mandate, concentration, position limits, restricted securities, cooldown |
| B3 Execution Authorization | 16 | Trader authority, desk exposure, price tolerance, margin, authorization lifetime |
| B4 Execution Reconciliation | 9 | Post-trade: fill monotonicity, quantity authority, price ceiling, identity match |

## Scenarios

### Governance (A-E)

| Group | Scenarios | Focus |
|-------|-----------|-------|
| A1-A5 | Equity admissibility | Clean, suspended, buying power, market closed, sell |
| B1-B3 | Crypto admissibility | Clean, exceeds position, session exemption |
| C1-C6 | Compliance | Clean, restricted, concentration, position, cooldown, domain |
| D1-D5 | Execution authorization | Clean, authority, desk limit, price, margin |
| E1-E5 | Reconciliation | Full fill, partial, over-execution, slippage, identity |

### Architectural (F-series)

| Scenario | Demonstration |
|----------|---------------|
| F1 | Expired authorization |
| F2 | Duplicate execution event deduplication |
| F3 | Cumulative over-execution and regression detection |
| F4 | Parameter mutation (submission binding) |
| F5 | Autonomous strategy governance (centerpiece) |
| F7 | Boundary version staleness |
| F8 | Forged B2 lineage (cross-chain material mismatch) |
| F9 | Notional inconsistency (arithmetic verification) |
| F10 | UNCERTAIN broker recovery |
| F11 | Concurrent authorization (parallel pipeline race) |
| F12 | Stale market order evidence |
| F13 | Transactional outbox crash recovery |
| F14 | Price-only broker correction |
| F15 | UNCERTAIN terminal state resolution |
| F16 | Authorization consumption (exactly-once dispatch) |
| F17 | Cross-chain order type mismatch |

Run `python3 demo.py --list` for descriptions, or `python3 demo.py --all` to
run all governance scenarios. `python3 demo.py --pipeline` runs the full
B1 -> B2 -> B3 -> Gateway -> B4 pipeline end-to-end.

## Verification Suite

`verify.py` contains four deterministic verification tests:

| Test | Proves |
|------|--------|
| Parity | Replay every sealed evaluation, confirm decision parity |
| Integrity | Re-derive B3 pre-computed fields, compare to sealed values |
| Counterfactual | Clean B1 ASSERT, then suspended account DEFER |
| Expiration | Full pipeline with short validity, wait, verify gateway rejects |

## Integer Arithmetic

All monetary values are in cents (BIGINT). All proportions are in basis points.
No floating-point arithmetic anywhere in the evaluation pipeline.

- Buy-limit notional: `(quantity * limit_price_cents + divisor - 1) // divisor`
  (ceiling division, strict upper bound on gross execution cost)
- Sell-limit / market notional: `quantity * price_cents // divisor` (floor)
- Price deviation: `(limit - reference) * 10000` (scaled to avoid division)
- Tolerance threshold: `tolerance_bps * reference_price_cents`

## Directory Structure

```
vb-os-reference-trading/
├── SETUP.md               # Complete setup guide
├── Dockerfile             # Container image for integration scripts
├── docker-compose.yml     # PostgreSQL + gateway + app container
├── requirements.txt       # Pinned Python dependencies
├── demo.py                # Scenario runner (--run, --all, --pipeline, --live)
├── verify.py              # Deterministic verification suite
├── execution_gateway.py   # Authorization verification, Alpaca submission, B4 outbox
├── bootstrap.py           # (Optional) Standalone VB-OS provisioning
├── seed_template.py       # (Admin) Register template in template registry
├── seed.sql               # PostgreSQL schema and seed data for all scenarios
├── manifest.json          # Machine-readable integration metadata
├── .env.example           # Environment variable template
├── vendor/                # Vendored VB-OS Python SDK (not yet on PyPI)
│   └── vb_os-0.1.0.tar.gz
└── boundaries/            # VB-OS DSL boundary definitions (VBL v2)
    ├── B_ORDER_ADMISSIBILITY.dsl
    ├── B_COMPLIANCE_AUTHORIZATION.dsl
    ├── B_EXECUTION_AUTHORIZATION.dsl
    └── B_EXECUTION_RECONCILIATION.dsl
```

## License

Copyright 2026 MNC Labs, Inc. Licensed under the Apache License, Version 2.0.
See [LICENSE](LICENSE) for the full text.
