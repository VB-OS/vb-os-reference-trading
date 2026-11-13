# VB-OS Trading Reference Integration

**Author:** [Asaad Riaz](https://github.com/anriaz) -- Founder, MNC Labs, Inc.
**Platform:** [VB-OS Cloud](https://vbos.cloud) | [Documentation](https://vb-os.org) | [GitHub](https://github.com/VB-OS)

---

## The Problem

Every trading firm has compliance checks, risk limits, and authorization
workflows. But in practice, these controls are scattered across systems --
an OMS checks one thing, a compliance engine checks another, a risk system
checks a third -- and no one can answer a simple question after the fact:
**was this trade fully authorized, by whom, on what evidence, and can you
prove it?**

When regulators ask, when audits happen, when something goes wrong, the
answer is a manual reconstruction from logs across multiple systems. There is
no sealed, tamper-evident record that captures the full authorization chain
from order proposal to execution reconciliation. If a trader exceeded their
desk limit, if a restricted security slipped through, if an expired
authorization was used -- the gap may not surface until weeks or months later.

## What VB-OS Does Differently

VB-OS creates a **deterministic governance pipeline** where every trade passes
through a sequence of boundary evaluations before it can execute. Each boundary
checks a specific dimension -- account eligibility, compliance rules, execution
authority, post-trade reconciliation -- and produces a sealed, replayable
decision:

- **ASSERT** means the evidence satisfied every condition. The trade can proceed.
- **DEFER** means something failed. The trade is blocked, and the record says
  exactly which condition failed, what evidence was missing, and what policy
  was in effect.

Every decision is sealed with full provenance: what evidence was submitted,
from which source system, at what version, at what time. Six months later,
you can replay the exact same evaluation and get the exact same result. This
is not a log or an audit trail -- it is a cryptographically deterministic
record of the governance decision.

## What This Integration Demonstrates

This repository is a working reference implementation of a governed trading
pipeline with four stages:

1. **Order Admissibility** -- Is the account active? Does the trader have
   buying power? Is the market open? Is the instrument eligible?
2. **Compliance Authorization** -- Does the order match the investment mandate?
   Is concentration within limits? Is the security on the restricted list?
3. **Execution Authorization** -- Does the trader have authority for this
   size? Is desk exposure within limits? Is the price within tolerance?
4. **Execution Reconciliation** -- After the trade executes, does the fill
   match what was authorized? Is the price within the ceiling? Does the
   execution identity match?

Each stage gates the next -- a DEFER at any boundary halts the pipeline.
The integration includes 42 scenarios covering clean trades, account
suspensions, compliance violations, expired authorizations, post-trade
reconciliation failures, and architectural edge cases like duplicate
execution events and concurrent authorization races.

It connects to **Alpaca paper trading** for live order submission, so you
can see the full cycle: governance evaluation, real broker execution, and
post-trade reconciliation -- all with sealed VB-OS records.

---

VB-OS holds no trading or risk authority. It verifies provenance, not truth:
was this order authorized, on what evidence, acquired from where, and can it be
proven six months from now. The evaluation engine is integer-only, deterministic,
and replay-safe -- the same input artifact, boundary snapshot, and binary version
always produce the same decision.

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

## About VB-OS

[VB-OS](https://vbos.cloud) is a verification-as-a-service platform that
provides deterministic, auditable governance for regulated workflows. Built by
[MNC Labs, Inc.](https://mnclabs.com), VB-OS evaluates evidence against
boundary definitions using an integer-only Rust binary, producing sealed
evaluation records with full provenance and replay determinism.

The platform supports any domain where decisions must be verifiable after the
fact: financial trading, healthcare credentialing, supply chain compliance,
and autonomous agent governance.

- **Console:** [vbos.cloud](https://vbos.cloud)
- **API:** [api.vb-os.org](https://api.vb-os.org)
- **Documentation:** [vb-os.org](https://vb-os.org)
- **GitHub:** [github.com/VB-OS](https://github.com/VB-OS)

## License

Copyright 2026 MNC Labs, Inc. All rights reserved.

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for
the full text.
