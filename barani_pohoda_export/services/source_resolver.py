# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 02 — Settlement advance-source resolver.
#
# For a settlement invoice (ordinary supply + negative-324 advance deduction), this
# service resolves each deduction line back to the *source* down-payment invoice
# (DPI) it settles, using ONLY native Odoo links:
#
#     account.move.line  (the -324 deduction on the settlement)
#         .sale_line_ids          -> the down-payment sale.order.line
#         .invoice_lines          -> every move line billed from that SO line
#         .move_id                -> their account.move    (minus the settlement)
#
# It never parses DDS data, document references, or free-form notes. The resolved
# source move is what the DOC 04 XML builder needs to fill the settlement's advance
# deduction (a negative "odpočet zálohy" item bound to the source) and its
# ``sourceDocument`` for each applied advance.
#
# Responsibility boundary (mirrors the classifier — see its header):
#   * This service owns the *resolution* blockers — facts knowable from the move graph
#     (and the source's own VAT rate) alone:
#       - BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_FOUND          (no single source advance)
#       - BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_VALID_ADVANCE  (source isn't an advance_invoice)
#       - BLOCK_SETTLEMENT_ADVANCE_VAT_RATE_MISMATCH         (deduction rate != source rate)
#   * It does NOT emit BLOCK_SETTLEMENT_ADVANCE_XML_SOURCE_NOT_AVAILABLE. That code
#     asks whether the source DPI has already been exported to POHODA (an
#     export-state / sequencing fact, read from ``barani_pohoda_export_state``),
#     which is the DOC 05 preflight's concern — exactly as the classifier defers
#     posted/company/date to DOC 05. The resolver hands the preflight the resolved
#     ``source_move`` so it can perform that check.
#   * It is pure / read-only: it reads native fields and writes nothing.

from odoo import api, models

from ..models import constants as C


class AdvanceSource:
    """One resolved (or unresolved) advance deduction on a settlement.

    Attributes:
        deduction_line:  the negative-324 ``account.move.line`` on the settlement.
        source_move:     the resolved source advance ``account.move`` — a single record
                         when ``blockers`` is empty; for NOT_VALID_ADVANCE it holds the
                         offending linked candidate(s) for diagnostics; empty otherwise.
        amount:          the applied advance amount (gross, VAT-inclusive), positive, in
                         the move currency (``abs(price_total)``). For a zero-VAT advance
                         gross == base; for a VAT-inclusive one it is the gross.
        deduction_rate:  effective VAT rate of the deduction line (VAT / base, a fraction;
                         0.0 for a zero-VAT deduction).
        source_rate:     effective VAT rate of the source advance (same convention).
        blockers:        list of ``BLOCK_*`` codes for this deduction (empty => ok).
    """

    __slots__ = ('deduction_line', 'source_move', 'amount',
                 'deduction_rate', 'source_rate', 'blockers')

    def __init__(self, deduction_line, source_move, amount, blockers,
                 deduction_rate=0.0, source_rate=0.0):
        self.deduction_line = deduction_line
        self.source_move = source_move
        self.amount = amount
        self.deduction_rate = deduction_rate
        self.source_rate = source_rate
        self.blockers = list(blockers)

    @property
    def is_resolved(self):
        """True if this deduction resolved to exactly one valid advance at a matching rate."""
        return not self.blockers and len(self.source_move) == 1

    def __repr__(self):
        return "AdvanceSource(source_move=%r, amount=%r, blockers=%r)" % (
            self.source_move, self.amount, self.blockers)


class SourceResolutionResult:
    """Result of resolving a settlement's advance deductions.

    Attributes:
        move:      the ``account.move`` that was resolved.
        doc_kind:  its classifier ``doc_kind`` (``advances`` is empty unless settlement).
        advances:  list of :class:`AdvanceSource`, one per negative-324 line.
        blockers:  de-duplicated union of every advance's blockers.
    """

    __slots__ = ('move', 'doc_kind', 'advances', 'blockers')

    def __init__(self, move, doc_kind, advances, blockers):
        self.move = move
        self.doc_kind = doc_kind
        self.advances = list(advances)
        self.blockers = list(blockers)

    @property
    def is_settlement(self):
        return self.doc_kind == 'settlement_invoice'

    @property
    def is_blocked(self):
        """True if any deduction failed to resolve to a valid advance at a matching rate."""
        return bool(self.blockers)

    @property
    def source_moves(self):
        """The resolved source DPI moves (one per cleanly-resolved advance)."""
        moves = self.move.browse()
        for advance in self.advances:
            if advance.is_resolved:
                moves |= advance.source_move
        return moves

    def __repr__(self):
        return "SourceResolutionResult(doc_kind=%r, advances=%d, blockers=%r)" % (
            self.doc_kind, len(self.advances), self.blockers)


