# Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.
# Author: Asaad Riaz

boundary_id: B_COMPLIANCE_AUTHORIZATION
version: 1
scope: "trading/order-governance"
eta_cap: 300

require_type: order_ref: string
require_type: account_id: string
require_type: instrument_id: string
require_type: side: string
require_type: quantity: integer
require_type: asset_class: string
require_type: order_notional_cents: integer
require_type: order_type: string
require_type: mandate_match: integer
require_type: concentration_bps_after: integer
require_type: concentration_limit_bps: integer
require_type: projected_position_quantity: integer
require_type: position_limit_quantity: integer
require_type: last_opposite_side_seconds: integer
require_type: cooldown_window_seconds: integer

require_provenance: order_ref: oms_database
require_provenance: account_id: oms_database
require_provenance: instrument_id: oms_database
require_provenance: side: oms_database
require_provenance: quantity: oms_database
require_provenance: asset_class: oms_database
require_provenance: order_notional_cents: oms_database
require_provenance: order_type: oms_database
require_provenance: mandate_match: compliance_system
require_provenance: concentration_bps_after: risk_system
require_provenance: concentration_limit_bps: risk_system
require_provenance: projected_position_quantity: risk_system
require_provenance: position_limit_quantity: risk_system
require_provenance: last_opposite_side_seconds: compliance_system
require_provenance: cooldown_window_seconds: compliance_system

require_evidence: order_ref
require_evidence: account_id
require_evidence: instrument_id
require_evidence: side
require_evidence: quantity
require_evidence: asset_class
require_evidence: order_notional_cents
require_evidence: order_type
require_evidence: mandate_match
require_evidence: concentration_bps_after
require_evidence: concentration_limit_bps
require_evidence: projected_position_quantity
require_evidence: position_limit_quantity
require_evidence: last_opposite_side_seconds
require_evidence: cooldown_window_seconds

prohibit_evidence: restricted_list_hit WHERE restricted_list_hit == 1

predicate: consistent_with_mandate: mandate_match == 1 | "Order is not consistent with the account mandate"
predicate: concentration_is_nonnegative: concentration_bps_after >= 0 | "Concentration value must be nonnegative"
predicate: concentration_limit_is_nonnegative: concentration_limit_bps >= 0 | "Concentration limit must be nonnegative"
predicate: within_concentration_limit: concentration_bps_after <= concentration_limit_bps | "Resulting position would breach the concentration limit"
predicate: projected_position_is_nonnegative: projected_position_quantity >= 0 | "Projected position must be nonnegative for long-only trading"
predicate: position_limit_is_nonnegative: position_limit_quantity >= 0 | "Position limit must be nonnegative"
predicate: within_position_limit: projected_position_quantity <= position_limit_quantity | "Projected position would exceed the instrument limit"
predicate: last_opposite_side_is_nonnegative: last_opposite_side_seconds >= 0 | "Time since last opposite-side trade must be nonnegative"
predicate: cooldown_is_nonnegative: cooldown_window_seconds >= 0 | "Cooldown window must be nonnegative"
predicate: outside_cooldown_window: last_opposite_side_seconds >= cooldown_window_seconds | "Order is within the opposite-side trading cooldown window"
