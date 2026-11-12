# Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.

boundary_id: B_EXECUTION_AUTHORIZATION
version: 1
scope: "trading/order-governance"
eta_cap: 300

define_set market_order_types: ("market")

require_type: account_id: string
require_type: instrument_id: string
require_type: asset_class: string
require_type: side: string
require_type: quantity: integer
require_type: quantity_unit: string
require_type: order_type: string
require_type: order_ref: string
require_type: order_notional_cents: integer
require_type: trader_authority_cents: integer
require_type: desk_exposure_cents: integer
require_type: desk_limit_cents: integer
require_type: combined_desk_and_order_cents: integer
require_type: limit_price_cents: integer
require_type: reference_price_cents: integer
require_type: price_tolerance_bps: integer
require_type: price_deviation_above_scaled: integer
require_type: price_deviation_below_scaled: integer
require_type: tolerance_threshold_scaled: integer
require_type: margin_requirement_cents: integer
require_type: available_margin_cents: integer
require_type: authorization_valid_seconds: integer
require_type: b1_evaluation_id: string
require_type: b2_evaluation_id: string

require_provenance: account_id: oms_database
require_provenance: instrument_id: oms_database
require_provenance: asset_class: oms_database
require_provenance: side: oms_database
require_provenance: quantity: oms_database
require_provenance: quantity_unit: oms_database
require_provenance: order_type: oms_database
require_provenance: order_ref: oms_database
require_provenance: order_notional_cents: oms_database
require_provenance: trader_authority_cents: risk_system
require_provenance: desk_exposure_cents: risk_system
require_provenance: desk_limit_cents: risk_system
require_provenance: combined_desk_and_order_cents: oms_database
require_provenance: limit_price_cents: oms_database
require_provenance: reference_price_cents: market_data
require_provenance: price_tolerance_bps: risk_system
require_provenance: price_deviation_above_scaled: oms_database
require_provenance: price_deviation_below_scaled: oms_database
require_provenance: tolerance_threshold_scaled: oms_database
require_provenance: margin_requirement_cents: risk_system
require_provenance: available_margin_cents: risk_system
require_provenance: authorization_valid_seconds: risk_system
require_provenance: b1_evaluation_id: orchestrator
require_provenance: b2_evaluation_id: orchestrator

require_evidence: account_id
require_evidence: instrument_id
require_evidence: asset_class
require_evidence: side
require_evidence: quantity
require_evidence: quantity_unit
require_evidence: order_type
require_evidence: order_ref
require_evidence: order_notional_cents
require_evidence: trader_authority_cents
require_evidence: desk_exposure_cents
require_evidence: desk_limit_cents
require_evidence: combined_desk_and_order_cents
require_evidence: limit_price_cents
require_evidence: reference_price_cents
require_evidence: price_tolerance_bps
require_evidence: price_deviation_above_scaled
require_evidence: price_deviation_below_scaled
require_evidence: tolerance_threshold_scaled
require_evidence: margin_requirement_cents
require_evidence: available_margin_cents
require_evidence: authorization_valid_seconds
require_evidence: b1_evaluation_id
require_evidence: b2_evaluation_id

predicate: notional_is_positive: order_notional_cents > 0 | "Order notional must be positive"
predicate: quantity_is_positive: quantity > 0 | "Order quantity must be positive"
predicate: authority_is_nonnegative: trader_authority_cents >= 0 | "Trader authority must be nonnegative"
predicate: within_trader_authority: order_notional_cents <= trader_authority_cents | "Order exceeds the authority granted to this trader"
predicate: desk_exposure_is_nonnegative: desk_exposure_cents >= 0 | "Desk exposure must be nonnegative"
predicate: desk_limit_is_nonnegative: desk_limit_cents >= 0 | "Desk limit must be nonnegative"
predicate: within_desk_limit: combined_desk_and_order_cents <= desk_limit_cents | "Order would breach the desk exposure limit"
predicate: price_is_reasonable: (order_type IN @market_order_types) OR (limit_price_cents > 0) | "Limit order must specify a positive price"
predicate: reference_price_is_valid: reference_price_cents > 0 | "Reference price is missing or invalid"
predicate: tolerance_is_nonnegative: price_tolerance_bps >= 0 | "Price tolerance must be nonnegative"
predicate: price_within_tolerance_above: (order_type IN @market_order_types) OR (price_deviation_above_scaled <= tolerance_threshold_scaled) | "Limit price exceeds the upper tolerance from reference"
predicate: price_within_tolerance_below: (order_type IN @market_order_types) OR (price_deviation_below_scaled <= tolerance_threshold_scaled) | "Limit price exceeds the lower tolerance from reference"
predicate: margin_requirement_is_nonnegative: margin_requirement_cents >= 0 | "Margin requirement must be nonnegative"
predicate: available_margin_is_nonnegative: available_margin_cents >= 0 | "Available margin must be nonnegative"
predicate: margin_is_sufficient: margin_requirement_cents <= available_margin_cents | "Insufficient margin for this order"
predicate: validity_is_positive: authorization_valid_seconds > 0 | "Authorization validity window must be positive"
