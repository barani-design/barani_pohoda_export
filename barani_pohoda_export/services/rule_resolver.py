# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 03 / DOC 04 — Rule resolver (Bucket B, Step 1).
#
# For one classified ``account.move``, resolves each exportable line to the POHODA
# codes the DOC 04 XML builder will emit. It picks the move's fiscal *profile*
# (matrix column) from the invoice's Odoo fiscal position, then for every
# exportable line finds the first matching ordered export *rule* (matrix row) and
# the mapping *cell* at (rule x profile x document_kind), and reads that cell's
# controlled dictionary codes: account assignment, VAT classification,
# KV / control-statement code and (OSS) MOSS service type.
#
# Reads native Odoo facts + the seeded matrix only; never DDS. Pure / read-only.
#
# PHASE 1 MATRIX CONTRACT (Bucket B handoff):
#   Matrix lookup always uses an explicit document_kind dimension:
#       regular / advance / settlement supply -> 'invoice'
#       settlement negative-324 deduction     -> 'down_payment_deduction'
#       credit note                           -> 'credit_note'
#   The deduction therefore has its own mapping cell. This prevents the XML builder
#   from confusing a standalone advance line with an odpočet-zálohy deduction line
#   and keeps the implementation aligned with the Bucket B Step 1 handoff.
#
# Responsibility boundary (mirrors the classifier / source resolver headers):
#   * Owns the mapping blockers knowable from the matrix + the move's profile/lines:
#       - BLOCK_MAPPING_PROFILE_NOT_FOUND  (no single fiscal profile for the move's FP)
#       - BLOCK_MAPPING_CELL_MISSING       (no matching rule, OR no cell for the triple)
#       - BLOCK_MAPPING_CELL_BLOCKED       (cell enabled_state = blocked)
#       - BLOCK_REPAIRS_OSS                (a *blocked* repairs rule x OSS profile cell)
#       - BLOCK_MAPPING_CELL_REVIEW_REQUIRED (cell enabled_state = review_required)
#       - BLOCK_REQUIRED_CODE_MISSING      (active cell missing a mandatory code)
#   * Does NOT gate posted / company / date-range (DOC 05 preflight); does NOT resolve
#     a settlement's source DPI nor the deduction-vs-source rate (DOC 02 source
#     resolver); does NOT apply a profile's ``blocks_export_when_used`` nor the
#     credit-note test-gate — those are the preflight's. It hands the preflight the
#     resolved ``fiscal_profile`` + per-line cells so it can perform those checks.
#   * For a classifier-blocked move (blocked_mixed_324 / unsupported) it resolves no
#     lines and adds no mapping blockers — the classifier already owns that block.
#   * It is pure / read-only: it reads native fields + the matrix and writes nothing.

from odoo import api, models

from ..models import constants as C

# Phase-1 heuristic for the Repairs+OSS block. The data model has no explicit
# "this is the repairs rule" flag, so a *blocked* cell on an OSS profile is reported
# as BLOCK_REPAIRS_OSS only when the rule name matches one of these markers; any other
# blocked cell is the generic BLOCK_MAPPING_CELL_BLOCKED. This is intentionally
# conservative and is the one fragile spot in this service: renaming/translating the
# rule would silence the specific code (it still blocks, as BLOCK_MAPPING_CELL_BLOCKED).
# A future ``is_repairs`` boolean (or a category marker) on barani.pohoda.export.rule
# would make this robust. Flagged for the audit.
_REPAIRS_RULE_NAME_MARKERS = ('repair', 'opravy', 'oprava', 'kalibr')

# Mandatory dictionary codes an *active* cell must carry, by line role. DOC 05's
# go-live gate groups "account / VAT / KV" as the required codes for an exportable
# item; MOSS is required only for an active OSS-profile cell (DOC 03 validation rule 6).
_REQUIRED_CODES_NOTE = "account / VAT / KV always; MOSS only when the profile is OSS"


