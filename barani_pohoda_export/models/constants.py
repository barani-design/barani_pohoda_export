# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# Single source of truth for the controlled Selection value sets used across the
# models (and, later, by the classifier / resolver / validation services).

GEOGRAPHY_SELECTION = [
    ('domestic', "Domestic (SK)"),
    ('eu', "EU"),
    ('foreign', "Foreign / non-EU"),
    ('oss', "OSS"),
    ('domestic_rpdp', "Domestic RPDP"),
]

CUSTOMER_TAX_STATUS_SELECTION = [
    ('vat_payer', "VAT payer"),
    ('no_vat_id', "No VAT ID"),
    ('any', "Any"),
]

# Full intended set (DOC 01). Phase 1 implements only product / category;
# 'account' and 'custom' are reserved and intentionally NOT offered in the UI yet.
MATCH_MODE_SELECTION = [
    ('product', "Product"),
    ('category', "Category"),
    ('account', "Account"),    # reserved: no backing match field yet
    ('custom', "Custom"),      # reserved: no backing match field yet
]
MATCH_MODE_PHASE1 = ('product', 'category')

# Mapping-cell dimension (which kind of document a cell maps).
DOCUMENT_KIND_SELECTION = [
    ('invoice', "Invoice"),
    ('credit_note', "Credit note"),
    ('down_payment_deduction', "Down-payment deduction"),
    ('bill', "Bill"),
    ('refund', "Refund"),
]

CELL_STATE_SELECTION = [
    ('active', "Active"),
    ('not_applicable', "Not applicable"),
    ('blocked', "Blocked"),
    ('review_required', "Review required"),
]

# Odoo account.move date field that feeds a given POHODA date (used by config
# defaults and by the export batch date range). Bounded by account.move fields.
DATE_SOURCE_SELECTION = [
    ('invoice_date', "Invoice date"),
    ('date', "Accounting date"),
    ('invoice_date_due', "Due date"),
]

# Classifier outcomes (DOC 00 glossary; OQ-1 v2 — advances are tax-config-driven).
# A single ``advance_invoice`` kind covers every down-payment invoice: its VAT is read
# from the line and may be 0 (EU B2B / non-EU export) or positive (domestic / OSS).
# ``blocked_vat_bearing_dpi`` was retired in OQ-1 v2: a VAT-bearing advance is valid,
# and a *posted* Odoo move always has internally-consistent base/VAT vs its taxes, so
# there is no shape-level "malformed advance" left to detect.
BARANI_DOC_KIND_SELECTION = [
    ('regular_invoice', "Regular invoice"),
    ('advance_invoice', "Advance invoice (down payment)"),
    ('settlement_invoice', "Settlement invoice"),
    ('credit_note', "Credit note"),
    ('blocked_mixed_324', "Blocked: mixed 324 + ordinary lines"),
    ('unsupported', "Unsupported"),
]

# Classifier outcomes that must never produce production XML as-is.
BLOCKED_DOC_KINDS = ('blocked_mixed_324', 'unsupported')

EXPORT_BATCH_STATE_SELECTION = [
    ('draft', "Draft"),
    ('validated', "Validated"),
    ('xml_generated', "XML generated"),
    ('sent', "Sent"),
    ('done', "Done"),
    ('warning', "Warning"),
    ('error', "Error"),
    ('cancelled', "Cancelled"),
]

EXPORT_BATCH_MOVE_STATE_SELECTION = [
    ('pending', "Pending"),
    ('blocked', "Blocked"),
    ('xml_generated', "XML generated"),
    ('sent', "Sent"),
    ('accepted', "Accepted"),
    ('warning', "Warning"),
    ('error', "Error"),
]

# Latest POHODA export status tracked on account.move. DOC 01 names the field but
# not its values; this set is inferred and may be aligned with the batch flow later.
MOVE_EXPORT_STATE_SELECTION = [
    ('not_exported', "Not exported"),
    ('blocked', "Blocked"),
    ('xml_generated', "XML generated"),
    ('sent', "Sent"),
    ('accepted', "Accepted"),
    ('warning', "Warning"),
    ('error', "Error"),
]


