# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 05 / Bucket C Step 1 — export preflight orchestration.
#
# The preflight is the single read-only pass that decides whether a set of moves is
# ready to export. For each move it sequences the Bucket B services:
#
#     classify  ->  (settlement only) source-resolve  ->  rule-resolve
#
# and aggregates EVERY blocker the services raise (shape, mapping profile, missing
# account/VAT codes, taxable-advance UN guard, advance source resolution, deduction-vs-
# source VAT-rate mismatch, ...) into a per-move and a batch-level report. On top of the
# service blockers it applies the DOC 05 move-level gates and the preflight-level checks
# that no single service owns:
#
#   * BLOCK_NOT_POSTED / BLOCK_WRONG_COMPANY — production export requires posted moves
#     belonging to the configured company (the services are deliberately
#     posting-agnostic; this is where the gate lives).
#   * BLOCK_AMOUNT_RECONCILIATION_FAILED — the substantive invoice lines must reconcile
#     to the move's stored untaxed base (catches an exportable-line-selection defect).
#   * BLOCK_SETTLEMENT_SUPPLY_LINES_MISSING / _DEDUCTION_LINES_MISSING — a settlement
#     must carry at least one supply line and at least one (negative-324) deduction line.
#
# This service is READ-ONLY and dormant: run() returns a report and writes nothing. The
# batch lifecycle (create the export.batch, build + XSD-validate the XML, persist the
# request/response + hashes, set the batch state via the service-only write path) is
# Bucket C Step 2, which consumes this report.

from odoo import _, api, models
from odoo.exceptions import UserError

from ..models import constants as C

# Date fields a batch may scope on. Derived from DATE_SOURCE_SELECTION (whose keys are
# real account.move field names) so the allow-list can never drift from the model, and
# the field name is never attacker-controlled.
_ALLOWED_DATE_FIELDS = tuple(key for key, _label in C.DATE_SOURCE_SELECTION)


class MovePreflightResult:
    """Preflight outcome for one ``account.move``."""

    __slots__ = ('move', 'doc_kind', 'blockers', 'line_resolutions',
                 'source_result', 'classification')

    def __init__(self, move, doc_kind, blockers=None, line_resolutions=None,
                 source_result=None, classification=None):
        self.move = move
        self.doc_kind = doc_kind
        self.blockers = list(blockers or [])
        self.line_resolutions = list(line_resolutions or [])
        self.source_result = source_result
        self.classification = classification

    @property
    def can_export(self):
        """No blockers and a kind that can produce XML (not unsupported / mixed-324).

        Note: credit notes pass the preflight when they resolve, but remain Phase-1
        builder-gated (``is_test_gated``); the batch lifecycle decides whether to emit
        them. The preflight is a *resolvability/correctness* gate, not a feature flag.
        """
        return not self.blockers and self.doc_kind not in C.BLOCKED_DOC_KINDS

    @property
    def is_settlement(self):
        return self.doc_kind == 'settlement_invoice'

    @property
    def is_test_gated(self):
        return self.doc_kind == 'credit_note'

    def __repr__(self):
        return ('MovePreflightResult(move=%s, doc_kind=%s, can_export=%s, blockers=%s)'
                % (self.move.id if self.move else None, self.doc_kind,
                   self.can_export, self.blockers))


class PreflightResult:
    """Aggregate preflight outcome over a set of moves."""

    __slots__ = ('move_results',)

    def __init__(self, move_results=None):
        self.move_results = list(move_results or [])

    @property
    def exportable_moves(self):
        return [r for r in self.move_results if r.can_export]

    @property
    def blocked_moves(self):
        return [r for r in self.move_results if not r.can_export]

    @property
    def is_blocked(self):
        """True if any move cannot be exported."""
        return any(not r.can_export for r in self.move_results)

    @property
    def all_blockers(self):
        """De-duplicated union of every move's blockers, order-preserving."""
        seen = set()
        out = []
        for r in self.move_results:
            for b in r.blockers:
                if b not in seen:
                    seen.add(b)
                    out.append(b)
        return out

    def __repr__(self):
        return ('PreflightResult(moves=%s, exportable=%s, blocked=%s, blockers=%s)'
                % (len(self.move_results), len(self.exportable_moves),
                   len(self.blocked_moves), self.all_blockers))


