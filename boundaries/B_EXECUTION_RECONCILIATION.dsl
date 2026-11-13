# Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.
# Author: Asaad Riaz

boundary_id: B_EXECUTION_RECONCILIATION
version: 1
scope: "trading/order-governance"
eta_cap: 900

define_set reached_venue_states: ("filled", "partially_filled")

require_type: venue_authorization_reference: string
require_type: expected_authorization_reference: string
require_type: execution_status: string
require_type: executed_quantity: integer
require_type: authorized_quantity: integer
require_type: previous_executed_quantity: integer
require_type: executed_avg_price_cents: integer
require_type: authorized_price_ceiling_cents: integer

require_provenance: venue_authorization_reference: venue_alpaca
require_provenance: expected_authorization_reference: sealed_evaluation
require_provenance: execution_status: venue_alpaca
require_provenance: executed_quantity: venue_alpaca
require_provenance: authorized_quantity: sealed_evaluation
require_provenance: previous_executed_quantity: gateway_state
require_provenance: executed_avg_price_cents: venue_alpaca
require_provenance: authorized_price_ceiling_cents: sealed_evaluation

require_evidence: venue_authorization_reference
require_evidence: expected_authorization_reference
require_evidence: execution_status
require_evidence: executed_quantity
require_evidence: authorized_quantity
require_evidence: previous_executed_quantity
require_evidence: executed_avg_price_cents
require_evidence: authorized_price_ceiling_cents

predicate: authorization_identity_matches: venue_authorization_reference == expected_authorization_reference | "Venue order does not carry the authorization it was placed under"
predicate: execution_reached_venue: execution_status IN @reached_venue_states | "Venue did not report the order as executed"
predicate: executed_quantity_is_positive: executed_quantity > 0 | "Executed quantity must be positive for a reached-venue state"
predicate: authorized_quantity_is_positive: authorized_quantity > 0 | "Authorized quantity must be positive"
predicate: previous_quantity_is_nonnegative: previous_executed_quantity >= 0 | "Previous executed quantity must be nonnegative"
predicate: execution_within_quantity_authority: executed_quantity <= authorized_quantity | "Cumulative execution exceeded the authorized quantity"
predicate: fill_is_monotonic: executed_quantity >= previous_executed_quantity | "Cumulative filled quantity regressed from a previously observed value"
predicate: execution_price_is_positive: executed_avg_price_cents > 0 | "Venue reported a non-positive execution price"
predicate: execution_within_price_authority: (authorized_price_ceiling_cents == 0) OR (executed_avg_price_cents <= authorized_price_ceiling_cents) | "Average execution price exceeded the authorized ceiling"
