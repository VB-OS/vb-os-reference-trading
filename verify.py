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

"""VB-OS Trading Reference Integration -- verification suite.

Four verification functions:
  1. Parity   -- replay every sealed evaluation and confirm decision parity
  2. Integrity -- re-derive pre-computed fields and compare to sealed evidence
  3. Counterfactual -- flip one predicate-deciding field, replay, confirm change
  4. Expiration -- verify the gateway rejects an expired B3 authorization

    python3 verify.py --all                # run all verifications
    python3 verify.py --parity             # replay parity only
    python3 verify.py --integrity          # arithmetic integrity only
    python3 verify.py --counterfactual     # counterfactual only
    python3 verify.py --expiration         # gateway expiration only
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

VBOS_API_URL = os.environ.get("VBOS_API_URL", "https://api.vbos.cloud")
VBOS_API_KEY = os.environ["VBOS_API_KEY"]
VBOS_PROJECT_ID = os.environ["VBOS_PROJECT_ID"]
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://trading-gateway:9093")

ROOT = Path(__file__).parent
STATE_FILE = ROOT / ".bootstrap-state.json"

RULE = "-" * 72


def _load_state() -> dict:
    if not STATE_FILE.exists():
        print("ERROR: .bootstrap-state.json not found. Run bootstrap.py first.")
        sys.exit(1)
    return json.loads(STATE_FILE.read_text())


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


def list_evaluations() -> list[dict]:
    path = f"/v1/projects/{VBOS_PROJECT_ID}/evaluations"
    result = api("GET", path)
    if not result:
        return []
    items = result.get("data", result.get("items", []))
    if isinstance(items, list):
        return items
    return []


# -- Test 1: Parity ---------------------------------------------------------

def verify_parity() -> tuple[int, int]:
    print(RULE)
    print("  Verification 1: Replay Parity")
    print(RULE)

    evaluations = list_evaluations()
    if not evaluations:
        print("  No evaluations found. Run demo.py --all first.")
        return 0, 0

    passed = 0
    failed = 0

    for ev in evaluations:
        eval_id = ev.get("evaluation_id") or ev.get("id", "")
        original_decision = ev.get("decision", "")
        boundary = ev.get("boundary_ref", "")

        if not eval_id:
            continue

        result = api("POST", f"/v1/projects/{VBOS_PROJECT_ID}/evaluations/{eval_id}/replay", {})
        if not result:
            print(f"    SKIP  {eval_id[:12]}  (replay failed)")
            failed += 1
            continue

        replay_out = result.get("replay_evaluation_output") or {}
        replay_decision = replay_out.get("decision", "")
        result_match = result.get("result_match", False)

        if result_match and replay_decision == original_decision:
            passed += 1
            print(f"    PASS  {eval_id[:12]}  {boundary}  {original_decision}")
        else:
            failed += 1
            print(f"    FAIL  {eval_id[:12]}  {boundary}  "
                  f"original={original_decision} replay={replay_decision}")

    print(f"\n  Parity: {passed} passed, {failed} failed out of {passed + failed}")
    return passed, failed


# -- Test 2: Integrity -------------------------------------------------------

def derive_notional(side: str, order_type: str, quantity: int,
                    price_cents: int, quantity_divisor: int) -> int:
    if order_type == "limit" and side == "buy":
        return (quantity * price_cents + quantity_divisor - 1) // quantity_divisor
    else:
        return quantity * price_cents // quantity_divisor


def verify_integrity() -> tuple[int, int]:
    print(RULE)
    print("  Verification 2: Arithmetic Integrity")
    print(RULE)

    evaluations = list_evaluations()
    passed = 0
    failed = 0

    for ev in evaluations:
        eval_id = ev.get("evaluation_id") or ev.get("id", "")
        boundary = ev.get("boundary_ref", "")

        if boundary != "B_EXECUTION_AUTHORIZATION":
            continue

        detail = api("GET", f"/v1/projects/{VBOS_PROJECT_ID}/evaluations/{eval_id}")
        if not detail:
            continue

        record = detail.get("data", detail)
        workload = record.get("artifact", {}).get("workload", {})
        if not workload:
            workload = record.get("evidence", {})

        side = workload.get("side", "buy")
        order_type = workload.get("order_type", "limit")
        quantity = int(workload.get("quantity", 0))
        limit_price = int(workload.get("limit_price_cents", 0))
        ref_price = int(workload.get("reference_price_cents", 0))
        desk_exp = int(workload.get("desk_exposure_cents", 0))

        price = limit_price if order_type == "limit" else ref_price
        # Assume divisor=1 for equities in sealed artifact
        expected_notional = derive_notional(side, order_type, quantity, price, 1)
        sealed_notional = int(workload.get("order_notional_cents", 0))

        expected_combined = desk_exp + sealed_notional
        sealed_combined = int(workload.get("combined_desk_and_order_cents", 0))

        checks_ok = True

        if expected_notional != sealed_notional:
            print(f"    FAIL  {eval_id[:12]}  notional: expected={expected_notional}, sealed={sealed_notional}")
            checks_ok = False

        if expected_combined != sealed_combined:
            print(f"    FAIL  {eval_id[:12]}  combined: expected={expected_combined}, sealed={sealed_combined}")
            checks_ok = False

        if order_type == "limit" and ref_price > 0:
            expected_above = (limit_price - ref_price) * 10000
            sealed_above = int(workload.get("price_deviation_above_scaled", 0))
            if expected_above != sealed_above:
                print(f"    FAIL  {eval_id[:12]}  deviation_above: {expected_above} vs {sealed_above}")
                checks_ok = False

            expected_below = (ref_price - limit_price) * 10000
            sealed_below = int(workload.get("price_deviation_below_scaled", 0))
            if expected_below != sealed_below:
                print(f"    FAIL  {eval_id[:12]}  deviation_below: {expected_below} vs {sealed_below}")
                checks_ok = False

            tolerance_bps = int(workload.get("price_tolerance_bps", 0))
            expected_threshold = tolerance_bps * ref_price
            sealed_threshold = int(workload.get("tolerance_threshold_scaled", 0))
            if expected_threshold != sealed_threshold:
                print(f"    FAIL  {eval_id[:12]}  threshold: {expected_threshold} vs {sealed_threshold}")
                checks_ok = False

        if checks_ok:
            passed += 1
            print(f"    PASS  {eval_id[:12]}  B3 arithmetic consistent")
        else:
            failed += 1

    print(f"\n  Integrity: {passed} passed, {failed} failed out of {passed + failed}")
    return passed, failed


# -- Test 3: Counterfactual --------------------------------------------------

def verify_counterfactual() -> tuple[int, int]:
    print(RULE)
    print("  Verification 3: Counterfactual Replay")
    print(RULE)

    state = _load_state()
    connector_ids = state.get("connector_ids", {})
    b1_connector = connector_ids.get("trading-b1-evidence")
    if not b1_connector:
        print("  No B1 connector in bootstrap state. Run bootstrap.py first.")
        return 0, 0

    # Submit a clean B1 evaluation, then re-submit with account_status changed
    print("  Submitting clean B1 evidence...")
    clean_evidence = {
        "order_ref": "VERIFY-CF-CLEAN",
        "account_id": "ACC-ALPHA-1",
        "instrument_id": "INST-AAPL",
        "account_status": "active",
        "side": "buy",
        "asset_class": "equity",
        "quantity": 100,
        "order_notional_cents": 2270000,
        "buying_power_cents": 200000000,
        "order_type": "limit",
        "market_session_open": 1,
    }
    clean_prov = {k: "oms_database" for k in clean_evidence}
    clean_resp = api(
        "POST",
        f"/v1/projects/{VBOS_PROJECT_ID}/connectors/{b1_connector}/gather",
        {"raw_payload": clean_evidence, "provenance": clean_prov},
    )

    passed = 0
    failed = 0

    if clean_resp:
        clean_eval = clean_resp.get("evaluation") or {}
        clean_decision = clean_eval.get("decision")
        print(f"    Clean B1: {clean_decision}")
        if clean_decision == "ASSERT":
            passed += 1
        else:
            failed += 1
            print(f"    FAIL: expected ASSERT, got {clean_decision}")

    # Counterfactual: suspended account
    print("  Submitting counterfactual B1 (account suspended)...")
    cf_evidence = dict(clean_evidence)
    cf_evidence["order_ref"] = "VERIFY-CF-SUSPENDED"
    cf_evidence["account_status"] = "suspended"
    cf_prov = {k: "oms_database" for k in cf_evidence}
    cf_resp = api(
        "POST",
        f"/v1/projects/{VBOS_PROJECT_ID}/connectors/{b1_connector}/gather",
        {"raw_payload": cf_evidence, "provenance": cf_prov},
    )

    if cf_resp:
        cf_eval = cf_resp.get("evaluation") or {}
        cf_decision = cf_eval.get("decision")
        print(f"    Counterfactual B1: {cf_decision}")
        if cf_decision == "DEFER":
            passed += 1
        else:
            failed += 1
            print(f"    FAIL: expected DEFER, got {cf_decision}")

    print(f"\n  Counterfactual: {passed} passed, {failed} failed")
    return passed, failed


# -- Test 4: Expiration ------------------------------------------------------

def verify_expiration() -> tuple[int, int]:
    print(RULE)
    print("  Verification 4: Gateway Expiration")
    print(RULE)

    state = _load_state()
    connector_ids = state.get("connector_ids", {})
    b1_conn = connector_ids.get("trading-b1-evidence")
    b2_conn = connector_ids.get("trading-b2-evidence")
    b3_conn = connector_ids.get("trading-b3-evidence")
    if not all([b1_conn, b2_conn, b3_conn]):
        print("  Missing connectors in bootstrap state.")
        return 0, 0

    passed = 0
    failed = 0

    # Run a quick B1 -> B2 -> B3 pipeline with authorization_valid_seconds=5
    print("  Running short-lived authorization pipeline (valid_seconds=5)...")

    b1_ev = {
        "order_ref": "VERIFY-EXP",
        "account_id": "ACC-ALPHA-1",
        "instrument_id": "INST-AAPL",
        "account_status": "active",
        "side": "buy",
        "asset_class": "equity",
        "quantity": 10,
        "order_notional_cents": 227000,
        "buying_power_cents": 200000000,
        "order_type": "limit",
        "market_session_open": 1,
    }
    b1_prov = {k: "oms_database" for k in b1_ev}
    b1_resp = api("POST", f"/v1/projects/{VBOS_PROJECT_ID}/connectors/{b1_conn}/gather",
                  {"raw_payload": b1_ev, "provenance": b1_prov})
    if not b1_resp:
        print("  B1 gather failed")
        return 0, 1

    b1_eval = b1_resp.get("evaluation", {})
    b1_id = b1_eval.get("evaluation_id", "")
    if b1_eval.get("decision") != "ASSERT":
        print(f"  B1 not ASSERT: {b1_eval.get('decision')}")
        return 0, 1

    b2_ev = {
        "order_ref": "VERIFY-EXP",
        "account_id": "ACC-ALPHA-1",
        "instrument_id": "INST-AAPL",
        "side": "buy",
        "quantity": 10,
        "asset_class": "equity",
        "order_notional_cents": 227000,
        "order_type": "limit",
        "mandate_match": 1,
        "concentration_bps_after": 500,
        "concentration_limit_bps": 1500,
        "projected_position_quantity": 10,
        "position_limit_quantity": 100000,
        "last_opposite_side_seconds": 86400,
        "cooldown_window_seconds": 600,
    }
    b2_prov = {k: "oms_database" for k in b2_ev}
    b2_prov["mandate_match"] = "compliance_system"
    b2_prov["concentration_bps_after"] = "risk_system"
    b2_prov["concentration_limit_bps"] = "risk_system"
    b2_prov["projected_position_quantity"] = "risk_system"
    b2_prov["position_limit_quantity"] = "risk_system"
    b2_prov["last_opposite_side_seconds"] = "compliance_system"
    b2_prov["cooldown_window_seconds"] = "compliance_system"
    b2_resp = api("POST", f"/v1/projects/{VBOS_PROJECT_ID}/connectors/{b2_conn}/gather",
                  {"raw_payload": b2_ev, "provenance": b2_prov})
    if not b2_resp:
        print("  B2 gather failed")
        return 0, 1

    b2_eval = b2_resp.get("evaluation", {})
    b2_id = b2_eval.get("evaluation_id", "")
    if b2_eval.get("decision") != "ASSERT":
        print(f"  B2 not ASSERT: {b2_eval.get('decision')}")
        return 0, 1

    b3_ev = {
        "account_id": "ACC-ALPHA-1",
        "instrument_id": "INST-AAPL",
        "asset_class": "equity",
        "side": "buy",
        "quantity": 10,
        "quantity_unit": "shares",
        "order_type": "limit",
        "order_ref": "VERIFY-EXP",
        "order_notional_cents": 227000,
        "trader_authority_cents": 5000000,
        "desk_exposure_cents": 40000000,
        "desk_limit_cents": 100000000,
        "combined_desk_and_order_cents": 40227000,
        "limit_price_cents": 22700,
        "reference_price_cents": 22700,
        "price_tolerance_bps": 500,
        "price_deviation_above_scaled": 0,
        "price_deviation_below_scaled": 0,
        "tolerance_threshold_scaled": 11350000,
        "margin_requirement_cents": 500000,
        "available_margin_cents": 5000000,
        "authorization_valid_seconds": 5,
        "b1_evaluation_id": b1_id,
        "b2_evaluation_id": b2_id,
    }
    b3_prov = {
        "account_id": "oms_database", "instrument_id": "oms_database",
        "asset_class": "oms_database", "side": "oms_database",
        "quantity": "oms_database", "quantity_unit": "oms_database",
        "order_type": "oms_database", "order_ref": "oms_database",
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
    b3_resp = api("POST", f"/v1/projects/{VBOS_PROJECT_ID}/connectors/{b3_conn}/gather",
                  {"raw_payload": b3_ev, "provenance": b3_prov})
    if not b3_resp:
        print("  B3 gather failed")
        return 0, 1

    b3_eval = b3_resp.get("evaluation", {})
    b3_id = b3_eval.get("evaluation_id", "")
    if b3_eval.get("decision") != "ASSERT":
        print(f"  B3 not ASSERT: {b3_eval.get('decision')}")
        return 0, 1

    print(f"  B3 ASSERT obtained: {b3_id[:12]}")
    print("  Waiting 10s for authorization to expire (valid_seconds=5)...")
    time.sleep(10)

    print("  Verifying expired authorization at gateway...")
    gw_resp = gateway_api("POST", "/verify", {"evaluation_id": b3_id})
    if gw_resp:
        authorized = gw_resp.get("authorized", True)
        error = gw_resp.get("error", "")
        if not authorized and "expired" in error.lower():
            passed += 1
            print(f"    PASS: gateway correctly rejected expired authorization")
            print(f"    Error: {error}")
        else:
            failed += 1
            print(f"    FAIL: expected rejection, got authorized={authorized}, error={error}")
    else:
        failed += 1
        print("    FAIL: gateway did not respond")

    print(f"\n  Expiration: {passed} passed, {failed} failed")
    return passed, failed


# -- CLI entry point ---------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="VB-OS Trading Reference Verification")
    parser.add_argument("--all", action="store_true", help="Run all verifications")
    parser.add_argument("--parity", action="store_true", help="Replay parity only")
    parser.add_argument("--integrity", action="store_true", help="Arithmetic integrity only")
    parser.add_argument("--counterfactual", action="store_true", help="Counterfactual only")
    parser.add_argument("--expiration", action="store_true", help="Gateway expiration only")

    args = parser.parse_args()
    run_all = args.all or not any([args.parity, args.integrity, args.counterfactual, args.expiration])

    total_pass = 0
    total_fail = 0

    if run_all or args.parity:
        p, f = verify_parity()
        total_pass += p
        total_fail += f
        print()

    if run_all or args.integrity:
        p, f = verify_integrity()
        total_pass += p
        total_fail += f
        print()

    if run_all or args.counterfactual:
        p, f = verify_counterfactual()
        total_pass += p
        total_fail += f
        print()

    if run_all or args.expiration:
        p, f = verify_expiration()
        total_pass += p
        total_fail += f
        print()

    print(RULE)
    print(f"  TOTAL: {total_pass} passed, {total_fail} failed")
    status = "ALL PASSED" if total_fail == 0 else "FAILURES DETECTED"
    print(f"  Status: {status}")
    print(RULE)
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