class PohodaPreflight(models.AbstractModel):
    _name = 'barani.pohoda.preflight'
    _description = "BARANI POHODA export preflight orchestration (DOC 05, Bucket C)"

    # ------------------------------------------------------------------ public
    @api.model
    def run(self, moves, config=None, require_posted=True):
        """Run the full read-only preflight over ``moves`` and return a report.

        Writes nothing. On top of the service blockers, the DOC 05 move-level gates
        are applied here (the services themselves are deliberately posting-agnostic):

        * ``BLOCK_NOT_POSTED`` — production export requires posted moves. Pass
          ``require_posted=False`` for an informative dry-run on drafts.
        * ``BLOCK_WRONG_COMPANY`` — with ``config`` given, every move must belong to
          the config's company; without it, the move's company must have an active
          export configuration.

        Move-level gates add blockers but do not skip classification/resolution, so a
        dry-run on a draft still reports its mapping problems.
        """
        Classifier = self.env['barani.pohoda.document.classifier']
        RuleResolver = self.env['barani.pohoda.rule.resolver']
        SourceResolver = self.env['barani.pohoda.source.resolver']
        Config = self.env['barani.pohoda.export.config']
        if config:
            config.ensure_one()
        config_by_company = {}

        results = []
        for move in moves:
            blockers = []

            # DOC 05 move-level gates.
            if require_posted and move.state != 'posted':
                blockers.append(C.BLOCK_NOT_POSTED)
            if config:
                if move.company_id != config.company_id:
                    blockers.append(C.BLOCK_WRONG_COMPANY)
            else:
                cid = move.company_id.id
                if cid not in config_by_company:
                    config_by_company[cid] = Config.search(
                        [('active', '=', True), ('company_id', '=', cid)], limit=1)
                if not config_by_company[cid]:
                    blockers.append(C.BLOCK_WRONG_COMPANY)

            classification = Classifier.classify(move)
            self._extend(blockers, classification.blockers)
            line_resolutions = []
            source_result = None

            # Classifier-blocked moves (unsupported / mixed-324) carry their own blocker
            # and have no resolvable mapping work; skip the resolvers to avoid piling a
            # misleading mapping blocker on top.
            if classification.doc_kind not in C.BLOCKED_DOC_KINDS:
                rule_result = RuleResolver.resolve(move, classification=classification)
                self._extend(blockers, rule_result.blockers)
                line_resolutions = rule_result.lines
                self._extend(blockers, self._advance_mapping_checks(line_resolutions))

                if classification.doc_kind == 'settlement_invoice':
                    source_result = SourceResolver.resolve(
                        move, classification=classification)
                    self._extend(blockers, source_result.blockers)
                    self._extend(blockers, self._settlement_line_checks(classification))

                self._extend(blockers, self._amount_check(move))

            results.append(MovePreflightResult(
                move=move, doc_kind=classification.doc_kind, blockers=blockers,
                line_resolutions=line_resolutions, source_result=source_result,
                classification=classification))
        return PreflightResult(results)

    @api.model
    def collect_moves(self, config, start_date=None, end_date=None, date_field=None):
        """Posted customer invoices/refunds in ``config``'s company within a date range.

        ``date_field`` defaults to the config's ``key_date`` (one of
        ``DATE_SOURCE_SELECTION``); only posted ``out_invoice`` / ``out_refund`` moves are
        returned (you export issued documents, not drafts). When the config scopes
        ``journal_ids``, only moves in those journals are collected (DOC 01). Returns an
        ``account.move`` recordset ordered by the chosen date.
        """
        if not config:
            raise UserError(_("A POHODA export configuration is required to collect moves."))
        field = date_field or config.key_date or 'invoice_date'
        if field not in _ALLOWED_DATE_FIELDS:
            field = 'invoice_date'
        domain = [
            ('company_id', '=', config.company_id.id),
            ('move_type', 'in', ('out_invoice', 'out_refund')),
            ('state', '=', 'posted'),
        ]
        if config.journal_ids:
            domain.append(('journal_id', 'in', config.journal_ids.ids))
        if start_date:
            domain.append((field, '>=', start_date))
        if end_date:
            domain.append((field, '<=', end_date))
        return self.env['account.move'].search(domain, order='%s, id' % field)

    # ----------------------------------------------------------------- checks
    @api.model
    def _amount_check(self, move):
        """The substantive invoice lines must reconcile to the move's untaxed base.

        Doc-kind-independent self-consistency guard. ``amount_untaxed`` is, by Odoo's
        definition, the sum of the substantive line subtotals, so a healthy move always
        passes; a mismatch means the exporter's line selection dropped or double-counted a
        substantive line. Only the untaxed base is checked here (it is an exact identity,
        so there are no per-tax-rounding false positives); VAT/total reconciliation is
        recomputed POHODA-side, and the cross-document 324-netting check (DPIs vs their
        settlement) is a multi-move check deferred to a later step.
        """
        currency = move.currency_id or move.company_id.currency_id
        real = move.invoice_line_ids.filtered(
            lambda l: (not l.display_type) or l.display_type == 'product')
        subtotal = sum(real.mapped('price_subtotal'))
        if currency.compare_amounts(subtotal, move.amount_untaxed) != 0:
            return [C.BLOCK_AMOUNT_RECONCILIATION_FAILED]
        return []

    @api.model
    def _advance_mapping_checks(self, line_resolutions):
        """Advance / deduction lines must map to a received-advance (324) předkontace.

        Audit-required (Architecture A v3): an advance (DPI) line or a settlement
        advance-deduction line whose resolved cell carries an account assignment NOT
        marked ``is_advance_account`` would post the 324 movement to a revenue
        předkontace in POHODA. The check reads the marker, not the literal code, so it
        survives the accountant remapping the POHODA code. Cells without an assignment
        are already covered by BLOCK_REQUIRED_CODE_MISSING.
        """
        out = []
        for ln in line_resolutions:
            if ln.role not in ('advance', 'deduction'):
                continue
            assignment = ln.cell.account_assignment_id if ln.cell else False
            if assignment and not assignment.is_advance_account:
                code = (C.BLOCK_ADVANCE_ACCOUNT_MAPPING_NOT_324 if ln.role == 'advance'
                        else C.BLOCK_SETTLEMENT_DEDUCTION_ACCOUNT_MAPPING_NOT_324)
                if code not in out:
                    out.append(code)
        return out

    @api.model
    def _settlement_line_checks(self, classification):
        """A settlement must carry both a supply line and a deduction line.

        The classifier's shape rules already require both before a move becomes a
        ``settlement_invoice``, so in normal flow these never fire; they make the
        invariant explicit and guard against a future classifier change.
        """
        out = []
        if not classification.ordinary_lines:
            out.append(C.BLOCK_SETTLEMENT_SUPPLY_LINES_MISSING)
        if not classification.dp_neg_lines:
            out.append(C.BLOCK_SETTLEMENT_DEDUCTION_LINES_MISSING)
        return out

    # ----------------------------------------------------------------- helper
    @staticmethod
    def _extend(acc, more):
        """Append blockers from ``more`` into ``acc`` without duplicates (order-preserving)."""
        for b in more:
            if b not in acc:
                acc.append(b)
