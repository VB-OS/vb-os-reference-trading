"""Execution gateway for VB-OS Trading Reference Integration.

Performs three verification operations on sealed B3 authorization artifacts,
enforces authorization consumption semantics, submits orders to Alpaca,
and runs post-execution reconciliation via a transactional outbox.

Runs as a persistent HTTP server inside Docker.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("gateway")

VBOS_API_URL = os.environ.get("VBOS_API_URL", "https://api.vbos.cloud")
VBOS_API_KEY = os.environ["VBOS_API_KEY"]
VBOS_PROJECT_ID = os.environ["VBOS_PROJECT_ID"]
VBOS_ENVIRONMENT_ID = os.environ["VBOS_ENVIRONMENT_ID"]
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
GATEWAY_DB_PATH = os.environ.get("GATEWAY_DB_PATH", "/data/gateway.db")
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "9093"))

APPROVED_PAPER_ENDPOINTS = [
    {"scheme": "https", "hostname": "paper-api.alpaca.markets"},
    {"scheme": "https", "hostname": "broker-api.sandbox.alpaca.markets"},
]

AUTHORIZATION_VALID_DEFAULT = 300
UNCERTAIN_SETTLEMENT_THRESHOLD = 120
UNCERTAIN_MAX_RETRIES = 3
RECONCILIATION_INTERVAL = 60

B1_BOUNDARY_REF = "B_ORDER_ADMISSIBILITY"
B2_BOUNDARY_REF = "B_COMPLIANCE_AUTHORIZATION"
B3_BOUNDARY_REF = "B_EXECUTION_AUTHORIZATION"
B4_BOUNDARY_REF = "B_EXECUTION_RECONCILIATION"

MATERIAL_ORDER_FIELDS = [
    "account_id", "instrument_id", "side", "quantity",
    "asset_class", "order_notional_cents", "order_type",
]


# ── Instrument registry (loaded from env or hardcoded for reference) ──────

INSTRUMENTS = {}


def load_instruments_from_db(db_url: str) -> None:
    """Load instrument data from PostgreSQL for gateway-side verification."""
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT instrument_id, symbol, asset_class, quantity_unit, quantity_divisor FROM instruments")
        for row in cur.fetchall():
            INSTRUMENTS[row[0]] = {
                "instrument_id": row[0],
                "symbol": row[1],
                "asset_class": row[2],
                "quantity_unit": row[3],
                "quantity_divisor": row[4],
            }
        cur.close()
        conn.close()
        log.info("Loaded %d instruments from database", len(INSTRUMENTS))
    except Exception as e:
        log.warning("Could not load instruments from database: %s", e)


# ── SQLite state ──────────────────────────────────────────────────────────

_db_lock = threading.Lock()


def _init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(GATEWAY_DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS placements (
            dispatch_id TEXT PRIMARY KEY,
            authorization_evaluation_id TEXT NOT NULL UNIQUE,
            alpaca_order_id TEXT,
            order_side TEXT,
            status TEXT NOT NULL DEFAULT 'CLAIMED',
            created_at TEXT NOT NULL,
            last_seen_filled_qty INTEGER DEFAULT 0,
            last_seen_avg_price_cents INTEGER DEFAULT 0,
            anomaly_count INTEGER DEFAULT 0,
            last_anomaly_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS b4_outbox (
            outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
            placement_dispatch_id TEXT NOT NULL REFERENCES placements(dispatch_id),
            b4_evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            pushed_at TEXT
        )
    """)
    conn.commit()
    return conn


_db_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        _db_conn = _init_db()
    return _db_conn


# ── VB-OS Cloud API ───────────────────────────────────────────────────────