class LineResolution:
    """Resolution of one exportable ``account.move.line`` to POHODA codes.

    Attributes:
        line:           the ``account.move.line`` being mapped.
        role:           'supply' (regular/settlement supply), 'advance' (standalone
                        advance line), or 'deduction' (settlement negative-324).
        document_kind:  the cell dimension used for the lookup ('invoice',
                        'down_payment_deduction', or 'credit_note').
        rule:           the matched ``barani.pohoda.export.rule`` (empty if none matched).
        cell:           the matched mapping cell (empty if none / no rule).
        account_code / vat_code / kv_code / moss_code:
                        the resolved POHODA code strings (False where absent). The
                        account code always comes from the matched cell's
                        account_assignment_id; deduction cells are distinguished by
                        document_kind='down_payment_deduction'.
        missing_codes:  names of mandatory codes that were empty on an active cell.
        blockers:       list of ``BLOCK_*`` codes for this line (empty => mapped cleanly).
    """

    __slots__ = ('line', 'role', 'document_kind', 'rule', 'cell',
                 'account_code', 'vat_code', 'kv_code', 'moss_code',
                 'missing_codes', 'blockers')

    def __init__(self, line, role, document_kind, rule, cell,
                 account_code=False, vat_code=False, kv_code=False, moss_code=False,
                 missing_codes=None, blockers=None):
        self.line = line
        self.role = role
        self.document_kind = document_kind
        self.rule = rule
        self.cell = cell
        self.account_code = account_code
        self.vat_code = vat_code
        self.kv_code = kv_code
        self.moss_code = moss_code
        self.missing_codes = list(missing_codes or [])
        self.blockers = list(blockers or [])

    @property
    def is_resolved(self):
        """True if this line resolved to an *active* cell with no blockers."""
        return (not self.blockers and bool(self.cell)
                and self.cell.enabled_state == 'active')

    def __repr__(self):
        return ("LineResolution(role=%r, account=%r, vat=%r, kv=%r, blockers=%r)"
                % (self.role, self.account_code, self.vat_code,
                   self.kv_code, self.blockers))


class RuleResolutionResult:
    """Result of resolving a move's exportable lines against the mapping matrix.

    Attributes:
        move:           the resolved ``account.move``.
        doc_kind:       its classifier ``doc_kind``.
        fiscal_profile: the resolved ``barani.pohoda.fiscal.profile`` (empty when the
                        profile could not be resolved => PROFILE_NOT_FOUND).
        lines:          list of :class:`LineResolution` (empty for a classifier-blocked
                        move, or when the profile did not resolve).
        blockers:       de-duplicated union of the profile blocker + every line's blockers.
    """

    __slots__ = ('move', 'doc_kind', 'fiscal_profile', 'lines', 'blockers')

    def __init__(self, move, doc_kind, fiscal_profile, lines, blockers):
        self.move = move
        self.doc_kind = doc_kind
        self.fiscal_profile = fiscal_profile
        self.lines = list(lines)
        self.blockers = list(blockers)

    @property
    def is_blocked(self):
        """True if any mapping blocker fired (profile or line level)."""
        return bool(self.blockers)

    @property
    def is_resolved(self):
        """True if there were lines to map and every one resolved to an active cell.

        Vacuously False for a move with no mappable lines (e.g. blocked_mixed_324):
        that is a *classifier* block, surfaced by the classifier, not here.
        """
        return bool(self.lines) and not self.blockers and all(
            ln.is_resolved for ln in self.lines)

    def __repr__(self):
        return ("RuleResolutionResult(doc_kind=%r, lines=%d, blockers=%r)"
                % (self.doc_kind, len(self.lines), self.blockers))


