# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .constants import MATCH_MODE_SELECTION, MATCH_MODE_PHASE1


class PohodaExportRule(models.Model):
    """Matrix row: a product / category matching rule. Rules are ordered;
    specific rules must win over broad ones (e.g. 'Služby' is residual only)."""

    _name = 'barani.pohoda.export.rule'
    _description = "BARANI POHODA Export Rule"
    _inherit = ['mail.thread']
    _order = 'config_id, sequence, id'

    config_id = fields.Many2one(
        'barani.pohoda.export.config', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(
        'res.company', related='config_id.company_id', store=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)

    match_mode = fields.Selection(
        selection=[m for m in MATCH_MODE_SELECTION if m[0] in MATCH_MODE_PHASE1],
        required=True, default='category', tracking=True,
        help="Phase 1 supports 'product' and 'category' matching. 'account' and "
             "'custom' are reserved and will be offered once their matching is built.")
    product_ids = fields.Many2many('product.product', string="Products")
    category_ids = fields.Many2many('product.category', string="Product categories")
    include_child_categories = fields.Boolean(
        default=False, tracking=True,
        help="Also match products in child categories. Parent categories may require "
             "review/approval (DOC 03 validation).")
    also_match_products = fields.Boolean(
        default=False,
        help="In a category rule, also match the explicitly listed products.")

    review_required = fields.Boolean(default=False, tracking=True)
    residual_only = fields.Boolean(
        default=False, tracking=True,
        help="Residual / general rule (e.g. 'Služby'); must not absorb lines that "
             "have a specific rule.")
    notes = fields.Text()

    mapping_cell_ids = fields.One2many(
        'barani.pohoda.export.rule.mapping.cell', 'rule_id', string="Mapping cells")

    @api.constrains('match_mode', 'product_ids', 'category_ids', 'active')
    def _check_match_targets(self):
        # Only enforce on active rules so a rule can be created and then configured.
        for rule in self:
            if not rule.active:
                continue
            if rule.match_mode == 'product' and not rule.product_ids:
                raise ValidationError(_("An active product rule must list at least one product."))
            if rule.match_mode == 'category' and not rule.category_ids:
                raise ValidationError(_("An active category rule must list at least one category."))
