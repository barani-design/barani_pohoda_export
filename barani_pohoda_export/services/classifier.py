# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 02 — Document classifier.
#
# Classifies a single ``account.move`` into a ``barani_doc_kind`` using ONLY native
# Odoo accounting facts: ``move_type``, the G/L account code of each line, the
# sign of each substantive line's stored ``price_subtotal``. It deliberately does
# not use VAT/gross-vs-net to decide whether a positive-324 line is an advance;
# under OQ-1 v2 a VAT-bearing advance is valid. It never reads DDS data, flags
# or text.
#
# Responsibility boundary (deliberate — see the build notes / assumptions):
#   * This service decides the document KIND from the move's *shape*. Under OQ-1 v2
#     advances are tax-config-driven: a positive-324 invoice is an ``advance_invoice``
#     whatever its VAT (0 for EU B2B / non-EU export, the rate for domestic / OSS), so
#     VAT on an advance or on a deduction is NOT a defect. The only shape block it emits
#     is mixed-324 (a positive-324 line mixed with ordinary or negative-324 lines), plus
#     an unsupported move type.
#   * It does NOT gate on posted / company / date-range — that is the DOC 05
#     preflight — and it does NOT resolve a settlement's source DPIs nor check the
#     deduction-vs-source VAT rate — that is the DOC 02 source resolver. A settlement is
#     classified by shape here; the ``BLOCK_SETTLEMENT_ADVANCE_*`` codes are added
#     downstream.
#   * It is pure / read-only: it reads native fields and writes nothing.

from odoo import api, models

from ..models import constants as C


class ClassificationResult:
    """Result of classifying one ``account.move``.

    Attributes:
        doc_kind:       one of ``constants.BARANI_DOC_KIND_SELECTION`` values.
        blockers:       list of ``BLOCK_*`` codes (empty => no shape problem).
        ordinary_lines / dp_pos_lines / dp_neg_lines / zero_lines:
                        ``account.move.line`` recordsets, for downstream use
                        (XML builder, source resolver, preflight).
    """

    __slots__ = ('doc_kind', 'blockers', 'ordinary_lines',
                 'dp_pos_lines', 'dp_neg_lines', 'zero_lines')

    def __init__(self, doc_kind, blockers, ordinary_lines,
                 dp_pos_lines, dp_neg_lines, zero_lines):
        self.doc_kind = doc_kind
        self.blockers = list(blockers)
        self.ordinary_lines = ordinary_lines
        self.dp_pos_lines = dp_pos_lines
        self.dp_neg_lines = dp_neg_lines
        self.zero_lines = zero_lines

    @property
    def is_blocked(self):
        """True if this move must not produce production XML as-is."""
        return self.doc_kind in C.BLOCKED_DOC_KINDS or bool(self.blockers)

    @property
    def is_test_gated(self):
        """Credit notes are Phase 1 test-gated (DOC 02)."""
        return self.doc_kind == 'credit_note'

    def __repr__(self):
        return "ClassificationResult(doc_kind=%r, blockers=%r)" % (
            self.doc_kind, self.blockers)


