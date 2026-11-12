# Copyright 2026 MNC Labs, Inc. Licensed under Apache 2.0.

boundary_id: B_ORDER_ADMISSIBILITY
version: 1
scope: "trading/order-governance"
eta_cap: 300

define_set supported_sides: ("buy", "sell")
define_set tradable_account_states: ("active")
define_set supported_order_types: ("market", "limit")
define_set session_exempt_classes: ("crypto")
define_set supported_asset_classes: ("equity", "crypto")

require_type: order_ref: string
require_type: account_id: string
require_type: instrument_id: string
require_type: account_status: string
require_type: side: string
require_type: asset_class: string
require_type: quantity: integer
require_type: order_notional_cents: integer
require_type: buying_power_cents: integer
require_type: order_type: string
require_type: market_session_open: integer

require_provenance: order_ref: oms_database
require_provenance: account_id: oms_database
require_provenance: instrument_id: oms_database
require_provenance: account_status: oms_database
require_provenance: side: oms_database
require_provenance: asset_class: oms_database
require_provenance: quantity: oms_database
require_provenance: order_notional_cents: oms_database
require_provenance: buying_power_cents: oms_database
require_provenance: order_type: oms_database
require_provenance: market_session_open: oms_database

require_evidence: order_ref
require_evidence: account_id
require_evidence: instrument_id
require_evidence: account_status
require_evidence: side
require_evidence: asset_class
require_evidence: quantity
require_evidence: order_notional_cents
require_evidence: buying_power_cents
require_evidence: order_type
require_evidence: market_session_open
require_evidence IF side == "sell": position_quantity

predicate: account_is_tradable: account_status IN @tradable_account_states | "Account is not in a state that permits trading"
predicate: side_is_supported: side IN @supported_sides | "Unsupported order side"
predicate: asset_class_is_supported: asset_class IN @supported_asset_classes | "Asset class is not supported"
predicate: quantity_is_positive: quantity > 0 | "Order quantity must be positive"
predicate: notional_is_positive: order_notional_cents > 0 | "Order notional must be positive"
predicate: order_type_is_supported: order_type IN @supported_order_types | "Order type is not supported"
predicate: buying_power_is_nonnegative: buying_power_cents >= 0 | "Buying power must be nonnegative"
predicate: buying_power_is_sufficient: (side != "buy") OR (order_notional_cents <= buying_power_cents) | "Order notional exceeds the account's buying power"
predicate: market_is_open: (asset_class IN @session_exempt_classes) OR (market_session_open == 1) | "Market session is closed for this asset class"
predicate: sell_is_covered: (side != "sell") OR (position_quantity >= quantity) | "Sell order exceeds available position"
