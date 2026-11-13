# VB-OS Trading Reference Integration -- Setup Guide

**Author:** [Asaad Riaz](https://github.com/anriaz) -- Founder, MNC Labs, Inc.
**Repository:** [github.com/VB-OS/vb-os-reference-trading](https://github.com/VB-OS/vb-os-reference-trading)
**Platform:** [VB-OS Cloud](https://vbos.cloud)

---

This guide walks you through setting up and running the governed financial
trading pipeline. By the end, you will have a working integration that
evaluates trade orders across four governance stages -- admissibility,
compliance, execution authorization, and post-trade reconciliation -- with
every decision sealed and replayable through VB-OS Cloud.

See [README.md](README.md) for the full problem statement and what this
integration demonstrates.

---

## Prerequisites

- **Python 3.11+**
- **Docker Desktop** (running)
- **Git**
- **Alpaca Paper Trading account** (optional) -- only needed for `--live` mode,
  which submits real paper orders. Sign up at https://alpaca.markets.

---

## Part 1: VB-OS Cloud Setup

### 1.1 Log in to the VB-OS Console

Open https://vbos.cloud and sign in.

### 1.2 Fork the Trading Order Governance template

1. In the sidebar, click **Registry**.
2. Find **"Trading Order Governance"**.
3. Click **Fork Template** and give the project a name (e.g. "Trading Evaluation").

This creates a fully provisioned project with:
- 4 boundaries (B1, B2, B3, B4) -- approved and deployed
- 4 connectors -- pre-configured with evidence mappings
- 4 governance flows -- approved, active, and deployed
- 1 certification model

### 1.3 Copy the Project ID and Environment ID

Inside the new project:

1. Go to **Settings** (in the project sub-navigation). Under the **General**
   tab, copy the **Project ID**.
2. Switch to the **Environments** tab. Copy the **Environment ID** for the
   "Development" environment created by the fork.

You will need both in your `.env` file.

### 1.4 Create an API key

Inside the project, go to **Integrate** and select the **API Keys** tab.
Select the environment from the dropdown, then click **Create Key**.

**Select all scopes.** This is a reference integration that exercises the
full platform surface -- boundaries, connectors, flows, evaluations,
certifications, and replay. Copy the key immediately; it is shown only once.

---

## Part 2: Local Environment Setup

### 2.1 Clone and enter the repository

```bash
git clone https://github.com/VB-OS/vb-os-reference-trading.git
cd vb-os-reference-trading
```

### 2.2 Create a virtual environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install vendor/vb_os-0.1.0.tar.gz
```

This creates an isolated Python environment, installs pinned dependencies
(psycopg2 for PostgreSQL access), and installs the VB-OS Python SDK from
the vendored distribution. `demo.py` uses the SDK; all other scripts
(`bootstrap.py`, `verify.py`, `execution_gateway.py`) use the standard
library only.

Every time you open a new terminal to run scripts, activate the environment
first:

```bash
source .venv/bin/activate
```

### 2.3 Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in the three values from Part 1:

```
VBOS_URL=https://api.vb-os.org
VBOS_API_KEY=<your API key from step 1.4>
VBOS_PROJECT_ID=<your project ID from step 1.3>
VBOS_ENVIRONMENT_ID=<your environment ID from step 1.3>
```

Leave all other values at their defaults -- they configure the local
PostgreSQL database and execution gateway that ship with this repository.

### 2.4 Start the local source systems

```bash
docker compose up -d
```

This starts two containers:
- **PostgreSQL** (OMS database) at `localhost:5432` -- user `trading`, database `trading`
- **Execution Gateway** at `http://localhost:9093` -- verifies B3 authorization
  and submits orders to Alpaca

The PostgreSQL container is seeded automatically from `seed.sql` on first start,
creating the schema (accounts, instruments, market_state, positions,
order_proposals) and all scenario data.

Verify both are running:

```bash
docker compose ps
```

Both should show `running` (healthy) status.

### 2.5 Verify the setup

At this point, everything is ready:

- **VB-OS Cloud** has your project with 4 boundaries, 4 connectors, 4 flows,
  and 1 certification model -- all provisioned by the template fork.
- **Local source systems** (PostgreSQL + execution gateway) are running with
  seeded data for all 42 scenarios.
- **Environment variables** are set in `.env`.

---

## Part 3: Run the Integration

All commands below require the environment to be exported:

```bash
set -a; source .env; set +a
```

### 3.1 List available scenarios

```bash
python3 demo.py --list
```

Shows all 24 governance scenarios (A-E groups) and optionally 18 architectural
demos (F-series) with `--include-f`.

### 3.2 Run individual scenarios

```bash
python3 demo.py --scenario A1    # B1: Clean equity buy -> ASSERT
python3 demo.py --scenario A2    # B1: Account suspended -> DEFER
python3 demo.py --scenario C2    # B2: Restricted security -> DEFER
python3 demo.py --scenario D1    # B3: Clean authorization -> ASSERT
python3 demo.py --scenario E1    # Full pipeline + gateway
```

Each scenario reads the order proposal from the local PostgreSQL database,
derives evidence, and submits the payload to VB-OS Cloud. VB-OS extracts
evidence, evaluates the boundary, and returns the decision with predicate
results and provenance.

### 3.3 Run scenario groups

```bash
python3 demo.py --scenario A     # All A-group (equity admissibility)
python3 demo.py --scenario C     # All C-group (compliance)
```

### 3.4 Run all scenarios

```bash
python3 demo.py --all                  # All A-E scenarios
python3 demo.py --all --include-f      # All A-E + F-series
```

Runs scenarios in sequence and reports the pass count.

### 3.5 Run the full B1 -> B2 -> B3 -> Gateway -> B4 pipeline

```bash
python3 demo.py --pipeline
```

Executes the complete governed trading workflow end-to-end on the E1 (clean
equity buy) scenario:

1. **B1 Order Admissibility** -- reads the order proposal from PostgreSQL,
   builds evidence (account status, buying power, market session), submits
   to VB-OS Cloud. On ASSERT, proceeds.
2. **B2 Compliance Authorization** -- builds compliance evidence (mandate
   match, concentration, position limits, cooldown), submits to VB-OS Cloud.
   On ASSERT, proceeds.
3. **B3 Execution Authorization** -- builds execution evidence (trader
   authority, desk exposure, price tolerance, margin, prior evaluation IDs),
   submits to VB-OS Cloud. On ASSERT, proceeds.
4. **Gateway** -- the execution gateway reads the sealed B3 evaluation from
   VB-OS Cloud, verifies the authorization chain (B1+B2+B3 all ASSERT,
   material identity match, arithmetic consistency, submission binding,
   expiration check), and submits the order.
5. **B4 Execution Reconciliation** -- simulated post-trade evidence
   (executed quantity, price, authorization identity) submitted to VB-OS
   Cloud for conformance verification.

Each stage gates the next -- a DEFER at any boundary halts the pipeline.

### 3.6 Run with live Alpaca paper trading

```bash
python3 demo.py --live E1
```

Requires `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in `.env`. The gateway
verifies the B3 authorization and submits a real paper order to Alpaca.

---

## Part 4: Verify Results on the Platform

After running the demo scenarios, open the VB-OS Console at
https://vbos.cloud to see what the platform recorded. Sign in and
navigate to the project you created in Part 1.

### Overview

The project landing page shows a summary of recent activity: total
evaluations, ASSERT/DEFER split, and boundary health. After running
`--all`, you should see evaluations for all four boundaries.

### Monitor

Click **Monitor** in the project sub-navigation. This is the live
evaluation feed. Each row shows:

- **Decision** -- ASSERT (green) or DEFER (amber)
- **Boundary** -- which boundary was evaluated
- **Timestamp** -- when the evaluation occurred
- **Acquisition class** -- `caller_supplied_payload` for all four boundaries

You should see entries for every scenario you ran. ASSERT results
confirm the workload passed all predicates. DEFER results show which
predicate failed.

### Evaluation Detail

Click any evaluation row to open the detail view. This shows:

- **Evidence** -- the exact evidence fields VB-OS extracted from the
  raw payload (account status, notional, buying power, etc.)
- **Predicate results** -- each named predicate and whether it passed
  or failed
- **Provenance** -- how the evidence was acquired (connector ID,
  acquisition class, timestamp)
- **Failure reasons** -- for DEFER results, the specific predicate
  that failed or the prohibition that was violated

### Replay

From the evaluation detail view, click **Replay**. VB-OS re-evaluates
the same input artifact against the same boundary snapshot and binary
version. The replay result must match the original. This proves
deterministic reproducibility: given the same artifact, boundary, and
binary, the result is identical.

The replay view shows:

- **Original decision** and **Replay decision** -- these must match
- **Identity match** -- confirms the same binary and boundary snapshot
  were used
- **Input hash** -- proves the input artifact has not been modified

### What to Expect

| Scenario | Boundary | Expected Decision | What to Look For |
|----------|----------|-------------------|------------------|
| A1 | B1 Order Admissibility | ASSERT | All 10 predicates pass, clean equity buy |
| A2 | B1 Order Admissibility | DEFER | `account_is_active` fails (account suspended) |
| A3 | B1 Order Admissibility | DEFER | `buyer_has_buying_power` fails (insufficient funds) |
| A4 | B1 Order Admissibility | DEFER | `market_session_permits_order` fails (market closed) |
| C2 | B2 Compliance Authorization | DEFER | `restricted_list_hit` prohibition violated |
| D2 | B3 Execution Authorization | DEFER | `within_trader_authority` fails (exceeds limit) |
| E1 | B3 + Gateway | ASSERT | Full pipeline passes, gateway verified |

---

## Part 5: Verification Suite

```bash
python3 verify.py --all
```

Four deterministic verification tests that prove the governance pipeline
is working correctly. No live trading is needed.

| Test | Proves |
|------|--------|
| `--parity` | Replay every sealed evaluation, confirm decision parity |
| `--integrity` | Re-derive B3 pre-computed fields, compare to sealed values |
| `--counterfactual` | Clean B1 ASSERT, then suspended account DEFER |
| `--expiration` | Full pipeline with short validity, wait, verify gateway rejects |

Run individual tests:

```bash
python3 verify.py --parity
python3 verify.py --integrity
python3 verify.py --counterfactual
python3 verify.py --expiration
```

---

## Part 6: Running with Docker (Alternative)

Instead of installing Python and the SDK locally, you can run all scripts
inside a container. This requires only Docker Desktop and a configured `.env`
file (Parts 1 and 2.3).

### 6.1 Start source systems and build the app image

```bash
docker compose --profile app build
docker compose up -d
```

The first command builds the integration container image. The second starts
PostgreSQL and the execution gateway. Health checks ensure PostgreSQL is
ready before any app commands run.

### 6.2 Run scenarios

```bash
docker compose --profile app run --rm trading-app demo.py --list
docker compose --profile app run --rm trading-app demo.py --scenario A1
docker compose --profile app run --rm trading-app demo.py --all
```

### 6.3 Run the full pipeline

```bash
docker compose --profile app run --rm trading-app demo.py --pipeline
```

### 6.4 Run verification vectors

```bash
docker compose --profile app run --rm trading-app verify.py --all
```

### 6.5 Stop everything

```bash
docker compose --profile app down
```

---

## What Each Script Does

| Script | Purpose | Dependencies |
|--------|---------|--------------|
| `demo.py` | Runs individual scenarios and the full pipeline against real source systems | `vbos` SDK (vendored in `vendor/`) |
| `verify.py` | Four deterministic verification tests | Standard library only |
| `execution_gateway.py` | Authorization verification, Alpaca submission, B4 outbox | Standard library only |
| `bootstrap.py` | (Optional) Standalone provisioning of boundaries, connectors, flows, and certifications -- use if not forking the template | Standard library only |
| `seed_template.py` | (Admin) Register template in the platform template registry | `asyncpg` |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VBOS_URL` | Yes | VB-OS Cloud API URL. Default: `https://api.vb-os.org` |
| `VBOS_API_KEY` | Yes | API key from Part 1, step 1.4 |
| `VBOS_PROJECT_ID` | Yes | Project ID from Part 1, step 1.3 |
| `VBOS_ENVIRONMENT_ID` | Yes | Environment ID from Part 1, step 1.3 |
| `DATABASE_URL` | No | PostgreSQL connection string. Default: `postgresql://trading:trading@trading-postgres:5432/trading` |
| `GATEWAY_URL` | No | Execution gateway URL. Default: `http://trading-gateway:9093` |
| `ALPACA_API_KEY` | No | Alpaca paper trading API key. Only needed for `--live` mode. |
| `ALPACA_SECRET_KEY` | No | Alpaca paper trading secret key. Only needed for `--live` mode. |
| `ALPACA_BASE_URL` | No | Alpaca API base URL. Default: `https://paper-api.alpaca.markets` |

---

## Acquisition Mode

All four connectors use **caller-supplied payload**: the local scripts read
from the trading database (accounts, instruments, market state), build evidence,
and push it to VB-OS Cloud. VB-OS extracts evidence, evaluates the boundary,
and records the provenance. The platform never touches the source system
directly.

---

## Boundary DSL Source

The four boundary definitions live in `boundaries/` as VB-OS DSL source files:

| File | Boundary | Predicates |
|------|----------|------------|
| `B_ORDER_ADMISSIBILITY.dsl` | Order admissibility | 10 |
| `B_COMPLIANCE_AUTHORIZATION.dsl` | Compliance authorization | 10 |
| `B_EXECUTION_AUTHORIZATION.dsl` | Execution authorization | 16 |
| `B_EXECUTION_RECONCILIATION.dsl` | Execution reconciliation | 9 |

These are the source of truth. `bootstrap.py` syncs them to VB-OS Cloud on
every run.

---

## Integer Arithmetic

All monetary values are in cents (BIGINT). All proportions are in basis points.
No floating-point arithmetic anywhere in the evaluation pipeline.

- Buy-limit notional: `(quantity * limit_price_cents + divisor - 1) // divisor`
  (ceiling division, strict upper bound on gross execution cost)
- Sell-limit / market notional: `quantity * price_cents // divisor` (floor)
- Price deviation: `(limit - reference) * 10000` (scaled to avoid division)
- Tolerance threshold: `tolerance_bps * reference_price_cents`

---

## Troubleshooting

**demo.py fails with "HTTP 401"**
Your API key is invalid or expired. Create a new one under **Integrate >
API Keys** (step 1.4).

**demo.py fails with "HTTP 403"**
The API key is missing required scopes. Delete it and create a new one with
all scopes selected.

**"Connector 'trading-b1-evidence' not found"**
The template fork should have created all 4 connectors. Check your project
under **Integrate > Connectors**. If connectors are missing, re-fork the
template.

**Gateway connection refused**
The execution gateway runs inside Docker. Make sure containers are running:
`docker compose ps`. If the gateway is not running: `docker compose up -d`.

**PostgreSQL connection refused**
The trading database runs inside Docker. Check `docker compose ps`. If not
running: `docker compose up -d`. The database is seeded automatically from
`seed.sql` on first start.

**verify.py expiration test fails**
The expiration test requires the execution gateway to be running. Start it
with `docker compose up -d`.