# ── Classifier configuration (DOC 02) ────────────────────────────────────────
# G/L account-code prefix that marks a *received customer advance* line.
# DOC 02 assumption A1: this prefix is correct for BARANI received advances. If a
# future BARANI accounting unit uses a different advance account, this is the one
# place to promote to a config field.
ADVANCE_ACCOUNT_PREFIX = '324'

# Prefix set used by the classifier's _is_advance_line. Kept as a tuple so a second
# received-advance account can be added in one place. NOTE: the live extractor and
# trial creator currently scan ('324', '475'); this stays '324'-only per DOC 02 A1
# until the live metadata confirms which received-advance account(s) BARANI uses,
# at which point extractor / creator / classifier are aligned to one decision.
ADVANCE_ACCOUNT_PREFIXES = (ADVANCE_ACCOUNT_PREFIX,)

# Floor tolerance for the classifier's sign checks. The classifier prefers the move
# currency's own rounding (currency.is_zero / compare_amounts); this constant only
# documents the intended floor (DOC 02: "at least 0.005 EUR").
CLASSIFIER_AMOUNT_TOLERANCE = 0.005

# Tolerance for comparing two effective VAT rates (VAT / base, expressed as a fraction)
# in the resolver's settlement deduction-vs-source-advance guard. 0.005 = 0.5 pp, which
# cleanly separates "same rate" (e.g. 0.23 vs 18.70/81.30 = 0.23001, diff ~1e-5) from a
# genuine rate change (e.g. 0.23 vs 0.20, diff 0.03).
VAT_RATE_MATCH_TOLERANCE = 0.005


# ── Preflight blocker codes ───────────────────────────────────────────────────
# Single source of truth for the BLOCK_* codes. DOC 05 is the canonical *registry*
# (the full preflight); the DOC 02 classifier/resolver emit the subset relevant to
# document classification and source resolution. Each string equals its constant
# name so stored values and logs are self-describing.
BLOCK_UNSUPPORTED_MOVE_TYPE = 'BLOCK_UNSUPPORTED_MOVE_TYPE'
BLOCK_NOT_POSTED = 'BLOCK_NOT_POSTED'
BLOCK_WRONG_COMPANY = 'BLOCK_WRONG_COMPANY'
# OQ-1 v2 retired the zero-VAT-era codes (VAT on an advance, VAT on a deduction, and a
# DPI whose gross != net are all normal now): BLOCK_ZERO_VAT_DPI_HAS_TAX,
# BLOCK_ZERO_VAT_DPI_LINE_TOTAL_DIFFERS_FROM_SUBTOTAL,
# BLOCK_SETTLEMENT_ADVANCE_DEDUCTION_HAS_TAX, and (pruned 2026-06, self-audit R-2 with
# Jan's sign-off) BLOCK_ZERO_VAT_DPI_HAS_ORDINARY_LINE / BLOCK_ZERO_VAT_DPI_HAS_NEGATIVE_324
# — those two shapes classify as blocked_mixed_324 and emit BLOCK_MIXED_* instead.
BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_FOUND = 'BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_FOUND'
# Renamed from BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_ZERO_VAT_DPI (OQ-1 v2): the source
# must classify as a valid advance_invoice (VAT 0 or positive), not specifically zero-VAT.
BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_VALID_ADVANCE = 'BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_VALID_ADVANCE'
# New (OQ-1 v2): the settlement deduction's VAT rate must equal its source advance's
# rate, so the advance VAT is reversed at the same rate it was charged (no double count).
BLOCK_SETTLEMENT_ADVANCE_VAT_RATE_MISMATCH = 'BLOCK_SETTLEMENT_ADVANCE_VAT_RATE_MISMATCH'
BLOCK_SETTLEMENT_ADVANCE_XML_SOURCE_NOT_AVAILABLE = 'BLOCK_SETTLEMENT_ADVANCE_XML_SOURCE_NOT_AVAILABLE'
BLOCK_DDS_ADVANCE_MATH_DETECTED = 'BLOCK_DDS_ADVANCE_MATH_DETECTED'
BLOCK_MIXED_POSITIVE_324_AND_ORDINARY_LINES = 'BLOCK_MIXED_POSITIVE_324_AND_ORDINARY_LINES'
# New (Bucket B Step 1 — DOC 03 rule resolver): the move's Odoo fiscal position does
# not resolve to exactly one barani.pohoda.fiscal.profile (zero matches, or — a config
# error — more than one). Without a single profile there is no matrix column to map.
BLOCK_MAPPING_PROFILE_NOT_FOUND = 'BLOCK_MAPPING_PROFILE_NOT_FOUND'
BLOCK_MAPPING_CELL_MISSING = 'BLOCK_MAPPING_CELL_MISSING'
BLOCK_MAPPING_CELL_BLOCKED = 'BLOCK_MAPPING_CELL_BLOCKED'
BLOCK_MAPPING_CELL_REVIEW_REQUIRED = 'BLOCK_MAPPING_CELL_REVIEW_REQUIRED'
BLOCK_REQUIRED_CODE_MISSING = 'BLOCK_REQUIRED_CODE_MISSING'
BLOCK_REPAIRS_OSS = 'BLOCK_REPAIRS_OSS'
BLOCK_CREDIT_NOTE_NOT_TESTED = 'BLOCK_CREDIT_NOTE_NOT_TESTED'
BLOCK_XML_SCHEMA_VALIDATION_FAILED = 'BLOCK_XML_SCHEMA_VALIDATION_FAILED'