class PohodaDocumentClassifier(models.AbstractModel):
    _name = 'barani.pohoda.document.classifier'
    _description = "BARANI POHODA document classifier"

    # ------------------------------------------------------------------ public
    @api.model
    def classify(self, move):
        """Return a :class:`ClassificationResult` for one ``account.move``.

        Shape-only. Under OQ-1 v2 a positive-324 invoice is an ``advance_invoice``
        regardless of its VAT, so the mixed-324 shapes are evaluated before the advance
        branch, but no VAT / gross-vs-net check blocks an advance.
        """
        move.ensure_one()
        currency = move.currency_id or move.company_id.currency_id
        empty = self.env['account.move.line']

        # 1. Credit notes (out_refund) -> Phase 1 test-gated. The advance vs
        #    ordinary-goods credit-note split is a separate, import-tested path
        #    (DOC 02) handled with the XML builder/mapping; intentionally NOT
        #    merged into ordinary credit-note logic here.
        if move.move_type == 'out_refund':
            return ClassificationResult('credit_note', [], empty, empty, empty, empty)

        # 2. Only customer invoices are supported beyond credit notes.
        if move.move_type != 'out_invoice':
            return ClassificationResult(
                'unsupported', [C.BLOCK_UNSUPPORTED_MOVE_TYPE],
                empty, empty, empty, empty)

        ordinary, dp_pos, dp_neg, zero = self._split_lines(move, currency)

        def result(doc_kind, blockers):
            return ClassificationResult(
                doc_kind, blockers, ordinary, dp_pos, dp_neg, zero)

        # 3. Regular invoice: ordinary lines only, no advance (324) lines.
        if ordinary and not dp_pos and not dp_neg:
            return result('regular_invoice', [])

        # 4. Settlement: ordinary supply + negative-324 deduction, no positive 324.
        #    The deduction may carry VAT (a VAT-inclusive advance is reversed at its own
        #    rate) — that is expected, not a defect. Source resolution and the
        #    deduction-vs-source rate check (BLOCK_SETTLEMENT_ADVANCE_VAT_RATE_MISMATCH)
        #    are the resolver's job; no shape blocker here.
        if ordinary and dp_neg and not dp_pos:
            return result('settlement_invoice', [])

        # 5. Mixed 324: positive 324 + ordinary lines -> malformed.
        if dp_pos and ordinary:
            return result('blocked_mixed_324',
                          [C.BLOCK_MIXED_POSITIVE_324_AND_ORDINARY_LINES])

        # 6. Positive + negative 324 without ordinary lines -> malformed mix.
        if dp_pos and dp_neg:
            return result('blocked_mixed_324',
                          [C.BLOCK_MIXED_POSITIVE_324_AND_ORDINARY_LINES])

        # 7. Advance invoice (down payment): positive-324 only. VAT is read from the
        #    line and may be zero (EU B2B / non-EU export) or positive (domestic / OSS);
        #    a VAT-bearing advance is valid (OQ-1 v2). No block on VAT or gross != net.
        if dp_pos and not ordinary and not dp_neg:
            return result('advance_invoice', [])

        # 8. Anything else (e.g. a move with no substantive invoice lines).
        return result('unsupported', [C.BLOCK_UNSUPPORTED_MOVE_TYPE])

    # ----------------------------------------------------------------- helpers
    @api.model
    def _split_lines(self, move, currency):
        """Split substantive invoice lines into ordinary / +324 / -324 / ~0.

        Display-only section and note lines are ignored for classification. A
        substantive line with no account is treated as ordinary, not ignored, so
        a draft invoice with a positive-324 line plus an incomplete ordinary line
        cannot be misclassified as a clean advance.
        """
        real = move.invoice_line_ids.filtered(
            lambda l: (not l.display_type or l.display_type == 'product'))
        advance = real.filtered(lambda l: self._is_advance_line(l))
        ordinary = real - advance
        dp_pos = advance.filtered(
            lambda l: currency.compare_amounts(l.price_subtotal, 0.0) > 0)
        dp_neg = advance.filtered(
            lambda l: currency.compare_amounts(l.price_subtotal, 0.0) < 0)
        # Zero-value 324 lines (gross within rounding of 0) are non-substantive and
        # are intentionally ignored by classify(): they land in neither `ordinary`
        # nor the dp_* buckets, so e.g. ordinary + a 0.00 advance line stays a regular
        # invoice. They are returned for diagnostics / preflight visibility only.
        zero = advance - dp_pos - dp_neg
        return ordinary, dp_pos, dp_neg, zero

    @api.model
    def _is_advance_line(self, line):
        """True if a line is posted to a received-advance G/L account.

        Matches any prefix in C.ADVANCE_ACCOUNT_PREFIXES (DOC 02: currently '324').
        str.startswith accepts a tuple, so adding '475' there needs no code change.
        """
        account = line.account_id
        code = (account.code or '') if account else ''
        return bool(code and code.startswith(C.ADVANCE_ACCOUNT_PREFIXES))
