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

"""Idempotent VB-OS Cloud provisioning for the trading reference environment.

Creates and deploys 4 boundaries, registers 4 caller-supplied-payload connectors,
wires 4 governed dispatch flows, and runs 1 certification model. Safe to re-run.

Requires VBOS_API_KEY, VBOS_PROJECT_ID and VBOS_ENVIRONMENT_ID in the
environment (see .env.example).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("VBOS_URL", "https://api.vb-os.org")
API_KEY = os.environ["VBOS_API_KEY"]
PROJECT_ID = os.environ["VBOS_PROJECT_ID"]
ENV_ID = os.environ["VBOS_ENVIRONMENT_ID"]

ROOT = Path(__file__).parent
STATE_FILE = ROOT / ".bootstrap-state.json"

B1 = "B_ORDER_ADMISSIBILITY"
B2 = "B_COMPLIANCE_AUTHORIZATION"
B3 = "B_EXECUTION_AUTHORIZATION"
B4 = "B_EXECUTION_RECONCILIATION"

BOUNDARIES = [
    (
        B1,
        "Order Admissibility",
        "B1 -- is this proposed order structurally and operationally "
        "admissible before it enters the governance pipeline?",
    ),
    (
        B2,
        "Compliance and Mandate Authorization",
        "B2 -- does this order satisfy applicable restrictions, "
        "investment mandates, and policy constraints?",
    ),
    (
        B3,
        "Execution Authorization",
        "B3 -- does the trader have authority to submit this precisely "
        "bounded order, given current risk limits and market conditions?",
    ),
    (
        B4,
        "Execution Reconciliation",
        "B4 -- does the observed execution conform to the authorization "
        "that was granted? ASSERT = conformant, DEFER = anomaly.",
    ),
]


def _raw_mappings(fields: list[str]) -> list[dict]:
    return [{"evidence_field": f, "source_path": f} for f in fields]


B1_FIELDS = [
    "order_ref", "account_id", "instrument_id", "account_status",
    "side", "asset_class", "quantity", "order_notional_cents",
    "buying_power_cents", "order_type", "market_session_open",
]

B2_FIELDS = [
    "order_ref", "account_id", "instrument_id", "side", "quantity",
    "asset_class", "order_notional_cents", "order_type",
    "mandate_match", "concentration_bps_after", "concentration_limit_bps",
    "projected_position_quantity", "position_limit_quantity",
    "last_opposite_side_seconds", "cooldown_window_seconds",
]

B3_FIELDS = [
    "account_id", "instrument_id", "asset_class", "side", "quantity",
    "quantity_unit", "order_type", "order_ref", "order_notional_cents",
    "trader_authority_cents", "desk_exposure_cents", "desk_limit_cents",
    "combined_desk_and_order_cents", "limit_price_cents",
    "reference_price_cents", "price_tolerance_bps",
    "price_deviation_above_scaled", "price_deviation_below_scaled",
    "tolerance_threshold_scaled", "margin_requirement_cents",
    "available_margin_cents", "authorization_valid_seconds",
    "b1_evaluation_id", "b2_evaluation_id",
]

B4_FIELDS = [
    "venue_authorization_reference", "expected_authorization_reference",
    "execution_status", "executed_quantity", "authorized_quantity",
    "previous_executed_quantity", "executed_avg_price_cents",
    "authorized_price_ceiling_cents",
]

CONNECTORS = [
    {
        "name": "trading-b1-evidence",
        "provider": "postgresql",
        "boundary_ref": B1,
        "config": {},
        "credentials": {},
        "evidence_mappings": _raw_mappings(B1_FIELDS),
        "allowed_acquisition_classes": ["caller_supplied_payload"],
    },
    {
        "name": "trading-b2-evidence",
        "provider": "postgresql",
        "boundary_ref": B2,
        "config": {},
        "credentials": {},
        "evidence_mappings": _raw_mappings(B2_FIELDS),
        "allowed_acquisition_classes": ["caller_supplied_payload"],
    },
    {
        "name": "trading-b3-evidence",
        "provider": "postgresql",
        "boundary_ref": B3,
        "config": {},
        "credentials": {},
        "evidence_mappings": _raw_mappings(B3_FIELDS),
        "allowed_acquisition_classes": ["caller_supplied_payload"],
    },
    {
        "name": "trading-b4-evidence",
        "provider": "postgresql",
        "boundary_ref": B4,
        "config": {},
        "credentials": {},
        "evidence_mappings": _raw_mappings(B4_FIELDS),
        "allowed_acquisition_classes": ["caller_supplied_payload"],
    },
]


CERTIFICATION_VECTOR = {
    "order_ref": "CERT-B1-ADMISSIBILITY",
    "account_id": "ACC-ALPHA-1",
    "instrument_id": "INST-AAPL",
    "account_status": "active",
    "side": "buy",
    "asset_class": "equity",
    "quantity": 150,
    "order_notional_cents": 3405000,
    "buying_power_cents": 200000000,
    "order_type": "limit",
    "market_session_open": 1,
}

CERTIFICATION_DEFINITIONS = [
    {"field": f, "type": "string" if isinstance(v, str) else "integer"}
    for f, v in CERTIFICATION_VECTOR.items()
]


# ── API helpers ──────────────────────────────────────────────────────────


def api(method: str, path: str, body: dict | None = None, attempts: int = 4):
    for attempt in range(attempts):
        req = urllib.request.Request(
            BASE + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < attempts - 1:
                time.sleep(1 + attempt)
                continue
            print(f"  HTTP {e.code} {method} {path}: {e.read().decode()[:300]}")
            return None
    return None


def rows(response: dict | None) -> list:
    if not response:
        return []
    return response.get("data") or response.get("items") or []


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=1) + "\n")


# ── Provisioning ─────────────────────────────────────────────────────────


def provision_boundary(ref: str, name: str, description: str, state: dict) -> None:
    dsl = (ROOT / "boundaries" / f"{ref}.dsl").read_text()

    listing = api("GET", f"/v1/projects/{PROJECT_ID}/boundaries") or {}
    boundary_id = next(
        (b["id"] for b in listing.get("data", []) if b["boundary_ref"] == ref), None
    )

    if not boundary_id:
        created = api("POST", f"/v1/projects/{PROJECT_ID}/boundaries", {
            "boundary_ref": ref,
            "name": name,
            "description": description,
            "dsl_source": dsl,
        })
        if created is None:
            sys.exit(f"boundary creation failed: {ref}")
        boundary_id = created["id"]
        print(f"  {ref}: created")
    else:
        print(f"  {ref}: exists")

    versions = api("GET", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions")
    latest = versions["data"][0]
    detail = api(
        "GET", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions/{latest['id']}"
    ) or {}

    if detail.get("dsl_source", "").strip() != dsl.strip():
        latest = api("POST", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions", {
            "dsl_source": dsl,
            "change_description": "Sync from boundaries/ source of truth",
        })
        print(f"  {ref}: new version {latest['version_number']}")

    version_id, status = latest["id"], latest["status"]

    if status == "DRAFT":
        api("POST", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions/{version_id}/submit", {})
        status = "SUBMITTED"
    if status == "SUBMITTED":
        api("POST", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions/{version_id}/approve", {})
        status = "APPROVED"

    if status in ("APPROVED", "ACTIVE"):
        deployments = api("GET", f"/v1/projects/{PROJECT_ID}/environments/{ENV_ID}/deployments") or {}
        active = any(
            d.get("boundary_version_id") == version_id and d.get("status") == "ACTIVE"
            for d in deployments.get("data", [])
        )
        if active:
            print(f"  {ref}: already deployed")
        else:
            dep = api("POST", f"/v1/projects/{PROJECT_ID}/environments/{ENV_ID}/deployments",
                      {"boundary_version_id": version_id})
            if dep and dep.get("status") == "PENDING_APPROVAL":
                api("POST",
                    f"/v1/projects/{PROJECT_ID}/environments/{ENV_ID}/deployments/{dep['id']}/approve",
                    {"comment": "Trading reference bootstrap"})
            print(f"  {ref}: deployed")

    state.setdefault("boundaries", {})[ref] = {
        "boundary_id": boundary_id,
        "version_id": version_id,
    }


def provision_connectors(state: dict) -> None:
    listing = api("GET", f"/v1/projects/{PROJECT_ID}/connectors") or {}
    by_name = {c["name"]: c for c in listing.get("data", [])}
    connector_ids = state.get("connector_ids", {})

    for spec in CONNECTORS:
        name, config = spec["name"], spec["config"]
        existing = by_name.get(name)

        if existing:
            connector_id = existing["id"]
            patch: dict = {}
            if existing.get("config") != config:
                patch["config"] = config
            creds = spec.get("credentials", {})
            if any(v for v in creds.values()):
                patch["credentials"] = creds
            if patch:
                api("PATCH", f"/v1/projects/{PROJECT_ID}/connectors/{connector_id}", patch)
                print(f"  {name}: updated ({', '.join(patch.keys())})")
            else:
                print(f"  {name}: exists")
        else:
            body = {
                "name": name,
                "provider": spec["provider"],
                "config": config,
                "credentials": spec["credentials"],
                "allowed_acquisition_classes": spec.get(
                    "allowed_acquisition_classes", ["active_provider"]
                ),
                "evidence_mappings": spec["evidence_mappings"],
            }
            created = api("POST", f"/v1/projects/{PROJECT_ID}/connectors", body)
            if created is None:
                sys.exit(f"connector creation failed: {name}")
            connector_id = created["id"]
            print(f"  {name}: created -> {spec['boundary_ref']}")

        api("PUT", f"/v1/projects/{PROJECT_ID}/connectors/{connector_id}/schedule", {
            "auto_evaluate_enabled": True,
            "target_boundary_ref": spec["boundary_ref"],
            "target_environment_id": ENV_ID,
        })
        connector_ids[name] = connector_id

    state["connector_ids"] = connector_ids


def _governed_flow(
    connector_id: str,
    boundary_ref: str,
    boundary_version_id: str,
    assert_message: str,
    defer_title: str,
) -> dict:
    return {
        "schema_version": 1,
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "config": {"connector_id": connector_id},
            },
            {
                "id": "boundary-1",
                "type": "boundary",
                "config": {
                    "boundary_ref": boundary_ref,
                    "boundary_version_id": boundary_version_id,
                },
            },
            {
                "id": "assert-1",
                "type": "assert_action",
                "config": {"action_type": "log", "message": assert_message},
            },
            {
                "id": "defer-1",
                "type": "defer_action",
                "config": {"action_type": "notification", "title": defer_title},
            },
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "boundary-1"},
            {"id": "e2", "source": "boundary-1", "target": "assert-1", "label": "assert"},
            {"id": "e3", "source": "boundary-1", "target": "defer-1", "label": "defer"},
        ],
    }


def provision_flows(state: dict) -> None:
    boundaries = state["boundaries"]
    connector_ids = state["connector_ids"]

    flow_specs = [
        (
            "Order Admissibility Governance",
            "Fires on B1. ASSERT logs order admission into the governance pipeline.",
            _governed_flow(
                connector_ids["trading-b1-evidence"],
                B1, boundaries[B1]["version_id"],
                "Order admitted into governance pipeline",
                "Order inadmissible -- review required",
            ),
        ),
        (
            "Compliance Authorization Governance",
            "Fires on B2. ASSERT logs compliance authorization.",
            _governed_flow(
                connector_ids["trading-b2-evidence"],
                B2, boundaries[B2]["version_id"],
                "Order passes compliance and mandate checks",
                "Compliance violation detected -- order blocked",
            ),
        ),
        (
            "Execution Authorization Governance",
            "Fires on B3. ASSERT authorizes execution submission.",
            _governed_flow(
                connector_ids["trading-b3-evidence"],
                B3, boundaries[B3]["version_id"],
                "Order authorized for execution submission",
                "Execution authorization denied -- order blocked",
            ),
        ),
        (
            "Execution Reconciliation Governance",
            "Fires on B4. ASSERT confirms conformant execution; DEFER flags anomaly.",
            _governed_flow(
                connector_ids["trading-b4-evidence"],
                B4, boundaries[B4]["version_id"],
                "Execution conforms to authorization",
                "Execution anomaly detected -- reconciliation required",
            ),
        ),
    ]

    by_name = {f["name"]: f for f in rows(api("GET", f"/v1/projects/{PROJECT_ID}/flows"))}
    flow_ids = state.get("flow_ids", {})

    for name, description, definition in flow_specs:
        existing = by_name.get(name)
        if existing:
            flow_id = existing["id"]
            print(f"  {name}: exists")
        else:
            created = api("POST", f"/v1/projects/{PROJECT_ID}/flows",
                          {"name": name, "description": description})
            if created is None:
                sys.exit(f"flow creation failed: {name}")
            flow_id = created["id"]
            print(f"  {name}: created")

        versions = rows(api("GET", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions"))
        if versions:
            latest = versions[0]
            detail = api("GET",
                         f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions/{latest['id']}")
            existing_def = (detail or {}).get("definition", {})
            if json.dumps(existing_def, sort_keys=True) == json.dumps(definition, sort_keys=True):
                flow_ids[name] = flow_id
                print(f"    version up to date")
                continue
            print(f"    definition changed -- creating new version")

        version = api("POST", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions",
                      {"definition": definition})
        if version is None:
            sys.exit(f"flow version failed: {name}")
        vid = version["id"]
        api("POST", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions/{vid}/approve", {})
        api("PATCH", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}", {"status": "ACTIVE"})
        deployed = api("POST", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions/{vid}/deploy",
                       {"environment_id": ENV_ID})
        if deployed is None:
            sys.exit(f"flow deploy failed: {name}")
        print(f"  {name}: deployed")
        flow_ids[name] = flow_id

    state["flow_ids"] = flow_ids


def provision_certifications(state: dict) -> None:
    existing = {m["name"]: m for m in rows(
        api("GET", f"/v1/projects/{PROJECT_ID}/certification-models"))}
    model_ids = state.get("certification_model_ids", {})

    name = "Order Admissibility Certification"
    model = existing.get(name)
    if model:
        model_id = model["id"]
        print(f"  {name}: exists")
    else:
        created = api("POST", f"/v1/projects/{PROJECT_ID}/certification-models",
                      {"name": name, "description": f"Frozen conformance vector for {B1}"})
        if created is None:
            sys.exit(f"certification model creation failed: {name}")
        model_id = created["id"]
        print(f"  {name}: created")
    model_ids[name] = model_id

    if not rows(api("GET",
                    f"/v1/projects/{PROJECT_ID}/certification-models/{model_id}/versions")):
        version = api(
            "POST",
            f"/v1/projects/{PROJECT_ID}/certification-models/{model_id}/versions",
            {
                "evidence_definitions": CERTIFICATION_DEFINITIONS,
                "boundary_version_id": state["boundaries"][B1]["version_id"],
                "change_description": "Initial certification model version",
            },
        )
        if version is None:
            sys.exit(f"certification version failed: {name} -- check the "
                     "manage_certifications API key scope")

    run = api("POST", f"/v1/projects/{PROJECT_ID}/certify",
              {"model": name, "environment": "Development", "evidence": CERTIFICATION_VECTOR})
    if run is None:
        sys.exit(f"certification run failed: {name}")

    status = run.get("status")
    for _ in range(15):
        if status in ("PASSED", "FAILED", "ERROR", "CANCELLED"):
            break
        time.sleep(2)
        poll = api("GET", f"/v1/projects/{PROJECT_ID}/certifications/{run['id']}")
        status = (poll or {}).get("status", status)
    print(f"    run {status}")
    if status != "PASSED":
        sys.exit(f"certification did not pass: {name} ({status})")

    state["certification_model_ids"] = model_ids


# ── Entry point ──────────────────────────────────────────────────────────


def main() -> int:
    state = load_state()
    state["project_id"] = PROJECT_ID
    state["environment_id"] = ENV_ID

    print("Boundaries:")
    for ref, name, description in BOUNDARIES:
        provision_boundary(ref, name, description, state)

    print("Connectors:")
    provision_connectors(state)

    print("Flows:")
    provision_flows(state)

    print("Certifications:")
    provision_certifications(state)

    save_state(state)
    print(f"\nState written to {STATE_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