class PohodaRuleResolver(models.AbstractModel):
    _name = 'barani.pohoda.rule.resolver'
    _description = "BARANI POHODA mapping-matrix rule resolver"

    # ------------------------------------------------------------------ public
    @api.model
    def resolve(self, move, classification=None):
        """Return a :class:`RuleResolutionResult` for one ``account.move``.

        ``classification`` may be passed in to reuse a ClassificationResult the caller
        already computed (e.g. the preflight). Read-only; works on draft moves.
        """
        move.ensure_one()
        if classification is None:
            classification = self.env['barani.pohoda.document.classifier'].classify(move)
        doc_kind = classification.doc_kind

        blockers = []
        lines = []

        # Classifier-blocked / unsupported moves have no mapping work. Return before
        # profile resolution so the resolver does not add a misleading
        # BLOCK_MAPPING_PROFILE_NOT_FOUND on top of the classifier's own blocker.
        targets = self._exportable_targets(move, classification)
        if not targets:
            return RuleResolutionResult(
                move, doc_kind, self.env['barani.pohoda.fiscal.profile'].browse(),
                lines, blockers)

        # A single fiscal profile is required before any exportable line can be mapped.
        profile, profile_blocker = self._resolve_profile(move)
        if profile_blocker:
            blockers.append(profile_blocker)
            return RuleResolutionResult(move, doc_kind, profile, lines, blockers)

        active_rules = self._active_rules(move)
        for line, role, document_kind in targets:
            lines.append(
                self._resolve_line(line, role, document_kind, profile, active_rules))

        for ln in lines:
            for code in ln.blockers:
                if code not in blockers:
                    blockers.append(code)
        return RuleResolutionResult(move, doc_kind, profile, lines, blockers)

    # ----------------------------------------------------------------- profile
    @api.model
    def _resolve_profile(self, move):
        """Resolve the move's fiscal position to exactly one active fiscal profile.

        Returns ``(profile_recordset, blocker_or_None)``. Zero or more-than-one match
        both yield BLOCK_MAPPING_PROFILE_NOT_FOUND (a fiscal position must map to one
        profile); on a block the returned profile recordset is empty.
        """
        Profile = self.env['barani.pohoda.fiscal.profile']
        fp = move.fiscal_position_id
        if not fp:
            return Profile.browse(), C.BLOCK_MAPPING_PROFILE_NOT_FOUND
        profiles = Profile.search([('active', '=', True), ('account_fiscal_position_ids', 'in', fp.ids)])
        if len(profiles) == 1:
            return profiles, None
        return Profile.browse(), C.BLOCK_MAPPING_PROFILE_NOT_FOUND

    # ------------------------------------------------------------------- lines
    @api.model
    def _exportable_targets(self, move, classification):
        """List of ``(line, role, document_kind)`` to resolve, by classifier doc_kind.

        Settlement deductions use document_kind='down_payment_deduction' per the
        Bucket B Step 1 handoff. For a credit note the classifier returns early
        without splitting lines, so the substantive lines are taken from the move here.
        """
        kind = classification.doc_kind
        if kind == 'regular_invoice':
            return [(line, 'supply', 'invoice') for line in classification.ordinary_lines]
        if kind == 'advance_invoice':
            return [(line, 'advance', 'invoice') for line in classification.dp_pos_lines]
        if kind == 'settlement_invoice':
            targets = [(line, 'supply', 'invoice') for line in classification.ordinary_lines]
            targets += [(line, 'deduction', 'down_payment_deduction') for line in classification.dp_neg_lines]
            return targets
        if kind == 'credit_note':
            real = move.invoice_line_ids.filtered(
                lambda l: (not l.display_type or l.display_type == 'product'))
            return [(line, 'supply', 'credit_note') for line in real]
        # blocked_mixed_324 / unsupported -> nothing to map here.
        return []

    @api.model
    def _resolve_line(self, line, role, document_kind, profile, active_rules):
        """Resolve one line to a :class:`LineResolution`."""
        empty_rule = self.env['barani.pohoda.export.rule']
        empty_cell = self.env['barani.pohoda.export.rule.mapping.cell']

        rule = self._match_rule(line, active_rules)
        if not rule:
            # No matching ordered rule => no mapping for this line.
            return LineResolution(line, role, document_kind, empty_rule, empty_cell,
                                  blockers=[C.BLOCK_MAPPING_CELL_MISSING])

        cell = self.env['barani.pohoda.export.rule.mapping.cell'].search(
            [('rule_id', '=', rule.id),
             ('fiscal_profile_id', '=', profile.id),
             ('document_kind', '=', document_kind)], limit=1)
        if not cell:
            return LineResolution(line, role, document_kind, rule, empty_cell,
                                  blockers=[C.BLOCK_MAPPING_CELL_MISSING])

        state = cell.enabled_state
        if state == 'blocked':
            if profile.is_oss and self._is_repairs_rule(rule):
                code = C.BLOCK_REPAIRS_OSS
            else:
                code = C.BLOCK_MAPPING_CELL_BLOCKED
            return LineResolution(line, role, document_kind, rule, cell, blockers=[code])
        if state == 'review_required':
            return LineResolution(line, role, document_kind, rule, cell,
                                  blockers=[C.BLOCK_MAPPING_CELL_REVIEW_REQUIRED])
        if state == 'not_applicable':
            # Intentionally maps to no codes (DOC 03: omit the optional field); not a block.
            return LineResolution(line, role, document_kind, rule, cell)

        # active: emit the codes and verify the mandatory ones are present.
        account_rec = cell.account_assignment_id
        account_field = 'account_assignment'
        account_code = account_rec.code if account_rec else False
        vat_code = cell.vat_classification_id.code if cell.vat_classification_id else False
        kv_code = (cell.control_statement_code_id.code
                   if cell.control_statement_code_id else False)
        moss_code = (cell.moss_service_type_id.code
                     if cell.moss_service_type_id else False)

        missing = []
        if not account_code:
            missing.append(account_field)
        if not vat_code:
            missing.append('vat_classification')
        if not kv_code:
            missing.append('control_statement_code')
        if profile.is_oss and not moss_code:
            missing.append('moss_service_type')

        blockers = [C.BLOCK_REQUIRED_CODE_MISSING] if missing else []

        # Architecture A v3 anti-double-count guard: a *taxable* advance or
        # advance-deduction line must not carry 'UN' (excluded from the VAT return) —
        # UN would drop / re-declare the VAT the line represents. UN remains valid for a
        # genuinely zero-rate / reverse-charge / outside-VAT-return line, so the guard
        # fires only when the line's effective VAT rate is non-zero. Scoped to the
        # advance (DPI) and deduction roles per the audit (A04 / A11).
        if vat_code == C.VAT_CLASSIFICATION_UN_CODE and self._is_taxable(line):
            if role == 'advance':
                blockers.append(C.BLOCK_TAXABLE_ADVANCE_CLASSIFICATION_UN)
            elif role == 'deduction':
                blockers.append(C.BLOCK_TAXABLE_ADVANCE_DEDUCTION_CLASSIFICATION_UN)

        return LineResolution(line, role, document_kind, rule, cell,
                              account_code=account_code, vat_code=vat_code,
                              kv_code=kv_code, moss_code=moss_code,
                              missing_codes=missing, blockers=blockers)

    # ----------------------------------------------------------------- helpers
    @api.model
    def _active_rules(self, move):
        """Active export rules for the move's company, ordered (specific beats broad)."""
        return self.env['barani.pohoda.export.rule'].search(
            [('company_id', '=', move.company_id.id),
             ('config_id.active', '=', True),
             ('active', '=', True)],
            order='sequence, id')

    @api.model
    def _match_rule(self, line, active_rules):
        """First ordered active rule matching ``line``; empty recordset if none.

        Product rules match on the line's product; category rules on the product's
        category (and, when ``include_child_categories``, its descendants), with
        ``also_match_products`` as an extra explicit-product escape hatch. Rules are
        already ordered by sequence, so the first match is the most specific.
        """
        product = line.product_id
        categ = product.categ_id if product else self.env['product.category'].browse()
        for rule in active_rules:
            if rule.match_mode == 'product':
                if product and product in rule.product_ids:
                    return rule
            elif rule.match_mode == 'category':
                if self._categ_in(categ, rule.category_ids, rule.include_child_categories):
                    return rule
                if rule.also_match_products and product and product in rule.product_ids:
                    return rule
        return self.env['barani.pohoda.export.rule'].browse()

    @api.model
    def _categ_in(self, categ, categories, include_children):
        """True if ``categ`` is in ``categories`` (or a descendant when allowed).

        Descendant test uses native ``parent_path`` (no DB search): a category C is a
        descendant of A iff ``C.parent_path`` starts with ``A.parent_path``.
        """
        if not categ or not categories:
            return False
        if categ in categories:
            return True
        if include_children and categ.parent_path:
            for parent in categories:
                if parent.parent_path and categ.parent_path.startswith(parent.parent_path):
                    return True
        return False

    @api.model
    def _is_repairs_rule(self, rule):
        """Phase-1 name heuristic for the Repairs rule (see _REPAIRS_RULE_NAME_MARKERS)."""
        name = (rule.name or '').lower()
        return any(marker in name for marker in _REPAIRS_RULE_NAME_MARKERS)

    @api.model
    def _is_taxable(self, line):
        """True if the line carries a non-zero effective VAT rate.

        Sign-safe (works for a negative deduction line, where base and the VAT delta
        are both negative so the ratio is still positive): uses the stored
        price_total / price_subtotal, mirroring the XML builder's rateVAT band logic.
        """
        base = line.price_subtotal
        if not base:
            return False
        rate = (line.price_total - base) / base * 100.0
        return abs(rate) > 0.0001  # > ~0 (matches the builder's none-band threshold)
