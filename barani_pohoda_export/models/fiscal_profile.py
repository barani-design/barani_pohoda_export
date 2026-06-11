# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.

from odoo import fields, models

from .constants import GEOGRAPHY_SELECTION, CUSTOMER_TAX_STATUS_SELECTION


class PohodaFiscalProfile(models.Model):
    """Matrix column: an export treatment that groups one or more Odoo fiscal
    positions sharing the same POHODA mapping."""

    _name = 'barani.pohoda.fiscal.profile'
    _description = "BARANI POHODA Fiscal Profile"
    _inherit = ['mail.thread']
    _order = 'sequence, id'

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)

    geography = fields.Selection(GEOGRAPHY_SELECTION, required=True, tracking=True)
    customer_tax_status = fields.Selection(
        CUSTOMER_TAX_STATUS_SELECTION, default='any', required=True, tracking=True)

    account_fiscal_position_ids = fields.Many2many(
        'account.fiscal.position', string="Fiscal positions",
        help="Odoo fiscal positions grouped under this export treatment.")
    country_group_id = fields.Many2one('res.country.group', string="Country group")
    is_oss = fields.Boolean(string="Is OSS", tracking=True)

    blocks_export_when_used = fields.Boolean(
        default=False, tracking=True,
        help="If set, any invoice resolving to this profile is blocked in preflight.")
    requires_accountant_approval = fields.Boolean(default=False, tracking=True)
    notes = fields.Text()
