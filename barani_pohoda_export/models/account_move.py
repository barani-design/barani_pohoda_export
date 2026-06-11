# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.

from odoo import fields, models

from .constants import BARANI_DOC_KIND_SELECTION, MOVE_EXPORT_STATE_SELECTION


class AccountMove(models.Model):
    """Adds POHODA export-tracking fields to account.move.

    These are tracking/audit fields ONLY. This extension deliberately does not
    override create/write/unlink and never touches accounting amounts, posted
    invoice lines, taxes, products, sequences, or reconciliation fields.
    account.move already inherits mail.thread (core), so tracking=True logs
    export-status changes on the invoice chatter.
    """

    _inherit = 'account.move'

    barani_pohoda_doc_kind = fields.Selection(
        BARANI_DOC_KIND_SELECTION, string="POHODA document kind",
        copy=False, tracking=True)
    barani_pohoda_export_state = fields.Selection(
        MOVE_EXPORT_STATE_SELECTION, string="POHODA export state",
        default='not_exported', copy=False, tracking=True)
    barani_pohoda_last_batch_id = fields.Many2one(
        'barani.pohoda.export.batch', string="Last POHODA batch",
        copy=False, check_company=True)
    barani_pohoda_last_success_batch_id = fields.Many2one(
        'barani.pohoda.export.batch', string="Last successful POHODA batch",
        copy=False, check_company=True)
    barani_pohoda_exported_at = fields.Datetime(string="POHODA exported at", copy=False)
    barani_pohoda_pohoda_record_id = fields.Char(string="POHODA record ID", copy=False)
    barani_pohoda_export_hash = fields.Char(string="POHODA export hash", copy=False)
    barani_pohoda_last_error = fields.Text(string="POHODA last error", copy=False)
