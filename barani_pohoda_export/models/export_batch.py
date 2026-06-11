# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .constants import (
    DATE_SOURCE_SELECTION,
    BARANI_DOC_KIND_SELECTION,
    EXPORT_BATCH_STATE_SELECTION,
    EXPORT_BATCH_MOVE_STATE_SELECTION,
)


class PohodaExportBatch(models.Model):
    """Audit record of one export run: the request/response XML, their hashes,
    the config/mapping version hashes, and the per-document results. Inherits
    mail.thread so state transitions are logged; deletion is restricted to
    draft/cancelled batches to preserve the audit trail."""

    _name = 'barani.pohoda.export.batch'
    _description = "BARANI POHODA Export Batch"
    _inherit = ['mail.thread']
    _order = 'create_date desc, id desc'
    _check_company_auto = True

    # Result/lifecycle fields written only by the export service or its action
    # methods (never by direct UI/RPC writes). state and created_by are audit
    # facts; the service writes them via _service_write_audit_fields().
    _SERVICE_ONLY_FIELDS = frozenset({
        'state',
        'request_attachment_id', 'response_attachment_id',
        'request_sha256', 'response_sha256',
        'mapping_version_hash', 'config_version_hash',
        'validation_summary', 'created_by', 'sent_by', 'sent_at',
    })
    # Scope fields that may only change while the batch is still draft.
    _SCOPE_FIELDS = frozenset({
        'company_id', 'config_id', 'date_field', 'start_date', 'end_date'})

    name = fields.Char(required=True, default="New", copy=False, tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company)
    config_id = fields.Many2one(
        'barani.pohoda.export.config', required=True, ondelete='restrict',
        check_company=True, tracking=True)

    start_date = fields.Date(tracking=True)
    end_date = fields.Date(tracking=True)
    date_field = fields.Selection(
        DATE_SOURCE_SELECTION, default='invoice_date', required=True)

    state = fields.Selection(
        EXPORT_BATCH_STATE_SELECTION, default='draft', required=True,
        copy=False, tracking=True)

    request_attachment_id = fields.Many2one(
        'ir.attachment', string="Request XML", copy=False, ondelete='restrict')
    response_attachment_id = fields.Many2one(
        'ir.attachment', string="Response XML", copy=False, ondelete='restrict')
    request_sha256 = fields.Char(string="Request SHA-256", copy=False)
    response_sha256 = fields.Char(string="Response SHA-256", copy=False)
    validation_summary = fields.Text(copy=False)
    mapping_version_hash = fields.Char(copy=False)
    config_version_hash = fields.Char(copy=False)

    created_by = fields.Many2one(
        'res.users', string="Export created by",
        default=lambda self: self.env.user, copy=False)
    sent_by = fields.Many2one('res.users', string="Sent by", copy=False, tracking=True)
    sent_at = fields.Datetime(string="Sent at", copy=False, tracking=True)

    batch_move_ids = fields.One2many(
        'barani.pohoda.export.batch.move', 'batch_id', string="Documents", copy=False)
    move_count = fields.Integer(compute='_compute_move_count')

    @api.depends('batch_move_ids')
    def _compute_move_count(self):
        for batch in self:
            batch.move_count = len(batch.batch_move_ids)

    @api.constrains('start_date', 'end_date')
    def _check_date_range(self):
        for batch in self:
            if batch.start_date and batch.end_date and batch.start_date > batch.end_date:
                raise ValidationError(_(
                    "The start date must be on or before the end date."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'barani.pohoda.export.batch') or 'New'
            # Derive the company from the config when not set explicitly, so a
            # batch built from a config in another company does not trip the
            # company default and fail the check_company validation.
            if vals.get('config_id') and not vals.get('company_id'):
                config = self.env['barani.pohoda.export.config'].browse(vals['config_id'])
                if config.company_id:
                    vals['company_id'] = config.company_id.id
        return super().create(vals_list)

    def unlink(self):
        protected = self.filtered(lambda b: b.state not in ('draft', 'cancelled'))
        if protected:
            raise UserError(_(
                "Export batches that have progressed beyond draft are audit records "
                "and cannot be deleted. Cancel the batch instead."))
        return super().unlink()

    def write(self, vals):
        # Audit integrity. Public writes (UI/RPC) never touch service-owned audit
        # fields, and scope is frozen once the batch leaves draft. The export
        # service (DOC 02-05) writes audit fields via _service_write_audit_fields(),
        # which is private and therefore not reachable over RPC -- so context can
        # no longer be used as a bypass.
        forbidden = self._SERVICE_ONLY_FIELDS.intersection(vals)
        if forbidden:
            raise UserError(_(
                "These export-audit fields are maintained by the export service "
                "and cannot be edited directly: %s.") % ", ".join(sorted(forbidden)))
        scope = self._SCOPE_FIELDS.intersection(vals)
        if scope and any(b.state != 'draft' for b in self):
            raise UserError(_(
                "The batch scope can only be changed while the batch is in Draft."))
        return super().write(vals)

    def _service_write_audit_fields(self, vals):
        """Trusted-path write for the export service and its state-transition
        actions (DOC 02-05). Private (underscore-prefixed) so it is not exposed
        over RPC; it bypasses the public-write audit guard above. Callers must be
        module service code, not user input."""
        return super().write(vals)

    # ── Lifecycle buttons (thin delegates; the service holds the logic+guards) ──
    def action_validate(self):
        self.ensure_one()
        self.env['barani.pohoda.export.service'].action_validate(self)
        return True

    def action_generate_xml(self):
        self.ensure_one()
        self.env['barani.pohoda.export.service'].action_generate_xml(self)
        return True

    def action_mark_sent(self):
        self.ensure_one()
        return self.env['barani.pohoda.export.service'].action_mark_sent(self)

    def action_cancel(self):
        self.ensure_one()
        return self.env['barani.pohoda.export.service'].action_cancel(self)

    def action_reset_to_draft(self):
        self.ensure_one()
        return self.env['barani.pohoda.export.service'].action_reset_to_draft(self)


class PohodaExportBatchMove(models.Model):
    """One exported / attempted document inside a batch, with its classifier doc
    kind, per-document XML identifiers and the parsed POHODA response.

    ACL note: create/write are intentionally denied to Export Users and Managers
    so these per-document audit rows stay service-owned. The export service
    (DOC 02-05) will create them through a narrow, explicitly justified sudo path
    with company/rule checks -- not by granting users direct create rights."""

    _name = 'barani.pohoda.export.batch.move'
    _description = "BARANI POHODA Export Batch Document"
    _order = 'batch_id, id'
    _check_company_auto = True

    batch_id = fields.Many2one(
        'barani.pohoda.export.batch', required=True, ondelete='cascade', index=True)
    move_id = fields.Many2one(
        'account.move', required=True, ondelete='restrict', index=True,
        check_company=True)
    company_id = fields.Many2one(
        related='batch_id.company_id', store=True, index=True)

    barani_doc_kind = fields.Selection(
        BARANI_DOC_KIND_SELECTION, string="BARANI document kind")
    state = fields.Selection(
        EXPORT_BATCH_MOVE_STATE_SELECTION, default='pending', required=True)

    pohoda_record_id = fields.Char(string="POHODA record ID")
    pohoda_document_number = fields.Char(string="POHODA document number")
    source_hash = fields.Char()
    xml_item_id = fields.Char(string="XML item ID")
    validation_error_code = fields.Char()
    validation_error = fields.Text()
    response_state = fields.Char()
    response_code = fields.Char()
    response_message = fields.Text()

    _sql_constraints = [
        ('batch_move_uniq', 'unique(batch_id, move_id)',
         "A document may appear only once in the same POHODA export batch."),
    ]

    # ── Narrow service write path (resolves the deferred sudo finding) ──────
    # ACL denies create/write/unlink on this model to BOTH export groups so the
    # per-document audit rows stay service-owned. The export service and the
    # response parser go through the three private methods below, which (a) are
    # underscore-prefixed and therefore not reachable over RPC, (b) require the
    # acting user to be an Export Manager (or a trusted su context such as the
    # test runner), and (c) enforce company coherence before elevating. This is
    # the "narrow, explicitly justified sudo path with company/rule checks" the
    # ACL note promises -- NOT a blanket grant.

    def _check_service_caller(self, companies):
        if not self.env.su and not self.env.user.has_group(
                'barani_pohoda_export.group_export_manager'):
            raise AccessError(_(
                "Only Export Managers may run the POHODA export service."))
        allowed = self.env.user.company_ids
        if not self.env.su and any(c and c not in allowed for c in companies):
            raise AccessError(_(
                "POHODA export documents can only be maintained for companies "
                "you have access to."))

    @api.model
    def _service_create(self, vals_list):
        """Create per-document audit rows on behalf of the export service."""
        if isinstance(vals_list, dict):
            vals_list = [vals_list]
        Batch = self.env['barani.pohoda.export.batch']
        companies = [Batch.browse(v['batch_id']).company_id
                     for v in vals_list if v.get('batch_id')]
        self._check_service_caller(companies)
        return self.sudo().create(vals_list).with_env(self.env)

    def _service_write(self, vals):
        """Write per-document audit fields on behalf of the export service."""
        self._check_service_caller([line.company_id for line in self])
        return self.sudo().write(vals)

    def _service_unlink(self):
        """Remove lines while the batch is re-validated in Draft (audit-safe:
        rows of a batch beyond Draft are never deleted)."""
        if any(line.batch_id.state != 'draft' for line in self):
            raise UserError(_(
                "Documents of a batch that has progressed beyond Draft are audit "
                "records and cannot be removed."))
        self._check_service_caller([line.company_id for line in self])
        return self.sudo().unlink()