def vbos_api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{VBOS_API_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {VBOS_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise
    return {}


def read_sealed_evaluation(evaluation_id: str) -> dict:
    path = f"/v1/projects/{VBOS_PROJECT_ID}/evaluations/{evaluation_id}"
    return vbos_api("GET", path)


def get_active_deployment(boundary_id: str) -> dict:
    path = (
        f"/v1/projects/{VBOS_PROJECT_ID}/environments/{VBOS_ENVIRONMENT_ID}"
        f"/deployments/active?boundary_id={boundary_id}"
    )
    return vbos_api("GET", path)


def push_b4_evidence(connector_id: str, evidence: dict) -> dict:
    path = f"/v1/projects/{VBOS_PROJECT_ID}/connectors/{connector_id}/gather"
    return vbos_api("POST", path, {"raw_payload": evidence})


# ── Alpaca API ────────────────────────────────────────────────────────────

def _validate_broker_url() -> None:
    parsed = urllib.parse.urlparse(ALPACA_BASE_URL)
    if parsed.scheme != "https":
        raise ValueError(f"Broker URL must use HTTPS: {ALPACA_BASE_URL}")
    if parsed.port is not None:
        raise ValueError(f"Broker URL must not specify a port: {ALPACA_BASE_URL}")
    if ".." in (parsed.path or ""):
        raise ValueError(f"Broker URL contains path traversal: {ALPACA_BASE_URL}")
    if parsed.username or parsed.password:
        raise ValueError(f"Broker URL contains userinfo: {ALPACA_BASE_URL}")
    if parsed.query:
        raise ValueError(f"Broker URL contains query string: {ALPACA_BASE_URL}")
    if parsed.fragment:
        raise ValueError(f"Broker URL contains fragment: {ALPACA_BASE_URL}")
    hostname = (parsed.hostname or "").lower()
    try:
        import ipaddress
        ipaddress.ip_address(hostname)
        raise ValueError(f"Broker URL must use a named host, not IP: {ALPACA_BASE_URL}")
    except ValueError as ip_err:
        if "must use a named host" in str(ip_err):
            raise
    matched = any(
        e["scheme"] == parsed.scheme and e["hostname"] == hostname
        for e in APPROVED_PAPER_ENDPOINTS
    )
    if not matched:
        raise ValueError(f"Broker URL not in approved list: {ALPACA_BASE_URL}")


def alpaca_api(method: str, path: str, body: dict | None = None) -> dict | None:
    url = f"{ALPACA_BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "APCA-API-KEY-ID": ALPACA_API_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        return {"error": True, "status_code": e.code, "body": body_text}
    except Exception:
        return None


# ── Notional derivation ──────────────────────────────────────────────────

def derive_notional(side: str, order_type: str, quantity: int,
                    price_cents: int, quantity_divisor: int) -> int:
    if order_type == "limit" and side == "buy":
        return (quantity * price_cents + quantity_divisor - 1) // quantity_divisor
    else:
        return quantity * price_cents // quantity_divisor


# ── Price conversion ─────────────────────────────────────────────────────

def parse_fill_price(price_str: str, side: str) -> int:
    d = Decimal(price_str) * 100
    if side == "buy":
        int_part = int(d)
        if d % 1 > 0:
            int_part += 1
        return int_part
    else:
        return int(d)


# ── Authorization verification ────────────────────────────────────────────

class VerificationResult:
    def __init__(self, authorized: bool, details: dict):
        self.authorized = authorized
        self.details = details

    def to_dict(self) -> dict:
        return {"authorized": self.authorized, **self.details}


def verify_authorization(evaluation_id: str, proposed_order: dict | None = None) -> VerificationResult:
    """Shared verification function used by both live and simulated paths.

    Performs three operations:
    1. Authorization chain integrity (B1, B2, B3 all ASSERT, material match)
    2. Arithmetic consistency (pre-computed field derivation)
    3. Submission binding (proposed order matches sealed authorization)
    """
    try:
        b3_eval = read_sealed_evaluation(evaluation_id)
    except Exception as e:
        return VerificationResult(False, {"error": f"Cannot read B3 evaluation: {e}"})

    b3_data = b3_eval.get("data", b3_eval)
    if b3_data.get("decision") != "ASSERT":
        return VerificationResult(False, {"error": "B3 decision is not ASSERT"})
    if b3_data.get("boundary_ref") != B3_BOUNDARY_REF:
        return VerificationResult(False, {"error": f"B3 boundary_ref mismatch: {b3_data.get('boundary_ref')}"})

    artifact = b3_data.get("artifact", {})
    workload = artifact.get("workload", {})
    if not workload:
        evidence = b3_data.get("evidence", {})
        workload = evidence if evidence else {}

    b1_eval_id = workload.get("b1_evaluation_id")
    b2_eval_id = workload.get("b2_evaluation_id")
    if not b1_eval_id or not b2_eval_id:
        return VerificationResult(False, {"error": "Missing b1/b2 evaluation IDs in B3 workload"})

    # -- Operation 2a: Verify B1 authorization chain --
    try:
        b1_eval = read_sealed_evaluation(b1_eval_id)
    except Exception as e:
        return VerificationResult(False, {"error": f"Cannot read B1 evaluation: {e}"})

    b1_data = b1_eval.get("data", b1_eval)
    if b1_data.get("decision") != "ASSERT":
        return VerificationResult(False, {"error": "B1 decision is not ASSERT"})
    if b1_data.get("boundary_ref") != B1_BOUNDARY_REF:
        return VerificationResult(False, {"error": f"B1 boundary_ref mismatch: {b1_data.get('boundary_ref')}"})

    b1_workload = b1_data.get("artifact", {}).get("workload", {})
    if not b1_workload:
        b1_workload = b1_data.get("evidence", {})

    # -- Operation 2b: Verify B2 authorization chain --
    try:
        b2_eval = read_sealed_evaluation(b2_eval_id)
    except Exception as e:
        return VerificationResult(False, {"error": f"Cannot read B2 evaluation: {e}"})

    b2_data = b2_eval.get("data", b2_eval)
    if b2_data.get("decision") != "ASSERT":
        return VerificationResult(False, {"error": "B2 decision is not ASSERT"})
    if b2_data.get("boundary_ref") != B2_BOUNDARY_REF:
        return VerificationResult(False, {"error": f"B2 boundary_ref mismatch: {b2_data.get('boundary_ref')}"})

    b2_workload = b2_data.get("artifact", {}).get("workload", {})
    if not b2_workload:
        b2_workload = b2_data.get("evidence", {})

    # -- Cross-chain material identity verification --
    for field in MATERIAL_ORDER_FIELDS:
        b3_val = workload.get(field)
        b1_val = b1_workload.get(field)
        b2_val = b2_workload.get(field)
        if b1_val is not None and str(b1_val) != str(b3_val):
            return VerificationResult(False, {
                "error": f"B1/B3 material mismatch on {field}: B1={b1_val}, B3={b3_val}"
            })
        if b2_val is not None and str(b2_val) != str(b3_val):
            return VerificationResult(False, {
                "error": f"B2/B3 material mismatch on {field}: B2={b2_val}, B3={b3_val}"
            })

    # Also verify order_ref across chain
    b3_order_ref = workload.get("order_ref")
    if b1_workload.get("order_ref") != b3_order_ref:
        return VerificationResult(False, {"error": "B1/B3 order_ref mismatch"})
    if b2_workload.get("order_ref") != b3_order_ref:
        return VerificationResult(False, {"error": "B2/B3 order_ref mismatch"})

    # -- Boundary version staleness check --
    try:
        b1_version_id = b1_data.get("boundary_version_id")
        b2_version_id = b2_data.get("boundary_version_id")
        b3_version_id = b3_data.get("boundary_version_id")

        for ref, eval_version in [
            (B1_BOUNDARY_REF, b1_version_id),
            (B2_BOUNDARY_REF, b2_version_id),
            (B3_BOUNDARY_REF, b3_version_id),
        ]:
            if eval_version:
                active = get_active_deployment(ref)
                active_data = active.get("data", active)
                active_version = active_data.get("boundary_version_id")
                if active_version and str(active_version) != str(eval_version):
                    return VerificationResult(False, {
                        "error": f"Boundary {ref} version superseded: eval={eval_version}, active={active_version}"
                    })
    except Exception as e:
        return VerificationResult(False, {"error": f"Version staleness check failed: {e}"})

    # -- Authorization expiration --
    created_at_str = b3_data.get("created_at")
    auth_valid_secs = workload.get("authorization_valid_seconds", AUTHORIZATION_VALID_DEFAULT)
    if created_at_str and auth_valid_secs:
        try:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            expiration = created_at.timestamp() + int(auth_valid_secs)
            if now.timestamp() > expiration:
                return VerificationResult(False, {
                    "error": "Authorization expired",
                    "created_at": created_at_str,
                    "valid_seconds": auth_valid_secs,
                    "expired_at": datetime.fromtimestamp(expiration, tz=timezone.utc).isoformat(),
                })
        except Exception as e:
            return VerificationResult(False, {"error": f"Expiration check failed: {e}"})

    # -- Operation 3: Arithmetic consistency --
    instrument_id = workload.get("instrument_id")
    instrument = INSTRUMENTS.get(instrument_id, {})
    quantity_divisor = instrument.get("quantity_divisor", 1)
    side = workload.get("side", "buy")
    order_type = workload.get("order_type", "limit")
    quantity = int(workload.get("quantity", 0))

    if order_type == "limit":
        price_cents = int(workload.get("limit_price_cents", 0))
    else:
        price_cents = int(workload.get("reference_price_cents", 0))

    expected_notional = derive_notional(side, order_type, quantity, price_cents, quantity_divisor)
    sealed_notional = int(workload.get("order_notional_cents", 0))
    if expected_notional != sealed_notional:
        return VerificationResult(False, {
            "error": f"Notional derivation mismatch: expected={expected_notional}, sealed={sealed_notional}",
        })

    desk_exposure = int(workload.get("desk_exposure_cents", 0))
    order_notional = int(workload.get("order_notional_cents", 0))
    sealed_combined = int(workload.get("combined_desk_and_order_cents", 0))
    if desk_exposure + order_notional != sealed_combined:
        return VerificationResult(False, {
            "error": f"Combined desk+order mismatch: {desk_exposure}+{order_notional}={desk_exposure+order_notional}, sealed={sealed_combined}",
        })

    limit_price = int(workload.get("limit_price_cents", 0))
    ref_price = int(workload.get("reference_price_cents", 0))
    if order_type == "limit" and ref_price > 0:
        expected_above = (limit_price - ref_price) * 10000
        expected_below = (ref_price - limit_price) * 10000
        expected_threshold = int(workload.get("price_tolerance_bps", 0)) * ref_price

        sealed_above = int(workload.get("price_deviation_above_scaled", 0))
        sealed_below = int(workload.get("price_deviation_below_scaled", 0))
        sealed_threshold = int(workload.get("tolerance_threshold_scaled", 0))

        if expected_above != sealed_above:
            return VerificationResult(False, {"error": f"price_deviation_above mismatch: {expected_above} vs {sealed_above}"})
        if expected_below != sealed_below:
            return VerificationResult(False, {"error": f"price_deviation_below mismatch: {expected_below} vs {sealed_below}"})
        if expected_threshold != sealed_threshold:
            return VerificationResult(False, {"error": f"tolerance_threshold mismatch: {expected_threshold} vs {sealed_threshold}"})

    margin_req = int(workload.get("margin_requirement_cents", 0))
    margin_avail = int(workload.get("available_margin_cents", 0))
    if margin_req > margin_avail:
        return VerificationResult(False, {"error": f"Margin insufficient in sealed artifact: {margin_req} > {margin_avail}"})

    # -- Operation 1: Submission binding (if proposed_order provided) --
    if proposed_order:
        binding_fields = [
            "account_id", "instrument_id", "asset_class", "side",
            "quantity", "order_type", "order_ref",
        ]
        for field in binding_fields:
            sealed_val = workload.get(field)
            proposed_val = proposed_order.get(field)
            if str(sealed_val) != str(proposed_val):
                return VerificationResult(False, {
                    "error": f"Submission binding mismatch on {field}: sealed={sealed_val}, proposed={proposed_val}",
                })
        if order_type == "limit":
            sealed_limit = workload.get("limit_price_cents")
            proposed_limit = proposed_order.get("limit_price_cents")
            if str(sealed_limit) != str(proposed_limit):
                return VerificationResult(False, {
                    "error": f"Limit price mismatch: sealed={sealed_limit}, proposed={proposed_limit}",
                })
        sealed_unit = workload.get("quantity_unit")
        instrument_unit = instrument.get("quantity_unit")
        if instrument_unit and sealed_unit != instrument_unit:
            return VerificationResult(False, {
                "error": f"Quantity unit mismatch: sealed={sealed_unit}, instrument={instrument_unit}",
            })

    return VerificationResult(True, {
        "evaluation_id": evaluation_id,
        "b1_evaluation_id": b1_eval_id,
        "b2_evaluation_id": b2_eval_id,
        "order_ref": b3_order_ref,
        "side": side,
        "quantity": quantity,
        "order_type": order_type,
        "instrument_id": instrument_id,
        "workload": workload,
    })


# ── Order submission ─────────────────────────────────────────────────────

def submit_order(dispatch_id: str, evaluation_id: str,
                 proposed_order: dict, live: bool = True) -> dict:
    db = _db()
    with _db_lock:
        existing = db.execute(
            "SELECT dispatch_id, status FROM placements WHERE dispatch_id = ?",
            (dispatch_id,),
        ).fetchone()
        if existing:
            return {"status": "duplicate", "dispatch_id": dispatch_id, "placement_status": existing[1]}

        try:
            db.execute(
                """INSERT INTO placements (dispatch_id, authorization_evaluation_id, order_side, status, created_at)
                   VALUES (?, ?, ?, 'CLAIMED', ?)""",
                (dispatch_id, evaluation_id, proposed_order.get("side", "buy"),
                 datetime.now(timezone.utc).isoformat()),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return {"status": "authorization_consumed", "evaluation_id": evaluation_id}

    verification = verify_authorization(evaluation_id, proposed_order)
    if not verification.authorized:
        with _db_lock:
            db.execute(
                "UPDATE placements SET status = 'REJECTED' WHERE dispatch_id = ?",
                (dispatch_id,),
            )
            db.commit()
        return {"status": "rejected", "reason": verification.details}

    if not live:
        return {
            "status": "verified_not_submitted",
            "verification": verification.to_dict(),
            "dispatch_id": dispatch_id,
        }

    symbol = _resolve_symbol(verification.details.get("instrument_id", ""))
    alpaca_qty = _convert_quantity(
        verification.details.get("quantity", 0),
        verification.details.get("instrument_id", ""),
    )
    order_type = verification.details.get("order_type", "limit")
    side = verification.details.get("side", "buy")

    order_body = {
        "symbol": symbol,
        "qty": str(alpaca_qty),
        "side": side,
        "type": order_type,
        "time_in_force": "day",
        "client_order_id": evaluation_id,
    }
    if order_type == "limit":
        limit_cents = verification.details.get("workload", {}).get("limit_price_cents", 0)
        order_body["limit_price"] = str(Decimal(int(limit_cents)) / 100)

    result = alpaca_api("POST", "/v2/orders", order_body)

    if result is None:
        with _db_lock:
            db.execute(
                "UPDATE placements SET status = 'UNCERTAIN' WHERE dispatch_id = ?",
                (dispatch_id,),
            )
            db.commit()
        return {"status": "uncertain", "dispatch_id": dispatch_id}

    if result.get("error"):
        status_code = result.get("status_code", 0)
        if 400 <= status_code < 500:
            with _db_lock:
                db.execute(
                    "UPDATE placements SET status = 'REJECTED' WHERE dispatch_id = ?",
                    (dispatch_id,),
                )
                db.commit()
            return {"status": "rejected", "alpaca_error": result.get("body")}
        else:
            with _db_lock:
                db.execute(
                    "UPDATE placements SET status = 'UNCERTAIN' WHERE dispatch_id = ?",
                    (dispatch_id,),
                )
                db.commit()
            return {"status": "uncertain", "dispatch_id": dispatch_id}

    alpaca_order_id = result.get("id", "")
    with _db_lock:
        db.execute(
            "UPDATE placements SET status = 'SUBMITTED', alpaca_order_id = ? WHERE dispatch_id = ?",
            (alpaca_order_id, dispatch_id),
        )
        db.commit()

    return {
        "status": "submitted",
        "dispatch_id": dispatch_id,
        "alpaca_order_id": alpaca_order_id,
        "verification": verification.to_dict(),
    }


def _resolve_symbol(instrument_id: str) -> str:
    inst = INSTRUMENTS.get(instrument_id, {})
    return inst.get("symbol", instrument_id)


def _convert_quantity(quantity: int, instrument_id: str) -> str:
    inst = INSTRUMENTS.get(instrument_id, {})
    divisor = inst.get("quantity_divisor", 1)
    if divisor == 1:
        return str(quantity)
    return str(Decimal(quantity) / Decimal(divisor))


# ── Reconciliation ────────────────────────────────────────────────────────

def reconcile_placement(dispatch_id: str, b4_connector_id: str) -> dict | None:
    db = _db()
    with _db_lock:
        row = db.execute(
            """SELECT dispatch_id, authorization_evaluation_id, alpaca_order_id,
                      order_side, status, last_seen_filled_qty, last_seen_avg_price_cents
               FROM placements WHERE dispatch_id = ?""",
            (dispatch_id,),
        ).fetchone()

    if not row:
        return None

    _, eval_id, alpaca_order_id, order_side, status, last_qty, last_price = row
    if not alpaca_order_id:
        return {"status": status, "message": "No alpaca_order_id"}

    order_data = alpaca_api("GET", f"/v2/orders/{alpaca_order_id}")
    if not order_data or order_data.get("error"):
        return {"status": status, "message": "Could not fetch order from Alpaca"}

    broker_status = order_data.get("status", "")
    filled_qty_str = order_data.get("filled_qty", "0")
    filled_avg_price = order_data.get("filled_avg_price")

    filled_qty = int(Decimal(filled_qty_str)) if filled_qty_str else 0
    if filled_avg_price:
        new_price = parse_fill_price(filled_avg_price, order_side or "buy")
    else:
        new_price = 0

    # State-change detection
    if filled_qty == last_qty and new_price == last_price:
        _update_broker_status(dispatch_id, broker_status, filled_qty)
        return {"status": broker_status, "message": "No state change", "filled_qty": filled_qty}

    if filled_qty > last_qty or (filled_qty == last_qty and new_price != last_price):
        observation_type = "quantity_increase" if filled_qty > last_qty else "price_correction"
        b4_evidence = _build_b4_evidence(
            eval_id, alpaca_order_id, order_data, filled_qty, new_price, last_qty,
        )

        with _db_lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                if observation_type == "quantity_increase":
                    db.execute(
                        """UPDATE placements SET last_seen_filled_qty = ?, last_seen_avg_price_cents = ?,
                           status = ? WHERE dispatch_id = ?""",
                        (filled_qty, new_price, _map_broker_status(broker_status), dispatch_id),
                    )
                else:
                    db.execute(
                        """UPDATE placements SET last_seen_avg_price_cents = ?,
                           status = ? WHERE dispatch_id = ?""",
                        (new_price, _map_broker_status(broker_status), dispatch_id),
                    )
                db.execute(
                    """INSERT INTO b4_outbox (placement_dispatch_id, b4_evidence_json, created_at)
                       VALUES (?, ?, ?)""",
                    (dispatch_id, json.dumps(b4_evidence), datetime.now(timezone.utc).isoformat()),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise

        _drain_outbox_entry(b4_connector_id)
        return {"status": broker_status, "observation": observation_type, "filled_qty": filled_qty}

    if filled_qty < last_qty:
        log.error("Regression anomaly: dispatch=%s, new_qty=%d < last_qty=%d", dispatch_id, filled_qty, last_qty)
        b4_evidence = _build_b4_evidence(
            eval_id, alpaca_order_id, order_data, filled_qty, new_price, last_qty,
        )
        with _db_lock:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """UPDATE placements SET anomaly_count = anomaly_count + 1,
                       last_anomaly_at = ? WHERE dispatch_id = ?""",
                    (datetime.now(timezone.utc).isoformat(), dispatch_id),
                )
                db.execute(
                    """INSERT INTO b4_outbox (placement_dispatch_id, b4_evidence_json, created_at)
                       VALUES (?, ?, ?)""",
                    (dispatch_id, json.dumps(b4_evidence), datetime.now(timezone.utc).isoformat()),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        _drain_outbox_entry(b4_connector_id)
        return {"status": broker_status, "observation": "regression", "filled_qty": filled_qty}

    return {"status": broker_status}


def _build_b4_evidence(eval_id: str, alpaca_order_id: str,
                       order_data: dict, filled_qty: int,
                       price_cents: int, previous_qty: int) -> dict:
    try:
        b3_eval = read_sealed_evaluation(eval_id)
        b3_data = b3_eval.get("data", b3_eval)
        workload = b3_data.get("artifact", {}).get("workload", {})
        if not workload:
            workload = b3_data.get("evidence", {})
    except Exception:
        workload = {}

    authorized_qty = int(workload.get("quantity", 0))
    side = workload.get("side", "buy")
    if side == "buy" and workload.get("order_type") == "limit":
        ceiling = int(workload.get("limit_price_cents", 0))
    else:
        ceiling = 0

    broker_status = order_data.get("status", "")
    mapped_status = "filled" if broker_status == "filled" else "partially_filled"

    return {
        "venue_authorization_reference": order_data.get("client_order_id", ""),
        "expected_authorization_reference": eval_id,
        "execution_status": mapped_status,
        "executed_quantity": filled_qty,
        "previous_executed_quantity": previous_qty,
        "authorized_quantity": authorized_qty,
        "executed_avg_price_cents": price_cents,
        "authorized_price_ceiling_cents": ceiling,
    }


def _map_broker_status(alpaca_status: str) -> str:
    mapping = {
        "filled": "FILLED",
        "partially_filled": "PARTIALLY_FILLED",
        "canceled": "CANCELED",
        "expired": "EXPIRED",
        "new": "SUBMITTED",
        "accepted": "SUBMITTED",
        "pending_new": "SUBMITTED",
    }
    return mapping.get(alpaca_status, "SUBMITTED")


def _update_broker_status(dispatch_id: str, broker_status: str, filled_qty: int) -> None:
    mapped = _map_broker_status(broker_status)
    db = _db()
    with _db_lock:
        current = db.execute(
            "SELECT status FROM placements WHERE dispatch_id = ?", (dispatch_id,)
        ).fetchone()
        if current and current[0] not in ("FILLED", "CANCELED", "EXPIRED", "REJECTED", "ABANDONED"):
            db.execute(
                "UPDATE placements SET status = ? WHERE dispatch_id = ?",
                (mapped, dispatch_id),
            )
            db.commit()


def _drain_outbox_entry(b4_connector_id: str) -> None:
    if not b4_connector_id:
        return
    db = _db()
    with _db_lock:
        rows = db.execute(
            "SELECT outbox_id, b4_evidence_json FROM b4_outbox WHERE pushed_at IS NULL ORDER BY outbox_id"
        ).fetchall()

    for outbox_id, evidence_json in rows:
        evidence = json.loads(evidence_json)
        provenance = {}
        for field, source in [
            ("venue_authorization_reference", "venue_alpaca"),
            ("expected_authorization_reference", "sealed_evaluation"),
            ("execution_status", "venue_alpaca"),
            ("executed_quantity", "venue_alpaca"),
            ("previous_executed_quantity", "gateway_state"),
            ("authorized_quantity", "sealed_evaluation"),
            ("executed_avg_price_cents", "venue_alpaca"),
            ("authorized_price_ceiling_cents", "sealed_evaluation"),
        ]:
            provenance[field] = source

        payload = {"raw_payload": evidence, "provenance": provenance}
        try:
            push_b4_evidence(b4_connector_id, payload)
            with _db_lock:
                db.execute(
                    "UPDATE b4_outbox SET pushed_at = ? WHERE outbox_id = ?",
                    (datetime.now(timezone.utc).isoformat(), outbox_id),
                )
                db.commit()
            log.info("Pushed B4 evidence outbox_id=%d", outbox_id)
        except Exception as e:
            log.error("Failed to push B4 evidence outbox_id=%d: %s", outbox_id, e)


def drain_all_outbox(b4_connector_id: str) -> int:
    _drain_outbox_entry(b4_connector_id)
    db = _db()
    with _db_lock:
        remaining = db.execute(
            "SELECT COUNT(*) FROM b4_outbox WHERE pushed_at IS NULL"
        ).fetchone()[0]
    return remaining


# ── Startup recovery ─────────────────────────────────────────────────────

def startup_recovery() -> None:
    db = _db()
    with _db_lock:
        claimed = db.execute(
            "SELECT dispatch_id FROM placements WHERE status = 'CLAIMED'"
        ).fetchall()
        for (did,) in claimed:
            db.execute(
                "UPDATE placements SET status = 'UNCERTAIN' WHERE dispatch_id = ?",
                (did,),
            )
        if claimed:
            db.commit()
            log.info("Recovered %d CLAIMED placements to UNCERTAIN", len(claimed))


# ── HTTP handler ──────────────────────────────────────────────────────────

class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log.info(format, *args)

    def _json_response(self, status: int, body: dict) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
        elif self.path == "/placements":
            db = _db()
            with _db_lock:
                rows = db.execute(
                    """SELECT dispatch_id, authorization_evaluation_id, alpaca_order_id,
                              status, created_at, last_seen_filled_qty, last_seen_avg_price_cents,
                              anomaly_count
                       FROM placements ORDER BY created_at DESC"""
                ).fetchall()
            placements = []
            for r in rows:
                placements.append({
                    "dispatch_id": r[0],
                    "authorization_evaluation_id": r[1],
                    "alpaca_order_id": r[2],
                    "status": r[3],
                    "created_at": r[4],
                    "last_seen_filled_qty": r[5],
                    "last_seen_avg_price_cents": r[6],
                    "anomaly_count": r[7],
                })
            self._json_response(200, {"placements": placements})
        else:
            self._json_response(404, {"error": "Not found"})

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length > 0 else {}

        if self.path == "/submit":
            dispatch_id = body.get("dispatch_id", "")
            evaluation_id = body.get("evaluation_id", "")
            proposed_order = body.get("proposed_order", {})
            live = body.get("live", True)

            if not dispatch_id or not evaluation_id:
                self._json_response(400, {"error": "Missing dispatch_id or evaluation_id"})
                return

            result = submit_order(dispatch_id, evaluation_id, proposed_order, live=live)
            status_code = {
                "submitted": 201,
                "verified_not_submitted": 200,
                "duplicate": 200,
                "authorization_consumed": 409,
                "rejected": 403,
                "uncertain": 202,
            }.get(result.get("status", ""), 500)
            self._json_response(status_code, result)

        elif self.path == "/verify":
            evaluation_id = body.get("evaluation_id", "")
            proposed_order = body.get("proposed_order")
            if not evaluation_id:
                self._json_response(400, {"error": "Missing evaluation_id"})
                return
            result = verify_authorization(evaluation_id, proposed_order)
            self._json_response(200 if result.authorized else 403, result.to_dict())

        elif self.path == "/reconcile":
            dispatch_id = body.get("dispatch_id", "")
            b4_connector_id = body.get("b4_connector_id", "")
            if not dispatch_id:
                self._json_response(400, {"error": "Missing dispatch_id"})
                return
            result = reconcile_placement(dispatch_id, b4_connector_id)
            self._json_response(200, result or {"error": "Placement not found"})

        elif self.path == "/drain-outbox":
            b4_connector_id = body.get("b4_connector_id", "")
            remaining = drain_all_outbox(b4_connector_id)
            self._json_response(200, {"remaining": remaining})

        else:
            self._json_response(404, {"error": "Not found"})


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    if ALPACA_API_KEY:
        _validate_broker_url()
        log.info("Broker URL validated: %s", ALPACA_BASE_URL)

    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        load_instruments_from_db(db_url)

    _init_db()
    startup_recovery()
    drain_all_outbox(os.environ.get("B4_CONNECTOR_ID", ""))

    server = ThreadingHTTPServer(("0.0.0.0", GATEWAY_PORT), GatewayHandler)
    log.info("Gateway listening on port %d", GATEWAY_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
