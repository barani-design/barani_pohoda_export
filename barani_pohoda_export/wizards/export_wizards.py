# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 05 / Bucket C Step 3 — the wizard layer over the batch lifecycle service.
#
# * Export wizard:          date range -> create + validate a batch in one step.
# * Response import wizard: upload the POHODA response file -> action_apply_response.
# * Re-export wizard:       DOC 05 edge 14 — a manager authorizes re-export of an
#   ACCEPTED document with a recorded reason, after an idempotency-hash check that
#   tells them whether the document changed since POHODA accepted it (unchanged =>
#   re-import would duplicate the document in POHODA).
#
# All three are Export-Manager-only (ACL + the service's _check_manager re-check);
# every state transition still goes through barani.pohoda.export.service.

import base64
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import html_escape

from ..models import constants as C


class PohodaExportWizard(models.TransientModel):
    _name = 'barani.pohoda.export.wizard'
    _description = "POHODA export wizard (create + validate a batch)"

    @api.model
    def _default_config(self):
        return self.env['barani.pohoda.export.config'].search(
            [('active', '=', True), ('company_id', '=', self.env.company.id)], limit=1)

    @api.model
    def _default_end(self):
        # Last day of the previous month.
        return fields.Date.context_today(self).replace(day=1) - timedelta(days=1)

    @api.model
    def _default_start(self):
        # First day of the previous month.
        return self._default_end().replace(day=1)

    @api.model
    def _default_date_field(self):
        config = self._default_config()
        return config.key_date if config else 'invoice_date'

    config_id = fields.Many2one(
        'barani.pohoda.export.config', string="Configuration", required=True,
        default=_default_config)
    start_date = fields.Date(required=True, default=_default_start)
    end_date = fields.Date(required=True, default=_default_end)
    date_field = fields.Selection(
        C.DATE_SOURCE_SELECTION, string="Date field", required=True,
        default=_default_date_field)

    @api.onchange('config_id')
    def _onchange_config_id(self):
        if self.config_id:
            self.date_field = self.config_id.key_date

    def action_create_batch(self):
        """Create the batch for the chosen scope and validate it immediately."""
        self.ensure_one()
        batch = self.env['barani.pohoda.export.batch'].create({
            'config_id': self.config_id.id,
            'company_id': self.config_id.company_id.id,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'date_field': self.date_field,
        })
        batch.action_validate()
        return {
            'type': 'ir.actions.act_window',
            'name': _("POHODA Export Batch"),
            'res_model': 'barani.pohoda.export.batch',
            'res_id': batch.id,
            'view_mode': 'form',
            'target': 'current',
        }


class PohodaResponseImportWizard(models.TransientModel):
    _name = 'barani.pohoda.response.import.wizard'
    _description = "POHODA response import wizard"

    batch_id = fields.Many2one(
        'barani.pohoda.export.batch', string="Batch", required=True,
        domain=[('state', '=', 'sent')])
    response_file = fields.Binary(string="POHODA response XML", required=True)
    response_filename = fields.Char()

    def action_import(self):
        self.ensure_one()
        response = base64.b64decode(self.response_file)
        self.env['barani.pohoda.export.service'].action_apply_response(
            self.batch_id, response)
        return {
            'type': 'ir.actions.act_window',
            'name': _("POHODA Export Batch"),
            'res_model': 'barani.pohoda.export.batch',
            'res_id': self.batch_id.id,
            'view_mode': 'form',
            'target': 'current',
        }


class PohodaReexportWizard(models.TransientModel):
    _name = 'barani.pohoda.reexport.wizard'
    _description = "POHODA re-export authorization (DOC 05 edge 14)"

    move_ids = fields.Many2many(
        'account.move', string="Documents", required=True)
    reason = fields.Text(
        string="Re-export reason", required=True,
        help="Recorded in each document's chatter together with the "
             "idempotency-hash check result.")
    summary = fields.Text(compute='_compute_summary')
    any_unchanged = fields.Boolean(compute='_compute_summary')

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        if 'move_ids' in fields_list and self.env.context.get('active_model') == 'account.move':
            vals['move_ids'] = [(6, 0, self.env.context.get('active_ids', []))]
        return vals

    @api.depends('move_ids')
    def _compute_summary(self):
        Service = self.env['barani.pohoda.export.service']
        for wiz in self:
            lines = []
            any_unchanged = False
            for move in wiz.move_ids:
                if move.barani_pohoda_export_state != 'accepted':
                    lines.append(_(
                        "%s — state '%s': not an accepted export, will be skipped."
                    ) % (move.name or move.id,
                         move.barani_pohoda_export_state or 'not_exported'))
                    continue
                unchanged = (move.barani_pohoda_export_hash
                             and Service._source_hash(move) == move.barani_pohoda_export_hash)
                if unchanged:
                    any_unchanged = True
                    lines.append(_(
                        "%s — accepted, content UNCHANGED since the accepted export: "
                        "re-importing will create a DUPLICATE document in POHODA."
                    ) % (move.name or move.id))
                else:
                    lines.append(_(
                        "%s — accepted, content changed since the accepted export "
                        "(corrected document)."
                    ) % (move.name or move.id))
            wiz.summary = "\n".join(lines)
            wiz.any_unchanged = any_unchanged

    def action_authorize(self):
        """Clear the already-exported gate for the accepted documents, with a reason."""
        self.ensure_one()
        Service = self.env['barani.pohoda.export.service']
        Service._check_manager()
        if not (self.reason or '').strip():
            raise UserError(_("A re-export reason is required."))
        accepted = self.move_ids.filtered(
            lambda m: m.barani_pohoda_export_state == 'accepted')
        if not accepted:
            raise UserError(_(
                "None of the selected documents is in the 'accepted' POHODA export "
                "state; there is nothing to authorize."))
        for move in accepted:
            unchanged = (move.barani_pohoda_export_hash
                         and Service._source_hash(move) == move.barani_pohoda_export_hash)
            body = (
                "%s<br/><b>%s</b> %s<br/>%s %s" % (
                    html_escape(_("POHODA re-export authorized.")),
                    html_escape(_("Reason:")), html_escape(self.reason.strip()),
                    html_escape(_("Idempotency check:")),
                    html_escape(_("content UNCHANGED since the accepted export "
                                  "(re-import will duplicate the document in POHODA).")
                                if unchanged else
                                _("content changed since the accepted export.")),
                ))
            # Chatter via scoped sudo (an Export Manager may lack accounting write
            # rights); authorship stays with the acting user.
            move.sudo().message_post(
                body=body, author_id=self.env.user.partner_id.id)
            Service._write_move_mirror(move, {
                'barani_pohoda_export_state': 'not_exported',
                'barani_pohoda_last_error': False,
            })
        return {'type': 'ir.actions.act_window_close'}
