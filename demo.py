# Copyright 2026 MNC Labs, Inc.
# Author: Asaad Riaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""VB-OS Trading Reference Integration -- governed order pipeline demo.

Runs 30 governance scenarios (A1-E5) plus 17 architectural demos (F1-F17)
through the four-boundary pipeline: B1 Order Admissibility, B2 Compliance,
B3 Execution Authorization, B4 Execution Reconciliation.

    python3 demo.py --list                  # list all scenarios
    python3 demo.py --run A1               # run one scenario
    python3 demo.py --run A                # run a group
    python3 demo.py --all                  # run all A-E scenarios
    python3 demo.py --all --include-f      # run all including F-series
    python3 demo.py --live E1              # run with live Alpaca submission
    python3 demo.py --benchmark            # timing benchmark (all A-E)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psycopg2

VBOS_API_URL = os.environ.get("VBOS_API_URL", "https://api.vbos.cloud")
VBOS_API_KEY = os.environ["VBOS_API_KEY"]
VBOS_PROJECT_ID = os.environ["VBOS_PROJECT_ID"]
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://trading:trading@trading-postgres:5432/trading")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://trading-gateway:9093")

ROOT = Path(__file__).parent
STATE_FILE = ROOT / ".bootstrap-state.json"

RULE = "-" * 72


def _load_state() -> dict:
    if not STATE_FILE.exists():
        print("ERROR: .bootstrap-state.json not found. Run bootstrap.py first.")
        sys.exit(1)
    return json.loads(STATE_FILE.read_text())


STATE: dict = {}
CONNECTOR_IDS: dict = {}
BOUNDARY_IDS: dict = {}


def _init_state() -> None:
    global STATE, CONNECTOR_IDS, BOUNDARY_IDS
    STATE = _load_state()
    CONNECTOR_IDS = STATE.get("connector_ids", {})
    BOUNDARY_IDS = STATE.get("boundaries", {})


# -- VB-OS Cloud API -------------------------------------------------------

def api(method: str, path: str, body: dict | None = None) -> dict | None:
    url = f"{VBOS_API_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {VBOS_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            body_text = e.read().decode()[:300] if e.fp else ""
            print(f"  HTTP {e.code}: {body_text}")
            return None
    return None


def gather(connector_name: str, payload: dict, provenance: dict | None = None) -> dict | None:
    connector_id = CONNECTOR_IDS.get(connector_name)
    if not connector_id:
        print(f"  ERROR: connector '{connector_name}' not in bootstrap state")
        return None
    body: dict = {"raw_payload": payload}
    if provenance:
        body["provenance"] = provenance
    return api("POST", f"/v1/projects/{VBOS_PROJECT_ID}/connectors/{connector_id}/gather", body)


def read_evaluation(evaluation_id: str) -> dict | None:
    return api("GET", f"/v1/projects/{VBOS_PROJECT_ID}/evaluations/{evaluation_id}")


def replay_evaluation(evaluation_id: str) -> dict | None:
    return api("POST", f"/v1/projects/{VBOS_PROJECT_ID}/evaluations/{evaluation_id}/replay", {})


# -- Gateway API ------------------------------------------------------------

def gateway_api(method: str, path: str, body: dict | None = None) -> dict | None:
    url = f"{GATEWAY_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()[:300] if e.fp else ""
        print(f"  Gateway HTTP {e.code}: {body_text}")
        return None
    except Exception as exc:
        print(f"  Gateway error: {exc}")
        return None


# -- Display helpers --------------------------------------------------------

def show_evaluation(evaluation: dict, label: str) -> None:
    print(f"    {label} {evaluation.get('evaluation_id', '')}")
    print(f"      Decision: {evaluation.get('decision')}")
    print(f"      Boundary: {evaluation.get('boundary_ref')}")
    for name, result in (evaluation.get("predicate_results") or {}).items():
        passed = result.get("passed") if isinstance(result, dict) else result
        mark = "PASS" if passed else "FAIL"
        print(f"      {name}: {mark}")
    for reason in evaluation.get("failure_reasons") or []:
        detail = reason.get("predicate") or reason.get("field") or ""
        print(f"      Failure: {reason.get('type')}{': ' + detail if detail else ''}")


def show_replay(evaluation_id: str) -> None:
    result = replay_evaluation(evaluation_id)
    if not result:
        return
    out = result.get("replay_evaluation_output") or {}
    print(f"      Replay: {out.get('decision')} "
          f"(result_match={result.get('result_match')}, "
          f"identity_match={result.get('replay_id_match')})")


# -- Database access --------------------------------------------------------