class PohodaSourceResolver(models.AbstractModel):
    _name = 'barani.pohoda.source.resolver'
    _description = "BARANI POHODA settlement advance-source resolver"

    # ------------------------------------------------------------------ public
    @api.model
    def resolve(self, move, classification=None):
        """Return a :class:`SourceResolutionResult` for one ``account.move``.

        For non-settlement moves the result carries an empty ``advances`` list and is
        never blocked on resolution grounds. ``classification`` may be passed in to
        reuse a :class:`ClassificationResult` the caller already computed.
        """
        move.ensure_one()
        if classification is None:
            classification = self.env['barani.pohoda.document.classifier'].classify(move)

        advances = []
        if classification.doc_kind == 'settlement_invoice':
            for line in classification.dp_neg_lines:
                advances.append(self._resolve_one(move, line))

        blockers = []
        for advance in advances:
            for code in advance.blockers:
                if code not in blockers:
                    blockers.append(code)
        return SourceResolutionResult(move, classification.doc_kind, advances, blockers)

    # ----------------------------------------------------------------- helpers
    @api.model
    def _resolve_one(self, settlement, deduction_line):
        """Resolve one negative-324 deduction line to its source advance move.

        On a clean single-source resolution, also check that the deduction's VAT rate
        equals the source advance's rate (so the advance VAT is reversed at the rate it
        was charged); a mismatch is BLOCK_SETTLEMENT_ADVANCE_VAT_RATE_MISMATCH.
        """
        empty = self.env['account.move']
        currency = settlement.currency_id or settlement.company_id.currency_id
        amount = abs(deduction_line.price_total)

        candidates = self._linked_moves(deduction_line) - settlement
        if not candidates:
            return AdvanceSource(
                deduction_line, empty, amount,
                [C.BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_FOUND])

        classifier = self.env['barani.pohoda.document.classifier']
        valid = empty
        valid_cls = None
        for candidate in candidates:
            cls = classifier.classify(candidate)
            if cls.doc_kind == 'advance_invoice':
                valid |= candidate
                valid_cls = cls

        # Exactly one advance among the linked moves => unambiguous source. This also
        # discards partial-settlement noise: another settlement that shares the
        # down-payment sale line classifies as 'settlement_invoice', not advance_invoice.
        if len(valid) == 1:
            blockers = []
            deduction_rate = self._lines_vat_rate(deduction_line, currency)
            source_rate = self._lines_vat_rate(valid_cls.dp_pos_lines, currency)
            if abs(deduction_rate - source_rate) > C.VAT_RATE_MATCH_TOLERANCE:
                blockers.append(C.BLOCK_SETTLEMENT_ADVANCE_VAT_RATE_MISMATCH)
            return AdvanceSource(deduction_line, valid, amount, blockers,
                                 deduction_rate, source_rate)

        # Linked moves exist but none is a valid advance => the source is not a valid
        # advance (e.g. an ordinary invoice or a mixed-324 doc mis-linked).
        if not valid:
            return AdvanceSource(
                deduction_line, candidates, amount,
                [C.BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_VALID_ADVANCE])

        # More than one advance linked to a single deduction line: the source cannot be
        # pinned to one document, so there is no single source to reference.
        return AdvanceSource(
            deduction_line, empty, amount,
            [C.BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_FOUND])

    @api.model
    def _lines_vat_rate(self, lines, currency):
        """Effective VAT rate (VAT / base) across ``lines`` as a fraction; 0.0 if base ~ 0.

        Uses native amounts only — ``sum(price_total - price_subtotal) / sum(price_subtotal)``
        — so a zero-VAT advance/deduction returns 0.0 and a 23% one returns ~0.23. The sign
        cancels (a negative deduction line yields the same positive rate as its source).
        Read-only.
        """
        base = 0.0
        vat = 0.0
        for line in lines:
            base += line.price_subtotal
            vat += line.price_total - line.price_subtotal
        if currency.is_zero(base):
            return 0.0
        return vat / base

    @api.model
    def _linked_moves(self, deduction_line):
        """Native source-move set for a deduction line.

        Walks ``sale_line_ids -> invoice_lines -> move_id`` and returns an
        ``account.move`` recordset (possibly empty). No DDS / text is consulted.
        """
        moves = self.env['account.move']
        for sale_line in deduction_line.sale_line_ids:
            for invoice_line in sale_line.invoice_lines:
                moves |= invoice_line.move_id
        return moves
