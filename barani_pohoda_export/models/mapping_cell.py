# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.

from odoo import fields, models

from .constants import DOCUMENT_KIND_SELECTION, CELL_STATE_SELECTION


class PohodaExportRuleMappingCell(models.Model):
    """One matrix cell = (rule x fiscal profile x document kind). Replaces the
    deleted flat 'barani.pohoda.export.rule.mapping' model. Inherits mail.thread
    because cell changes directly affect exported XML codes.

    PHASE 1 RESOLVER CONTRACT (Bucket B Step 1): the resolver uses the
    document_kind dimension explicitly. Regular, advance and settlement-supply lines
    use ``invoice`` cells; settlement negative-324 advance deductions use
    ``down_payment_deduction`` cells; credit-note lines use ``credit_note`` cells
    when accountant/import-test approved. ``downpayment_account_assignment_id`` is
    retained as a legacy/matrix-display helper, but the resolver reads
    ``account_assignment_id`` from the matched document-kind cell.
    """

    _name = 'barani.pohoda.export.rule.mapping.cell'
    _description = "BARANI POHODA Mapping Cell"
    _inherit = ['mail.thread']
    _order = 'rule_id, fiscal_profile_id, document_kind'
    _check_company_auto = True

    rule_id = fields.Many2one(
        'barani.pohoda.export.rule', required=True, ondelete='cascade', index=True)
    fiscal_profile_id = fields.Many2one(
        'barani.pohoda.fiscal.profile', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        'res.company', related='rule_id.company_id', store=True, index=True)
    document_kind = fields.Selection(
        DOCUMENT_KIND_SELECTION, required=True, default='invoice', tracking=True)
    enabled_state = fields.Selection(
        CELL_STATE_SELECTION, required=True, default='active', tracking=True,
        help="Blank is never valid. 'blocked' / 'review_required' block matching "
             "invoices in preflight.")

    # ondelete='restrict' so deleting a dictionary code cannot silently clear a
    # cell's export code. check_company on the account-assignment refs forces a
    # selected code to be global or in the cell's company.
    account_assignment_id = fields.Many2one(
        'barani.pohoda.account.assignment', string="Account assignment",
        ondelete='restrict', check_company=True, tracking=True)
    downpayment_account_assignment_id = fields.Many2one(
        'barani.pohoda.account.assignment', string="Down-payment deduction account",
        ondelete='restrict', check_company=True, tracking=True)
    vat_classification_id = fields.Many2one(
        'barani.pohoda.vat.classification', string="VAT classification",
        ondelete='restrict', tracking=True)
    control_statement_code_id = fields.Many2one(
        'barani.pohoda.control.statement.code', string="Control statement (KV) code",
        ondelete='restrict', tracking=True)
    moss_service_type_id = fields.Many2one(
        'barani.pohoda.moss.service.type', string="OSS / MOSS service type",
        ondelete='restrict', tracking=True)

    accountant_note = fields.Text()
    approved_by = fields.Many2one('res.users', string="Approved by", tracking=True)
    approved_at = fields.Datetime(string="Approved at", tracking=True)

    _sql_constraints = [
        ('rule_profile_kind_uniq', 'unique(rule_id, fiscal_profile_id, document_kind)',
         "Only one mapping cell may exist per rule, fiscal profile and document kind."),
    ]

    def name_get(self):
        kinds = dict(self._fields['document_kind'].selection)
        result = []
        for cell in self:
            label = "%s · %s · %s" % (
                cell.rule_id.name or "",
                cell.fiscal_profile_id.name or "",
                kinds.get(cell.document_kind, cell.document_kind or ""),
            )
            result.append((cell.id, label))
        return result