# --- Advance tax document Architecture A v3 (external audit 2026-06-09) ----------
# These enforce the Phase-1 Architecture A invariants: an Odoo VAT-bearing DPI is a
# regular issuedInvoice posting to 324 (never the non-tax issuedAdvanceInvoice); a
# taxable advance / advance-deduction line must NOT silently carry 'UN' (which would
# drop or re-declare the VAT); the settlement nets via signed invoiceItem lines, never
# invoiceAdvancePaymentItem. Wiring by component:
#   - rule resolver (now): BLOCK_TAXABLE_ADVANCE_CLASSIFICATION_UN,
#         BLOCK_TAXABLE_ADVANCE_DEDUCTION_CLASSIFICATION_UN
#   - xml builder (already, by construction): advance -> issuedInvoice and NO
#         invoiceAdvancePaymentItem (BLOCK_ADVANCE_XML_TYPE_FORBIDDEN /
#         BLOCK_EXPORT_DOUBLE_REPRESENTS_ADVANCE_DEDUCTION are invariants asserted by
#         tests); builder hard-stops on a blocked doc_kind (BLOCK_XML_DOCUMENT_KIND_INVALID)
#   - Bucket C preflight (later): BLOCK_ADVANCE_ACCOUNT_MAPPING_NOT_324,
#         BLOCK_SETTLEMENT_DEDUCTION_ACCOUNT_MAPPING_NOT_324,
#         BLOCK_SETTLEMENT_SUPPLY_LINES_MISSING, BLOCK_SETTLEMENT_DEDUCTION_LINES_MISSING,
#         BLOCK_AMOUNT_RECONCILIATION_FAILED
#   - response parser, Step 4 (later): BLOCK_RESPONSE_PARSE_FAILED
BLOCK_ADVANCE_XML_TYPE_FORBIDDEN = 'BLOCK_ADVANCE_XML_TYPE_FORBIDDEN'
BLOCK_TAXABLE_ADVANCE_CLASSIFICATION_UN = 'BLOCK_TAXABLE_ADVANCE_CLASSIFICATION_UN'
BLOCK_TAXABLE_ADVANCE_DEDUCTION_CLASSIFICATION_UN = 'BLOCK_TAXABLE_ADVANCE_DEDUCTION_CLASSIFICATION_UN'
BLOCK_ADVANCE_ACCOUNT_MAPPING_NOT_324 = 'BLOCK_ADVANCE_ACCOUNT_MAPPING_NOT_324'
BLOCK_SETTLEMENT_DEDUCTION_ACCOUNT_MAPPING_NOT_324 = 'BLOCK_SETTLEMENT_DEDUCTION_ACCOUNT_MAPPING_NOT_324'
BLOCK_EXPORT_DOUBLE_REPRESENTS_ADVANCE_DEDUCTION = 'BLOCK_EXPORT_DOUBLE_REPRESENTS_ADVANCE_DEDUCTION'
BLOCK_AMOUNT_RECONCILIATION_FAILED = 'BLOCK_AMOUNT_RECONCILIATION_FAILED'
BLOCK_XML_DOCUMENT_KIND_INVALID = 'BLOCK_XML_DOCUMENT_KIND_INVALID'
BLOCK_SETTLEMENT_SUPPLY_LINES_MISSING = 'BLOCK_SETTLEMENT_SUPPLY_LINES_MISSING'
BLOCK_SETTLEMENT_DEDUCTION_LINES_MISSING = 'BLOCK_SETTLEMENT_DEDUCTION_LINES_MISSING'
BLOCK_RESPONSE_PARSE_FAILED = 'BLOCK_RESPONSE_PARSE_FAILED'
# DOC 05 edge 14: a move already accepted by POHODA may not be silently re-exported;
# re-export is a manager action with a reason and an idempotency-hash check (wizard).
BLOCK_ALREADY_EXPORTED = 'BLOCK_ALREADY_EXPORTED'