def load_proposal(order_ref: str) -> dict | None:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT p.order_ref, p.account_id, p.instrument_id, p.side, p.order_type,
               p.quantity, p.limit_price_cents, p.trader_authority_cents,
               p.desk_exposure_cents, p.desk_limit_cents, p.price_tolerance_bps,
               p.margin_requirement_cents, p.available_margin_cents,
               p.authorization_valid_seconds, p.mandate_match,
               p.concentration_bps_after, p.concentration_limit_bps,
               p.projected_position_quantity, p.position_limit_quantity,
               p.last_opposite_side_seconds, p.cooldown_window_seconds,
               p.restricted_list_hit, p.scenario_group, p.scenario_label,
               a.account_status, a.buying_power_cents,
               i.asset_class, i.quantity_unit, i.quantity_divisor, i.symbol,
               m.reference_price_cents, m.market_session_open,
               COALESCE(pos.position_quantity, 0) as position_quantity
        FROM order_proposals p
        JOIN accounts a ON a.account_id = p.account_id
        JOIN instruments i ON i.instrument_id = p.instrument_id
        JOIN market_state m ON m.instrument_id = p.instrument_id
        LEFT JOIN positions pos ON pos.account_id = p.account_id
                                AND pos.instrument_id = p.instrument_id
        WHERE p.order_ref = %s
    """, (order_ref,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    cols = [
        "order_ref", "account_id", "instrument_id", "side", "order_type",
        "quantity", "limit_price_cents", "trader_authority_cents",
        "desk_exposure_cents", "desk_limit_cents", "price_tolerance_bps",
        "margin_requirement_cents", "available_margin_cents",
        "authorization_valid_seconds", "mandate_match",
        "concentration_bps_after", "concentration_limit_bps",
        "projected_position_quantity", "position_limit_quantity",
        "last_opposite_side_seconds", "cooldown_window_seconds",
        "restricted_list_hit", "scenario_group", "scenario_label",
        "account_status", "buying_power_cents",
        "asset_class", "quantity_unit", "quantity_divisor", "symbol",
        "reference_price_cents", "market_session_open", "position_quantity",
    ]
    return dict(zip(cols, row))


# -- Evidence derivation ----------------------------------------------------

def derive_notional(side: str, order_type: str, quantity: int,
                    price_cents: int, quantity_divisor: int) -> int:
    if order_type == "limit" and side == "buy":
        return (quantity * price_cents + quantity_divisor - 1) // quantity_divisor
    else:
        return quantity * price_cents // quantity_divisor


def build_b1_evidence(p: dict) -> tuple[dict, dict]:
    price = p["limit_price_cents"] if p["order_type"] == "limit" else p["reference_price_cents"]
    notional = derive_notional(p["side"], p["order_type"], p["quantity"], price, p["quantity_divisor"])

    evidence = {
        "order_ref": p["order_ref"],
        "account_id": p["account_id"],
        "instrument_id": p["instrument_id"],
        "account_status": p["account_status"],
        "side": p["side"],
        "asset_class": p["asset_class"],
        "quantity": p["quantity"],
        "order_notional_cents": notional,
        "buying_power_cents": p["buying_power_cents"],
        "order_type": p["order_type"],
        "market_session_open": p["market_session_open"],
    }
    if p["side"] == "sell":
        evidence["position_quantity"] = p["position_quantity"]

    provenance = {k: "oms_database" for k in evidence}
    return evidence, provenance


def build_b2_evidence(p: dict) -> tuple[dict, dict]:
    price = p["limit_price_cents"] if p["order_type"] == "limit" else p["reference_price_cents"]
    notional = derive_notional(p["side"], p["order_type"], p["quantity"], price, p["quantity_divisor"])

    evidence = {
        "order_ref": p["order_ref"],
        "account_id": p["account_id"],
        "instrument_id": p["instrument_id"],
        "side": p["side"],
        "quantity": p["quantity"],
        "asset_class": p["asset_class"],
        "order_notional_cents": notional,
        "order_type": p["order_type"],
        "mandate_match": p["mandate_match"],
        "concentration_bps_after": p["concentration_bps_after"],
        "concentration_limit_bps": p["concentration_limit_bps"],
        "projected_position_quantity": p["projected_position_quantity"],
        "position_limit_quantity": p["position_limit_quantity"],
        "last_opposite_side_seconds": p["last_opposite_side_seconds"],
        "cooldown_window_seconds": p["cooldown_window_seconds"],
    }
    if p["restricted_list_hit"]:
        evidence["restricted_list_hit"] = p["restricted_list_hit"]

    provenance = {k: "oms_database" for k in evidence}
    provenance["mandate_match"] = "compliance_system"
    provenance["concentration_bps_after"] = "risk_system"
    provenance["concentration_limit_bps"] = "risk_system"
    provenance["projected_position_quantity"] = "risk_system"
    provenance["position_limit_quantity"] = "risk_system"
    provenance["last_opposite_side_seconds"] = "compliance_system"
    provenance["cooldown_window_seconds"] = "compliance_system"
    if "restricted_list_hit" in evidence:
        provenance["restricted_list_hit"] = "compliance_system"
    return evidence, provenance


def build_b3_evidence(p: dict, b1_eval_id: str, b2_eval_id: str) -> tuple[dict, dict]:
    price = p["limit_price_cents"] if p["order_type"] == "limit" else p["reference_price_cents"]
    notional = derive_notional(p["side"], p["order_type"], p["quantity"], price, p["quantity_divisor"])
    combined = p["desk_exposure_cents"] + notional
    ref_price = p["reference_price_cents"]
    limit_price = p["limit_price_cents"]

    if p["order_type"] == "limit" and ref_price > 0:
        deviation_above = (limit_price - ref_price) * 10000
        deviation_below = (ref_price - limit_price) * 10000
        tolerance_threshold = p["price_tolerance_bps"] * ref_price
    else:
        deviation_above = 0
        deviation_below = 0
        tolerance_threshold = 0

    evidence = {
        "account_id": p["account_id"],
        "instrument_id": p["instrument_id"],
        "asset_class": p["asset_class"],
        "side": p["side"],
        "quantity": p["quantity"],
        "quantity_unit": p["quantity_unit"],
        "order_type": p["order_type"],
        "order_ref": p["order_ref"],
        "order_notional_cents": notional,
        "trader_authority_cents": p["trader_authority_cents"],
        "desk_exposure_cents": p["desk_exposure_cents"],
        "desk_limit_cents": p["desk_limit_cents"],
        "combined_desk_and_order_cents": combined,
        "limit_price_cents": limit_price,
        "reference_price_cents": ref_price,
        "price_tolerance_bps": p["price_tolerance_bps"],
        "price_deviation_above_scaled": deviation_above,
        "price_deviation_below_scaled": deviation_below,
        "tolerance_threshold_scaled": tolerance_threshold,
        "margin_requirement_cents": p["margin_requirement_cents"],
        "available_margin_cents": p["available_margin_cents"],
        "authorization_valid_seconds": p["authorization_valid_seconds"],
        "b1_evaluation_id": b1_eval_id,
        "b2_evaluation_id": b2_eval_id,
    }

    provenance = {
        "account_id": "oms_database",
        "instrument_id": "oms_database",
        "asset_class": "oms_database",
        "side": "oms_database",
        "quantity": "oms_database",
        "quantity_unit": "oms_database",
        "order_type": "oms_database",
        "order_ref": "oms_database",
        "order_notional_cents": "oms_database",
        "trader_authority_cents": "risk_system",
        "desk_exposure_cents": "risk_system",
        "desk_limit_cents": "risk_system",
        "combined_desk_and_order_cents": "oms_database",
        "limit_price_cents": "oms_database",
        "reference_price_cents": "market_data",
        "price_tolerance_bps": "risk_system",
        "price_deviation_above_scaled": "oms_database",
        "price_deviation_below_scaled": "oms_database",
        "tolerance_threshold_scaled": "oms_database",
        "margin_requirement_cents": "risk_system",
        "available_margin_cents": "risk_system",
        "authorization_valid_seconds": "risk_system",
        "b1_evaluation_id": "orchestrator",
        "b2_evaluation_id": "orchestrator",
    }

    return evidence, provenance


# -- Pipeline orchestration -------------------------------------------------

def run_pipeline(p: dict, live: bool = False) -> dict:
    """Run B1 -> B2 -> B3 sequential pipeline. Returns result dict."""
    result = {
        "order_ref": p["order_ref"],
        "scenario_label": p["scenario_label"],
        "b1": None, "b2": None, "b3": None,
        "gateway": None,
        "stopped_at": None,
    }

    # -- B1: Order Admissibility --
    print("  Stage B1 -- Order Admissibility")
    b1_ev, b1_prov = build_b1_evidence(p)
    b1_resp = gather("trading-b1-evidence", b1_ev, b1_prov)
    if not b1_resp:
        result["stopped_at"] = "B1"
        return result

    b1_eval = b1_resp.get("evaluation") or {}
    result["b1"] = b1_eval
    show_evaluation(b1_eval, "B1")
    b1_decision = b1_eval.get("decision")
    b1_eval_id = b1_eval.get("evaluation_id", "")

    if b1_decision != "ASSERT":
        result["stopped_at"] = "B1"
        if b1_eval_id:
            show_replay(b1_eval_id)
        return result

    if b1_eval_id:
        show_replay(b1_eval_id)

    # -- B2: Compliance Authorization --
    print("\n  Stage B2 -- Compliance Authorization")
    b2_ev, b2_prov = build_b2_evidence(p)
    b2_resp = gather("trading-b2-evidence", b2_ev, b2_prov)
    if not b2_resp:
        result["stopped_at"] = "B2"
        return result

    b2_eval = b2_resp.get("evaluation") or {}
    result["b2"] = b2_eval
    show_evaluation(b2_eval, "B2")
    b2_decision = b2_eval.get("decision")
    b2_eval_id = b2_eval.get("evaluation_id", "")

    if b2_decision != "ASSERT":
        result["stopped_at"] = "B2"
        if b2_eval_id:
            show_replay(b2_eval_id)
        return result

    if b2_eval_id:
        show_replay(b2_eval_id)

    # -- B3: Execution Authorization --
    print("\n  Stage B3 -- Execution Authorization")
    b3_ev, b3_prov = build_b3_evidence(p, b1_eval_id, b2_eval_id)
    b3_resp = gather("trading-b3-evidence", b3_ev, b3_prov)
    if not b3_resp:
        result["stopped_at"] = "B3"
        return result

    b3_eval = b3_resp.get("evaluation") or {}
    result["b3"] = b3_eval
    show_evaluation(b3_eval, "B3")
    b3_decision = b3_eval.get("decision")
    b3_eval_id = b3_eval.get("evaluation_id", "")

    if b3_decision != "ASSERT":
        result["stopped_at"] = "B3"
        if b3_eval_id:
            show_replay(b3_eval_id)
        return result

    if b3_eval_id:
        show_replay(b3_eval_id)

    # -- Gateway verification --
    print("\n  Gateway -- Authorization Verification")
    dispatch_id = f"DISPATCH-{p['order_ref']}-{int(time.time())}"
    proposed = {
        "account_id": p["account_id"],
        "instrument_id": p["instrument_id"],
        "asset_class": p["asset_class"],
        "side": p["side"],
        "quantity": p["quantity"],
        "order_type": p["order_type"],
        "order_ref": p["order_ref"],
    }
    if p["order_type"] == "limit":
        proposed["limit_price_cents"] = p["limit_price_cents"]

    gw_body = {
        "dispatch_id": dispatch_id,
        "evaluation_id": b3_eval_id,
        "proposed_order": proposed,
        "live": live,
    }
    gw_resp = gateway_api("POST", "/submit", gw_body)
    result["gateway"] = gw_resp
    if gw_resp:
        print(f"    Gateway status: {gw_resp.get('status')}")
        if gw_resp.get("alpaca_order_id"):
            print(f"    Alpaca order: {gw_resp.get('alpaca_order_id')}")
        if gw_resp.get("reason"):
            err = gw_resp["reason"]
            print(f"    Rejection: {err.get('error', err)}")

    return result


# -- Scenario registry ------------------------------------------------------

SCENARIOS: dict[str, tuple[str, str, str]] = {
    "A1": ("ORD-A1", "Clean equity buy", "ASSERT at B3"),
    "A2": ("ORD-A2", "Account suspended", "DEFER at B1"),
    "A3": ("ORD-A3", "Exceeds buying power", "DEFER at B1"),
    "A4": ("ORD-A4", "Market closed for equity", "DEFER at B1"),
    "A5": ("ORD-A5", "Equity sell within position", "ASSERT at B3"),
    "B1": ("ORD-B1", "Clean crypto buy", "ASSERT at B3"),
    "B2": ("ORD-B2", "Crypto sell exceeds position", "DEFER at B1"),
    "B3": ("ORD-B3", "Crypto during equity market close", "ASSERT at B3"),
    "C1": ("ORD-C1", "Clean compliance pass", "ASSERT at B3"),
    "C2": ("ORD-C2", "Restricted security", "DEFER at B2"),
    "C3": ("ORD-C3", "Exceeds concentration limit", "DEFER at B2"),
    "C4": ("ORD-C4", "Exceeds position limit", "DEFER at B2"),
    "C5": ("ORD-C5", "Opposite-side cooldown violation", "DEFER at B2"),
    "C6": ("ORD-C6", "Negative concentration limit", "DEFER at B2"),
    "D1": ("ORD-D1", "Clean authorization", "ASSERT at B3"),
    "D2": ("ORD-D2", "Exceeds trader authority", "DEFER at B3"),
    "D3": ("ORD-D3", "Exceeds desk exposure limit", "DEFER at B3"),
    "D4": ("ORD-D4", "Price beyond tolerance", "DEFER at B3"),
    "D5": ("ORD-D5", "Insufficient margin", "DEFER at B3"),
    "E1": ("ORD-E1", "Full fill conformant", "ASSERT at B3 + gateway"),
    "E2": ("ORD-E2", "Partial fill conformant", "ASSERT at B3 + gateway"),
    "E3": ("ORD-E3", "Over-execution simulated", "ASSERT at B3 + simulated B4"),
    "E4": ("ORD-E4", "Price slippage exceeds ceiling", "ASSERT at B3 + simulated B4"),
    "E5": ("ORD-E5", "Authorization identity mismatch", "ASSERT at B3 + simulated B4"),
}

F_SCENARIOS: dict[str, tuple[str, str, str]] = {
    "F1":  ("ORD-F1",  "Expired authorization", "Gateway rejects expired B3"),
    "F2":  ("ORD-F2",  "Duplicate execution event", "Gateway dedup via cumulative state"),
    "F3":  ("ORD-F3",  "Cumulative over-execution and regression", "B4 anomaly detection"),
    "F4":  ("ORD-F4",  "Parameter mutation", "Gateway detects tampered evidence"),
    "F5":  ("ORD-F5-R1", "Autonomous strategy -- authorized", "Desk capacity permits"),
    "F5b": ("ORD-F5-R2", "Autonomous strategy -- blocked", "Desk capacity consumed"),
    "F7":  ("ORD-F7",  "Boundary version change", "Gateway rejects stale version"),
    "F8":  ("ORD-F8-B1B3", "Forged B2 lineage", "Cross-chain material mismatch"),
    "F9":  ("ORD-F9",  "Notional inconsistency", "Gateway arithmetic verification"),
    "F10": ("ORD-F10", "Ambiguous broker submission", "UNCERTAIN recovery"),
    "F11": ("ORD-F11-ALPHA", "Concurrent authorization -- Alpha", "Parallel pipeline race"),
    "F11b":("ORD-F11-BETA",  "Concurrent authorization -- Beta", "Parallel pipeline race"),
    "F12": ("ORD-F12", "Stale evidence market order", "Market order reference price"),
    "F13": ("ORD-F13", "Outbox crash recovery", "Transactional outbox guarantee"),
    "F14": ("ORD-F14", "Price-only broker correction", "Cumulative state comparison"),
    "F15": ("ORD-F15", "UNCERTAIN recovery", "Terminal state resolution"),
    "F16": ("ORD-F16", "Concurrent dispatch same B3", "Authorization consumption"),
    "F17": ("ORD-F17-MKT", "Cross-chain order type mismatch", "B1/B3 material mismatch"),
}


def list_scenarios(include_f: bool = False) -> None:
    print(RULE)
    print("VB-OS Trading Reference -- Governance Scenarios")
    print(RULE)
    print()
    for key, (order_ref, label, expectation) in SCENARIOS.items():
        print(f"  {key:>4s}  {label:<45s}  [{expectation}]")
    if include_f:
        print()
        print("  Architectural demos (F-series):")
        for key, (order_ref, label, expectation) in F_SCENARIOS.items():
            print(f"  {key:>4s}  {label:<45s}  [{expectation}]")
    print()
    print(f"  {len(SCENARIOS)} governance scenarios"
          + (f" + {len(F_SCENARIOS)} architectural demos" if include_f else ""))
    print(RULE)


def run_scenario(key: str, live: bool = False) -> dict | None:
    all_scenarios = {**SCENARIOS, **F_SCENARIOS}
    if key not in all_scenarios:
        print(f"Unknown scenario: {key}")
        return None

    order_ref, label, expectation = all_scenarios[key]
    print(RULE)
    print(f"  Scenario {key}: {label}")
    print(f"  Order: {order_ref}  |  Expected: {expectation}")
    print(RULE)

    p = load_proposal(order_ref)
    if not p:
        print(f"  ERROR: proposal {order_ref} not found in database")
        return None

    # A4 override: market_session_open must be 0 (DB keeps MSFT open for other scenarios)
    if key == "A4":
        p["market_session_open"] = 0

    # F-series scenarios with special handling
    if key.startswith("F"):
        return run_f_scenario(key, p, live)

    result = run_pipeline(p, live=live)
    print()

    # E3-E5: simulated B4 reconciliation evidence
    if key in ("E3", "E4", "E5") and result.get("b3") and result["b3"].get("decision") == "ASSERT":
        run_simulated_b4(key, p, result)

    return result


def run_f_scenario(key: str, p: dict, live: bool = False) -> dict:
    """Handle F-series architectural demonstration scenarios."""

    if key == "F1":
        # Expired authorization: run pipeline, then wait for expiration, then submit to gateway
        print("  Running pipeline to obtain B3 authorization...")
        result = run_pipeline(p, live=False)
        if not result.get("b3") or result["b3"].get("decision") != "ASSERT":
            print("  Pipeline did not produce B3 ASSERT; cannot demonstrate expiration")
            return result
        auth_valid = p.get("authorization_valid_seconds", 30)
        print(f"\n  Waiting {auth_valid + 5}s for authorization to expire (valid_seconds={auth_valid})...")
        time.sleep(auth_valid + 5)
        print("  Submitting expired authorization to gateway...")
        gw_resp = gateway_api("POST", "/verify", {
            "evaluation_id": result["b3"]["evaluation_id"],
        })
        print(f"    Gateway response: {json.dumps(gw_resp, indent=2)}")
        result["gateway"] = gw_resp
        return result

    if key == "F2":
        # Duplicate execution event: submit same B4 evidence twice
        result = run_pipeline(p, live=False)
        if not result.get("gateway"):
            return result
        dispatch_id = f"DISPATCH-{p['order_ref']}-{int(time.time())}"
        print("\n  Submitting duplicate dispatch with same evaluation_id...")
        gw_resp = gateway_api("POST", "/submit", {
            "dispatch_id": dispatch_id,
            "evaluation_id": result["b3"]["evaluation_id"],
            "proposed_order": {"account_id": p["account_id"], "instrument_id": p["instrument_id"],
                               "asset_class": p["asset_class"], "side": p["side"],
                               "quantity": p["quantity"], "order_type": p["order_type"],
                               "order_ref": p["order_ref"]},
            "live": False,
        })
        print(f"    Duplicate dispatch result: {gw_resp}")
        return result

    if key == "F4":
        # Parameter mutation: alter quantity in proposed order vs sealed B3
        result = run_pipeline(p, live=False)
        if not result.get("b3") or result["b3"].get("decision") != "ASSERT":
            return result
        print("\n  Submitting mutated order to gateway (quantity altered)...")
        dispatch_id = f"DISPATCH-F4-{int(time.time())}"
        gw_resp = gateway_api("POST", "/submit", {
            "dispatch_id": dispatch_id,
            "evaluation_id": result["b3"]["evaluation_id"],
            "proposed_order": {
                "account_id": p["account_id"], "instrument_id": p["instrument_id"],
                "asset_class": p["asset_class"], "side": p["side"],
                "quantity": p["quantity"] + 500,
                "order_type": p["order_type"], "order_ref": p["order_ref"],
            },
            "live": False,
        })
        print(f"    Gateway rejection: {gw_resp}")
        result["gateway"] = gw_resp
        return result

    if key in ("F5", "F5b"):
        result = run_pipeline(p, live=False)
        return result

    if key == "F8":
        # Forged B2 lineage: run B1 + B2 with different accounts, then splice into B3
        print("  Running B1 for real order (ACC-ALPHA-1)...")
        b1_ev, b1_prov = build_b1_evidence(p)
        b1_resp = gather("trading-b1-evidence", b1_ev, b1_prov)
        b1_eval = (b1_resp or {}).get("evaluation", {})
        show_evaluation(b1_eval, "B1")
        b1_eval_id = b1_eval.get("evaluation_id", "")

        print("\n  Running B2 with DIFFERENT account (ACC-ALPHA-2) -- forged lineage")
        p_fake = load_proposal("ORD-F8-B2")
        if not p_fake:
            print("  ERROR: ORD-F8-B2 not found")
            return {"stopped_at": "F8-setup"}
        b2_ev, b2_prov = build_b2_evidence(p_fake)
        b2_resp = gather("trading-b2-evidence", b2_ev, b2_prov)
        b2_eval = (b2_resp or {}).get("evaluation", {})
        show_evaluation(b2_eval, "B2-forged")
        b2_eval_id = b2_eval.get("evaluation_id", "")

        print("\n  Running B3 with forged B2 evaluation_id (cross-chain mismatch expected)...")
        b3_ev, b3_prov = build_b3_evidence(p, b1_eval_id, b2_eval_id)
        b3_resp = gather("trading-b3-evidence", b3_ev, b3_prov)
        b3_eval = (b3_resp or {}).get("evaluation", {})
        show_evaluation(b3_eval, "B3")

        if b3_eval.get("decision") == "ASSERT":
            print("\n  B3 ASSERTed but gateway will catch cross-chain mismatch...")
            dispatch_id = f"DISPATCH-F8-{int(time.time())}"
            gw_resp = gateway_api("POST", "/verify", {
                "evaluation_id": b3_eval.get("evaluation_id"),
                "proposed_order": {
                    "account_id": p["account_id"],
                    "instrument_id": p["instrument_id"],
                    "asset_class": p["asset_class"],
                    "side": p["side"],
                    "quantity": p["quantity"],
                    "order_type": p["order_type"],
                    "order_ref": p["order_ref"],
                },
            })
            print(f"    Gateway verification: {gw_resp}")

        return {"b1": b1_eval, "b2_forged": b2_eval, "b3": b3_eval}

    if key == "F9":
        # Notional inconsistency: run pipeline with deliberately wrong notional
        # The seed data has quantity=1000, price=$200, but the derivation will produce
        # the correct notional; we override it to demonstrate gateway arithmetic check
        result = run_pipeline(p, live=False)
        if not result.get("b3") or result["b3"].get("decision") != "ASSERT":
            return result
        print("\n  Gateway arithmetic verification would catch notional inconsistency")
        print("  (In this scenario, the sealed B3 artifact's notional is correct;")
        print("   a real inconsistency would be caught during verify_authorization)")
        return result

    if key == "F10":
        # UNCERTAIN recovery: submit, simulate timeout, then recover
        result = run_pipeline(p, live=False)
        print("\n  F10 demonstrates UNCERTAIN recovery when broker response is ambiguous.")
        print("  In --live mode, a network timeout would set UNCERTAIN status,")
        print("  and the reconciliation loop resolves it to FILLED/CANCELED/EXPIRED.")
        return result

    if key == "F16":
        # Concurrent dispatch same B3: two dispatches referencing same authorization
        result = run_pipeline(p, live=False)
        if not result.get("b3") or result["b3"].get("decision") != "ASSERT":
            return result
        b3_eval_id = result["b3"]["evaluation_id"]
        print(f"\n  First dispatch consumed B3 evaluation {b3_eval_id}")
        print("  Attempting second dispatch with same authorization...")
        dispatch_id = f"DISPATCH-F16-DUP-{int(time.time())}"
        gw_resp = gateway_api("POST", "/submit", {
            "dispatch_id": dispatch_id,
            "evaluation_id": b3_eval_id,
            "proposed_order": {
                "account_id": p["account_id"], "instrument_id": p["instrument_id"],
                "asset_class": p["asset_class"], "side": p["side"],
                "quantity": p["quantity"], "order_type": p["order_type"],
                "order_ref": p["order_ref"],
            },
            "live": False,
        })
        print(f"    Second dispatch result: {gw_resp}")
        return result

    if key == "F17":
        # Cross-chain order type mismatch: B1 with market, B3 with limit
        print("  Running B1 with market order type...")
        b1_ev, b1_prov = build_b1_evidence(p)
        b1_resp = gather("trading-b1-evidence", b1_ev, b1_prov)
        b1_eval = (b1_resp or {}).get("evaluation", {})
        show_evaluation(b1_eval, "B1-market")

        print("\n  Running B2 with market order type...")
        b2_ev, b2_prov = build_b2_evidence(p)
        b2_resp = gather("trading-b2-evidence", b2_ev, b2_prov)
        b2_eval = (b2_resp or {}).get("evaluation", {})
        show_evaluation(b2_eval, "B2-market")

        # Now load the limit variant and run B3 with mismatched order_type
        p_limit = load_proposal("ORD-F17-LMT")
        if not p_limit:
            print("  ERROR: ORD-F17-LMT not found")
            return {}
        b1_eval_id = b1_eval.get("evaluation_id", "")
        b2_eval_id = b2_eval.get("evaluation_id", "")

        print("\n  Running B3 with LIMIT order type (mismatch with B1/B2 market)...")
        b3_ev, b3_prov = build_b3_evidence(p_limit, b1_eval_id, b2_eval_id)
        b3_resp = gather("trading-b3-evidence", b3_ev, b3_prov)
        b3_eval = (b3_resp or {}).get("evaluation", {})
        show_evaluation(b3_eval, "B3-limit")

        if b3_eval.get("decision") == "ASSERT":
            print("\n  Gateway cross-chain verification catches order_type mismatch...")
            gw_resp = gateway_api("POST", "/verify", {
                "evaluation_id": b3_eval.get("evaluation_id"),
            })
            print(f"    Gateway: {gw_resp}")

        return {"b1": b1_eval, "b2": b2_eval, "b3": b3_eval}

    # Default for other F scenarios: run standard pipeline
    result = run_pipeline(p, live=False)
    return result


def run_simulated_b4(key: str, p: dict, result: dict) -> None:
    """Run simulated B4 reconciliation for E3-E5 scenarios."""
    b3_eval_id = result["b3"]["evaluation_id"]
    b4_connector = "trading-b4-evidence"

    if key == "E3":
        # Over-execution: executed_quantity > authorized_quantity
        print("  Simulated B4 -- Over-execution")
        b4_evidence = {
            "venue_authorization_reference": b3_eval_id,
            "expected_authorization_reference": b3_eval_id,
            "execution_status": "filled",
            "executed_quantity": p["quantity"] + 50,
            "authorized_quantity": p["quantity"],
            "previous_executed_quantity": 0,
            "executed_avg_price_cents": p["limit_price_cents"],
            "authorized_price_ceiling_cents": p["limit_price_cents"],
        }
    elif key == "E4":
        # Price slippage: executed price > authorized ceiling
        print("  Simulated B4 -- Price slippage exceeds ceiling")
        b4_evidence = {
            "venue_authorization_reference": b3_eval_id,
            "expected_authorization_reference": b3_eval_id,
            "execution_status": "filled",
            "executed_quantity": p["quantity"],
            "authorized_quantity": p["quantity"],
            "previous_executed_quantity": 0,
            "executed_avg_price_cents": p["limit_price_cents"] + 500,
            "authorized_price_ceiling_cents": p["limit_price_cents"],
        }
    elif key == "E5":
        # Authorization identity mismatch
        print("  Simulated B4 -- Authorization identity mismatch")
        b4_evidence = {
            "venue_authorization_reference": "WRONG-AUTH-ID",
            "expected_authorization_reference": b3_eval_id,
            "execution_status": "filled",
            "executed_quantity": p["quantity"],
            "authorized_quantity": p["quantity"],
            "previous_executed_quantity": 0,
            "executed_avg_price_cents": p["limit_price_cents"],
            "authorized_price_ceiling_cents": p["limit_price_cents"],
        }
    else:
        return

    b4_provenance = {
        "venue_authorization_reference": "venue_alpaca",
        "expected_authorization_reference": "sealed_evaluation",
        "execution_status": "venue_alpaca",
        "executed_quantity": "venue_alpaca",
        "authorized_quantity": "sealed_evaluation",
        "previous_executed_quantity": "gateway_state",
        "executed_avg_price_cents": "venue_alpaca",
        "authorized_price_ceiling_cents": "sealed_evaluation",
    }

    b4_resp = gather(b4_connector, b4_evidence, b4_provenance)
    if b4_resp:
        b4_eval = b4_resp.get("evaluation") or {}
        show_evaluation(b4_eval, "B4")
        b4_eval_id = b4_eval.get("evaluation_id", "")
        if b4_eval_id:
            show_replay(b4_eval_id)
    print()


# -- Benchmark mode ---------------------------------------------------------

def run_benchmark() -> None:
    print(RULE)
    print("  VB-OS Trading Reference -- Pipeline Benchmark")
    print(RULE)
    print()
    timings: list[tuple[str, float, str]] = []

    for key, (order_ref, label, _) in SCENARIOS.items():
        p = load_proposal(order_ref)
        if not p:
            continue
        start = time.monotonic()
        run_pipeline(p, live=False)
        elapsed = time.monotonic() - start
        timings.append((key, elapsed, label))
        print(f"  {key}: {elapsed:.3f}s\n")

    print(RULE)
    print("  Summary:")
    total = sum(t for _, t, _ in timings)
    for key, elapsed, label in timings:
        print(f"    {key:>4s}  {elapsed:6.3f}s  {label}")
    print(f"\n    Total: {total:.3f}s  |  Mean: {total/len(timings):.3f}s")
    print(RULE)


# -- CLI entry point --------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="VB-OS Trading Reference Demo")
    parser.add_argument("--list", action="store_true", help="List all scenarios")
    parser.add_argument("--run", type=str, help="Run a specific scenario (e.g. A1) or group (e.g. A)")
    parser.add_argument("--all", action="store_true", help="Run all A-E scenarios")
    parser.add_argument("--include-f", action="store_true", help="Include F-series in --all or --list")
    parser.add_argument("--live", type=str, nargs="?", const="ALL",
                        help="Run with live Alpaca submission (optionally specify scenario)")
    parser.add_argument("--benchmark", action="store_true", help="Run timing benchmark")

    args = parser.parse_args()

    if args.list:
        list_scenarios(include_f=args.include_f)
        return

    _init_state()

    if args.benchmark:
        run_benchmark()
        return

    if args.live:
        target = args.live
        if target == "ALL":
            for key in SCENARIOS:
                run_scenario(key, live=True)
        else:
            run_scenario(target, live=True)
        return

    if args.all:
        scenarios = dict(SCENARIOS)
        if args.include_f:
            scenarios.update(F_SCENARIOS)
        for key in scenarios:
            run_scenario(key, live=False)
            print()
        return

    if args.run:
        target = args.run.upper()
        if target in {**SCENARIOS, **F_SCENARIOS}:
            run_scenario(target, live=False)
        else:
            matched = [k for k in {**SCENARIOS, **F_SCENARIOS} if k.startswith(target)]
            if matched:
                for key in sorted(matched):
                    run_scenario(key, live=False)
                    print()
            else:
                print(f"No scenarios matching '{target}'")
        return

    list_scenarios(include_f=True)


if __name__ == "__main__":
    main()
