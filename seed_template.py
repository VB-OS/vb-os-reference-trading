# Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.
# Author: Asaad Riaz
"""Register the Trading Order Governance reference template in the VB-OS Cloud template registry.

Creates a multi-boundary template with 4 boundaries, 4 connectors, 4 flows,
and a certification model. Users can fork this template to provision the entire
governed trading pipeline with a single click.

Usage:
    python3 seed_template.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import asyncpg

ROOT = Path(__file__).parent


def _resolve_dsn() -> str:
    import os
    import re
    from urllib.parse import quote

    raw = os.environ.get(
        "VBOS_DATABASE_URL", "postgresql://vbos:vbos@localhost:5432/vbos"
    )
    url = raw.replace("postgresql+asyncpg://", "postgresql://")
    m = re.match(r"^(postgresql://[^:]+:)([^@]+)(@.+)$", url)
    if m:
        url = m.group(1) + quote(m.group(2), safe="") + m.group(3)
    return url


DB_DSN = _resolve_dsn()
SLUG = "trading-order-governance"


def _read_dsl(filename: str) -> str:
    return (ROOT / "boundaries" / filename).read_text()


# ── Evidence schemas ──────────────────────────────────────────────────────

B1_EVIDENCE_SCHEMA = {
    "order_ref": {"type": "string"},
    "account_id": {"type": "string"},
    "instrument_id": {"type": "string"},
    "account_status": {"type": "string"},
    "side": {"type": "string"},
    "asset_class": {"type": "string"},
    "quantity": {"type": "integer"},
    "order_notional_cents": {"type": "integer"},
    "buying_power_cents": {"type": "integer"},
    "order_type": {"type": "string"},
    "market_session_open": {"type": "integer"},
}

B2_EVIDENCE_SCHEMA = {
    "order_ref": {"type": "string"},
    "account_id": {"type": "string"},
    "instrument_id": {"type": "string"},
    "side": {"type": "string"},
    "quantity": {"type": "integer"},
    "asset_class": {"type": "string"},
    "order_notional_cents": {"type": "integer"},
    "order_type": {"type": "string"},
    "mandate_match": {"type": "integer"},
    "concentration_bps_after": {"type": "integer"},
    "concentration_limit_bps": {"type": "integer"},
    "projected_position_quantity": {"type": "integer"},
    "position_limit_quantity": {"type": "integer"},
    "last_opposite_side_seconds": {"type": "integer"},
    "cooldown_window_seconds": {"type": "integer"},
}

B3_EVIDENCE_SCHEMA = {
    "account_id": {"type": "string"},
    "instrument_id": {"type": "string"},
    "asset_class": {"type": "string"},
    "side": {"type": "string"},
    "quantity": {"type": "integer"},
    "quantity_unit": {"type": "string"},
    "order_type": {"type": "string"},
    "order_ref": {"type": "string"},
    "order_notional_cents": {"type": "integer"},
    "trader_authority_cents": {"type": "integer"},
    "desk_exposure_cents": {"type": "integer"},
    "desk_limit_cents": {"type": "integer"},
    "combined_desk_and_order_cents": {"type": "integer"},
    "limit_price_cents": {"type": "integer"},
    "reference_price_cents": {"type": "integer"},
    "price_tolerance_bps": {"type": "integer"},
    "price_deviation_above_scaled": {"type": "integer"},
    "price_deviation_below_scaled": {"type": "integer"},
    "tolerance_threshold_scaled": {"type": "integer"},
    "margin_requirement_cents": {"type": "integer"},
    "available_margin_cents": {"type": "integer"},
    "authorization_valid_seconds": {"type": "integer"},
    "b1_evaluation_id": {"type": "string"},
    "b2_evaluation_id": {"type": "string"},
}

B4_EVIDENCE_SCHEMA = {
    "venue_authorization_reference": {"type": "string"},
    "expected_authorization_reference": {"type": "string"},
    "execution_status": {"type": "string"},
    "executed_quantity": {"type": "integer"},
    "authorized_quantity": {"type": "integer"},
    "previous_executed_quantity": {"type": "integer"},
    "executed_avg_price_cents": {"type": "integer"},
    "authorized_price_ceiling_cents": {"type": "integer"},
}


# ── Boundary set ──────────────────────────────────────────────────────────

BOUNDARY_SET = [
    {
        "ref": "B_ORDER_ADMISSIBILITY",
        "name": "Order Admissibility",
        "description": (
            "B1 -- is this proposed order structurally and operationally "
            "admissible before it enters the governance pipeline?"
        ),
        "dsl_source": _read_dsl("B_ORDER_ADMISSIBILITY.dsl"),
        "evidence_schema": B1_EVIDENCE_SCHEMA,
    },
    {
        "ref": "B_COMPLIANCE_AUTHORIZATION",
        "name": "Compliance and Mandate Authorization",
        "description": (
            "B2 -- does this order satisfy applicable restrictions, "
            "investment mandates, and policy constraints?"
        ),
        "dsl_source": _read_dsl("B_COMPLIANCE_AUTHORIZATION.dsl"),
        "evidence_schema": B2_EVIDENCE_SCHEMA,
    },
    {
        "ref": "B_EXECUTION_AUTHORIZATION",
        "name": "Execution Authorization",
        "description": (
            "B3 -- does the trader have authority to submit this precisely "
            "bounded order, given current risk limits and market conditions?"
        ),
        "dsl_source": _read_dsl("B_EXECUTION_AUTHORIZATION.dsl"),
        "evidence_schema": B3_EVIDENCE_SCHEMA,
    },
    {
        "ref": "B_EXECUTION_RECONCILIATION",
        "name": "Execution Reconciliation",
        "description": (
            "B4 -- does the observed execution conform to the authorization "
            "that was granted? ASSERT = conformant, DEFER = anomaly."
        ),
        "dsl_source": _read_dsl("B_EXECUTION_RECONCILIATION.dsl"),
        "evidence_schema": B4_EVIDENCE_SCHEMA,
    },
]


# ── Connector configs ─────────────────────────────────────────────────────

def _raw_mappings(schema: dict) -> list[dict]:
    return [
        {"evidence_field": field, "source_path": field}
        for field in schema
    ]


CONNECTOR_CONFIGS = [
    {
        "name": "trading-b1-evidence",
        "provider": "postgresql",
        "description": (
            "Order admissibility evidence from the OMS database. "
            "Caller-supplied payload: the orchestrator pushes evidence directly."
        ),
        "target_boundary_ref": "B_ORDER_ADMISSIBILITY",
        "allowed_acquisition_classes": ["caller_supplied_payload"],
        "evidence_mappings": _raw_mappings(B1_EVIDENCE_SCHEMA),
    },
    {
        "name": "trading-b2-evidence",
        "provider": "postgresql",
        "description": (
            "Compliance and mandate evidence from the compliance and risk systems. "
            "Caller-supplied payload: the orchestrator pushes evidence directly."
        ),
        "target_boundary_ref": "B_COMPLIANCE_AUTHORIZATION",
        "allowed_acquisition_classes": ["caller_supplied_payload"],
        "evidence_mappings": _raw_mappings(B2_EVIDENCE_SCHEMA),
    },
    {
        "name": "trading-b3-evidence",
        "provider": "postgresql",
        "description": (
            "Execution authorization evidence from the OMS, risk, and market data "
            "systems. Includes B1/B2 evaluation IDs for authorization lineage. "
            "Caller-supplied payload: the orchestrator pushes evidence directly."
        ),
        "target_boundary_ref": "B_EXECUTION_AUTHORIZATION",
        "allowed_acquisition_classes": ["caller_supplied_payload"],
        "evidence_mappings": _raw_mappings(B3_EVIDENCE_SCHEMA),
    },
    {
        "name": "trading-b4-evidence",
        "provider": "postgresql",
        "description": (
            "Execution reconciliation evidence from the execution gateway. "
            "Venue-reported fields, platform-derived fields, and gateway state. "
            "Caller-supplied payload: the gateway pushes evidence directly."
        ),
        "target_boundary_ref": "B_EXECUTION_RECONCILIATION",
        "allowed_acquisition_classes": ["caller_supplied_payload"],
        "evidence_mappings": _raw_mappings(B4_EVIDENCE_SCHEMA),
    },
]


# ── Flow definitions ──────────────────────────────────────────────────────

def _governed_flow(
    connector_name: str,
    boundary_ref: str,
    assert_message: str,
    defer_title: str,
) -> dict:
    return {
        "schema_version": 1,
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "config": {"connector_name": connector_name},
            },
            {
                "id": "boundary-1",
                "type": "boundary",
                "config": {"boundary_ref": boundary_ref},
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


FLOW_DEFINITIONS = [
    {
        "name": "Order Admissibility Governance",
        "description": "Fires on B1. ASSERT logs order admission into the governance pipeline.",
        "definition": _governed_flow(
            connector_name="trading-b1-evidence",
            boundary_ref="B_ORDER_ADMISSIBILITY",
            assert_message="Order admitted into governance pipeline",
            defer_title="Order inadmissible -- review required",
        ),
    },
    {
        "name": "Compliance Authorization Governance",
        "description": "Fires on B2. ASSERT logs compliance authorization.",
        "definition": _governed_flow(
            connector_name="trading-b2-evidence",
            boundary_ref="B_COMPLIANCE_AUTHORIZATION",
            assert_message="Order passes compliance and mandate checks",
            defer_title="Compliance violation detected -- order blocked",
        ),
    },
    {
        "name": "Execution Authorization Governance",
        "description": "Fires on B3. ASSERT authorizes execution submission.",
        "definition": _governed_flow(
            connector_name="trading-b3-evidence",
            boundary_ref="B_EXECUTION_AUTHORIZATION",
            assert_message="Order authorized for execution submission",
            defer_title="Execution authorization denied -- order blocked",
        ),
    },
    {
        "name": "Execution Reconciliation Governance",
        "description": "Fires on B4. ASSERT confirms conformant execution; DEFER flags anomaly.",
        "definition": _governed_flow(
            connector_name="trading-b4-evidence",
            boundary_ref="B_EXECUTION_RECONCILIATION",
            assert_message="Execution conforms to authorization",
            defer_title="Execution anomaly detected -- reconciliation required",
        ),
    },
]


# ── Certification model ───────────────────────────────────────────────────

CERTIFICATION_MODEL = {
    "name": "Order Admissibility Certification",
    "description": "Frozen conformance vector for the B1 order admissibility boundary",
    "evidence_definitions": B1_EVIDENCE_SCHEMA,
}


# ── Example workloads ─────────────────────────────────────────────────────

EXAMPLE_WORKLOADS = [
    {
        "boundary_ref": "B_ORDER_ADMISSIBILITY",
        "label": "Clean equity buy (ASSERT)",
        "expected_decision": "ASSERT",
        "payload": {
            "order_ref": "ORD-EXAMPLE-B1",
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
        },
    },
    {
        "boundary_ref": "B_COMPLIANCE_AUTHORIZATION",
        "label": "Clean compliance pass (ASSERT)",
        "expected_decision": "ASSERT",
        "payload": {
            "order_ref": "ORD-EXAMPLE-B2",
            "account_id": "ACC-ALPHA-1",
            "instrument_id": "INST-AAPL",
            "side": "buy",
            "quantity": 150,
            "asset_class": "equity",
            "order_notional_cents": 3405000,
            "order_type": "limit",
            "mandate_match": 1,
            "concentration_bps_after": 500,
            "concentration_limit_bps": 1500,
            "projected_position_quantity": 150,
            "position_limit_quantity": 100000,
            "last_opposite_side_seconds": 86400,
            "cooldown_window_seconds": 600,
        },
    },
    {
        "boundary_ref": "B_EXECUTION_AUTHORIZATION",
        "label": "Clean execution authorization (ASSERT)",
        "expected_decision": "ASSERT",
        "payload": {
            "account_id": "ACC-ALPHA-1",
            "instrument_id": "INST-AAPL",
            "asset_class": "equity",
            "side": "buy",
            "quantity": 150,
            "quantity_unit": "shares",
            "order_type": "limit",
            "order_ref": "ORD-EXAMPLE-B3",
            "order_notional_cents": 3405000,
            "trader_authority_cents": 5000000,
            "desk_exposure_cents": 40000000,
            "desk_limit_cents": 100000000,
            "combined_desk_and_order_cents": 43405000,
            "limit_price_cents": 22700,
            "reference_price_cents": 22700,
            "price_tolerance_bps": 500,
            "price_deviation_above_scaled": 0,
            "price_deviation_below_scaled": 0,
            "tolerance_threshold_scaled": 11350000,
            "margin_requirement_cents": 500000,
            "available_margin_cents": 5000000,
            "authorization_valid_seconds": 300,
            "b1_evaluation_id": "placeholder-b1-eval-id",
            "b2_evaluation_id": "placeholder-b2-eval-id",
        },
    },
    {
        "boundary_ref": "B_EXECUTION_RECONCILIATION",
        "label": "Full fill conformant (ASSERT)",
        "expected_decision": "ASSERT",
        "payload": {
            "venue_authorization_reference": "example-eval-id",
            "expected_authorization_reference": "example-eval-id",
            "execution_status": "filled",
            "executed_quantity": 150,
            "authorized_quantity": 150,
            "previous_executed_quantity": 0,
            "executed_avg_price_cents": 22650,
            "authorized_price_ceiling_cents": 22700,
        },
    },
]


# ── Template metadata ─────────────────────────────────────────────────────

TEMPLATE = {
    "slug": SLUG,
    "name": "Trading Order Governance",
    "category": "Finance",
    "domain": "finance",
    "description": (
        "Four-boundary governed trading pipeline through VB-OS. "
        "Covers order admissibility (B1), compliance authorization (B2), "
        "execution authorization (B3), and execution reconciliation (B4). "
        "Includes caller-supplied-payload connectors for OMS, compliance, "
        "risk, and venue evidence, plus four governed dispatch flows."
    ),
    "outcome_statement": (
        "A governed trading pipeline where every order authorization is "
        "independently verified, every execution is reconciled against its "
        "authorization, and every decision is preserved with its evidence "
        "for deterministic replay."
    ),
    "dsl_source": _read_dsl("B_ORDER_ADMISSIBILITY.dsl"),
    "evidence_schema": B1_EVIDENCE_SCHEMA,
    "boundary_set": BOUNDARY_SET,
    "connector_configs": CONNECTOR_CONFIGS,
    "flow_definitions": FLOW_DEFINITIONS,
    "certification_model": CERTIFICATION_MODEL,
    "example_workloads": EXAMPLE_WORKLOADS,
    "integration_code": {},
}


async def main() -> None:
    conn = await asyncpg.connect(DB_DSN)

    existing = await conn.fetchval(
        "SELECT id FROM templates WHERE slug = $1", SLUG
    )

    if existing:
        await conn.execute(
            """UPDATE templates SET
                name = $2,
                category = $3,
                domain = $4,
                description = $5,
                outcome_statement = $6,
                dsl_source = $7,
                evidence_schema = $8,
                boundary_set = $9,
                connector_configs = $10,
                flow_definitions = $11,
                certification_model = $12,
                example_workloads = $13,
                integration_code = $14,
                updated_at = now()
            WHERE slug = $1""",
            TEMPLATE["slug"],
            TEMPLATE["name"],
            TEMPLATE["category"],
            TEMPLATE["domain"],
            TEMPLATE["description"],
            TEMPLATE["outcome_statement"],
            TEMPLATE["dsl_source"],
            json.dumps(TEMPLATE["evidence_schema"]),
            json.dumps(TEMPLATE["boundary_set"]),
            json.dumps(TEMPLATE["connector_configs"]),
            json.dumps(TEMPLATE["flow_definitions"]),
            json.dumps(TEMPLATE["certification_model"]),
            json.dumps(TEMPLATE["example_workloads"]),
            json.dumps(TEMPLATE["integration_code"]),
        )
        print(f"Updated existing template: {SLUG}")
    else:
        await conn.execute(
            """INSERT INTO templates (
                id, slug, name, category, domain, description,
                outcome_statement, dsl_source, evidence_schema,
                boundary_set, connector_configs, flow_definitions,
                certification_model, example_workloads, integration_code,
                status, version, is_official, fork_count, visibility,
                next_version_number, evidence_field_count,
                prohibited_evidence_count, predicate_count,
                onboarding_eligible, created_at, updated_at
            ) VALUES (
                gen_random_uuid(), $1, $2, $3, $4, $5,
                $6, $7, $8,
                $9, $10, $11,
                $12, $13, $14,
                'PUBLISHED', 1, true, 0, 'PUBLIC',
                2, 11, 1, 10, false, now(), now()
            )""",
            TEMPLATE["slug"],
            TEMPLATE["name"],
            TEMPLATE["category"],
            TEMPLATE["domain"],
            TEMPLATE["description"],
            TEMPLATE["outcome_statement"],
            TEMPLATE["dsl_source"],
            json.dumps(TEMPLATE["evidence_schema"]),
            json.dumps(TEMPLATE["boundary_set"]),
            json.dumps(TEMPLATE["connector_configs"]),
            json.dumps(TEMPLATE["flow_definitions"]),
            json.dumps(TEMPLATE["certification_model"]),
            json.dumps(TEMPLATE["example_workloads"]),
            json.dumps(TEMPLATE["integration_code"]),
        )
        print(f"Created template: {SLUG}")

    row = await conn.fetchrow(
        """SELECT name, status,
            jsonb_array_length(boundary_set) as boundaries,
            jsonb_array_length(connector_configs) as connectors,
            jsonb_array_length(flow_definitions) as flows,
            certification_model->>'name' as cert_name
        FROM templates WHERE slug = $1""",
        SLUG,
    )
    print(f"  Name: {row['name']}")
    print(f"  Status: {row['status']}")
    print(f"  Boundaries: {row['boundaries']}")
    print(f"  Connectors: {row['connectors']}")
    print(f"  Flows: {row['flows']}")
    print(f"  Certification: {row['cert_name']}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
