# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.

from psycopg2 import sql

from odoo import fields, models


class PohodaDictionaryMixin(models.AbstractModel):
    """Shared base for every controlled POHODA code dictionary.

    Mapping-matrix cells reference these records (never free text). Only the
    ``code`` is ever emitted into the XML; ``name`` is an accountant-readable
    label shown in dropdowns. Concrete dictionaries also inherit ``mail.thread``
    so code changes (which affect XML output) are tracked.
    """

    _name = 'barani.pohoda.dictionary.mixin'
    _description = "BARANI POHODA Dictionary (mixin)"
    _order = 'code'

    code = fields.Char(
        required=True, tracking=True,
        help="POHODA code emitted verbatim into the exported XML.",
    )
    name = fields.Char(
        string="Label", required=True, tracking=True,
        help="Accountant-readable explanation. Shown in dropdowns; never exported.",
    )
    active = fields.Boolean(default=True, tracking=True)

    def name_get(self):
        result = []
        for rec in self:
            label = "%s — %s" % (rec.code, rec.name) if rec.name else (rec.code or "")
            result.append((rec.id, label))
        return result


class PohodaAccountAssignment(models.Model):
    _name = 'barani.pohoda.account.assignment'
    _inherit = ['barani.pohoda.dictionary.mixin', 'mail.thread']
    _description = "POHODA Account Assignment Code"

    company_id = fields.Many2one('res.company', string="Company")
    valid_for = fields.Char(
        string="Valid for",
        help="Optional applicability hint. Semantics finalised with the "
             "mapping-matrix validation (DOC 03).",
    )
    is_advance_account = fields.Boolean(
        string="Received-advance (324) předkontace", default=False, tracking=True,
        help="Mark the assignment(s) that post to the received-advances account "
             "(324 Prijaté preddavky). The export preflight requires advance (DPI) "
             "lines and settlement advance-deduction lines to map to a marked "
             "assignment (BLOCK_*_ACCOUNT_MAPPING_NOT_324). Marking the record — "
             "not matching the literal code — keeps the audit valid after the "
             "accountant remaps the POHODA předkontace code.",
    )

    _sql_constraints = [
        ('account_assignment_code_company_uniq', 'unique(code, company_id)',
         "A POHODA account assignment code must be unique per company."),
    ]

    def init(self):
        # Prevent duplicate GLOBAL codes (company_id IS NULL). Per-company
        # duplicates are already prevented by the (code, company_id) constraint.
        self.env.cr.execute(sql.SQL(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "barani_pohoda_account_assignment_global_code_uniq "
            "ON {table} (code) WHERE company_id IS NULL"
        ).format(table=sql.Identifier(self._table)))


class PohodaVatClassification(models.Model):
    _name = 'barani.pohoda.vat.classification'
    _inherit = ['barani.pohoda.dictionary.mixin', 'mail.thread']
    _description = "POHODA VAT Classification Code"

    valid_for_country = fields.Char(
        string="Valid for country / geography",
        help="Optional applicability hint (e.g. domestic / EU / foreign). "
             "Finalised with DOC 03 validation.",
    )
    valid_for_document = fields.Char(
        string="Valid for document kind",
        help="Optional applicability hint (e.g. invoice / credit note). "
             "Finalised with DOC 03 validation.",
    )

    _sql_constraints = [
        ('vat_classification_code_uniq', 'unique(code)',
         "A POHODA VAT classification code must be unique."),
    ]


class PohodaControlStatementCode(models.Model):
    _name = 'barani.pohoda.control.statement.code'
    _inherit = ['barani.pohoda.dictionary.mixin', 'mail.thread']
    _description = "POHODA Control Statement (KV) Code"

    valid_for_partner = fields.Char(
        string="Valid for partner type",
        help="Optional applicability hint. Finalised with DOC 03 validation.",
    )
    valid_for_document = fields.Char(
        string="Valid for document kind",
        help="Optional applicability hint. Finalised with DOC 03 validation.",
    )

    _sql_constraints = [
        ('control_statement_code_uniq', 'unique(code)',
         "A POHODA control statement code must be unique."),
    ]


class PohodaMossServiceType(models.Model):
    _name = 'barani.pohoda.moss.service.type'
    _inherit = ['barani.pohoda.dictionary.mixin', 'mail.thread']
    _description = "POHODA OSS / MOSS Service Type"

    valid_for_profile = fields.Char(
        string="Valid for fiscal profile",
        help="Optional applicability hint. Finalised with DOC 03 validation.",
    )

    _sql_constraints = [
        ('moss_service_type_code_uniq', 'unique(code)',
         "A POHODA OSS / MOSS service type code must be unique."),
    ]