# The 'UN' VAT classification code (not-included-in-VAT-return). Used by the resolver
# to detect a taxable line wrongly mapped to UN.
VAT_CLASSIFICATION_UN_CODE = 'UN'

ALL_BLOCKER_CODES = frozenset({
    BLOCK_UNSUPPORTED_MOVE_TYPE,
    BLOCK_NOT_POSTED,
    BLOCK_WRONG_COMPANY,
    BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_FOUND,
    BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_VALID_ADVANCE,
    BLOCK_SETTLEMENT_ADVANCE_VAT_RATE_MISMATCH,
    BLOCK_SETTLEMENT_ADVANCE_XML_SOURCE_NOT_AVAILABLE,
    BLOCK_DDS_ADVANCE_MATH_DETECTED,
    BLOCK_MIXED_POSITIVE_324_AND_ORDINARY_LINES,
    BLOCK_MAPPING_PROFILE_NOT_FOUND,
    BLOCK_MAPPING_CELL_MISSING,
    BLOCK_MAPPING_CELL_BLOCKED,
    BLOCK_MAPPING_CELL_REVIEW_REQUIRED,
    BLOCK_REQUIRED_CODE_MISSING,
    BLOCK_REPAIRS_OSS,
    BLOCK_CREDIT_NOTE_NOT_TESTED,
    BLOCK_XML_SCHEMA_VALIDATION_FAILED,
    # Architecture A v3 audit additions
    BLOCK_ADVANCE_XML_TYPE_FORBIDDEN,
    BLOCK_TAXABLE_ADVANCE_CLASSIFICATION_UN,
    BLOCK_TAXABLE_ADVANCE_DEDUCTION_CLASSIFICATION_UN,
    BLOCK_ADVANCE_ACCOUNT_MAPPING_NOT_324,
    BLOCK_SETTLEMENT_DEDUCTION_ACCOUNT_MAPPING_NOT_324,
    BLOCK_EXPORT_DOUBLE_REPRESENTS_ADVANCE_DEDUCTION,
    BLOCK_AMOUNT_RECONCILIATION_FAILED,
    BLOCK_XML_DOCUMENT_KIND_INVALID,
    BLOCK_SETTLEMENT_SUPPLY_LINES_MISSING,
    BLOCK_SETTLEMENT_DEDUCTION_LINES_MISSING,
    BLOCK_RESPONSE_PARSE_FAILED,
    BLOCK_ALREADY_EXPORTED,
})
