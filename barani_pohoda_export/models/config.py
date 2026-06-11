# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.

from psycopg2 import sql

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from .constants import DATE_SOURCE_SELECTION


class PohodaExportConfig(models.Model):
    _name = 'barani.pohoda.export.config'
    _description = "BARANI POHODA Export Configuration"
    _inherit = ['mail.thread']
    _order = 'company_id, name'
    _check_company_auto = True

    # ── Identity ──────────────────────────────────────────────────────────
    name = fields.Char(required=True, default="Pohoda", tracking=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True,
        default=lambda self: self.env.company, tracking=True,
    )
    # required=True closes the partial-index NULL loophole (active is never NULL).
    active = fields.Boolean(default=True, required=True, tracking=True)

    # ── POHODA target / data pack ────────────────────────────────────────
    pohoda_ico = fields.Char(
        string="POHODA ICO", tracking=True,
        help="IČO of the target POHODA accounting unit (dataPack 'ico' attribute).",
    )
    pohoda_key = fields.Char(
        string="POHODA Key", tracking=True,
        help="Identifier of the target POHODA data file / accounting unit "
             "(dataPack 'key' attribute).",
    )
    data_pack_version = fields.Char(default="2.0")
    invoice_version = fields.Char(default="2.0")
    encoding = fields.Selection(
        [('Windows-1250', "Windows-1250"), ('UTF-8', "UTF-8")],
        default='Windows-1250', required=True, tracking=True,
        help="XML encoding. Keep Windows-1250 unless UTF-8 has been POHODA import-tested.",
    )

    # ── Document selection ───────────────────────────────────────────────
    journal_ids = fields.Many2many(
        'account.journal', string="Journals", check_company=True,
        domain="[('type', '=', 'sale'), ('company_id', '=', company_id)]",
        help="Sales journals whose customer invoices / credit notes are in scope. "
             "The form loads company_id (invisibly) so this domain filters correctly "
             "for single-company users too.",
    )
    key_date = fields.Selection(
        DATE_SOURCE_SELECTION, string="Key date source",
        default='invoice_date', required=True, tracking=True,
        help="Odoo date used as the primary POHODA document date.",
    )
    accounting_date_source = fields.Selection(
        DATE_SOURCE_SELECTION, string="Accounting date source",
        default='invoice_date', required=True, tracking=True,
        help="Odoo date used as the POHODA accounting / VAT date.",
    )

    # ── Geography / OSS ──────────────────────────────────────────────────
    country_group_eu_id = fields.Many2one(
        'res.country.group', string="EU country group", tracking=True,
        help="Country group representing the EU (without SK), used to drive "
             "EU / domestic / foreign treatment.",
    )
    oss_enabled = fields.Boolean(string="OSS enabled", default=False, tracking=True)

    # ── Header defaults ──────────────────────────────────────────────────
    export_addresses = fields.Boolean(default=True, tracking=True)
    evidentiary_resources = fields.Char(
        string="Evidentiary resources", default="A", tracking=True,
        help="POHODA evidence / records type used on the document header.",
    )
    document_header_vat_classification_id = fields.Many2one(
        'barani.pohoda.vat.classification', ondelete='restrict', tracking=True,
        string="Header VAT classification (fallback)",
        help="Fallback header VAT classification. Line-level mapping is decisive; "
             "this is used only where the schema requires a header value.",
    )

    # ── Advance / DPI behaviour ──────────────────────────────────────────
    send_advance_deduction_with_reference = fields.Boolean(
        string="Send advance deduction with reference", default=True, tracking=True,
        help="Advance deductions must always carry their source-document reference.",
    )
    advance_flow_mode = fields.Selection(
        [
            ('block_until_configured', "Block until configured"),
            ('odoo_zero_vat_dpi_as_pohoda_advance_invoice',
             "Odoo advance invoice -> POHODA issued advance invoice (legacy key)"),
            ('pohoda_native_pf_then_tax_document',
             "POHODA native PF / advance then tax document"),
            ('odoo_vat_bearing_dpi_blocked', "Legacy: VAT-bearing advance blocked (do not use after OQ-1 v2)"),
        ],
        default='block_until_configured', required=True, tracking=True,
        help="Bucket-B configuration placeholder. The classifier itself has one doc_kind, 'advance_invoice', and accepts VAT 0 or positive according to Odoo tax configuration. The legacy selection key 'odoo_zero_vat_dpi_as_pohoda_advance_invoice' is retained for migration compatibility until the config UI is renamed.",
    )
    dpi_symvar_policy = fields.Selection(
        [
            ('odoo_payment_reference', "Use Odoo payment_reference"),
            ('source_order_or_pf_reference', "Use source order / PF reference"),
            ('blank_when_already_paid', "Blank when already paid"),
        ],
        default='source_order_or_pf_reference', required=True, tracking=True,
    )
    advance_credit_note_policy = fields.Selection(
        [
            ('test_gated', "Test-gated"),
            ('blocked', "Blocked"),
            ('enabled', "Enabled (import-tested)"),
        ],
        default='test_gated', required=True, tracking=True,
        help="Credit-note (issuedCreditNotice) export is test-gated until POHODA "
             "import and accountant approval.",
    )

    # ── Phase / schema ───────────────────────────────────────────────────
    phase_1_payment_export = fields.Boolean(
        string="Phase 1 payment export", default=False, tracking=True,
        help="Must remain False in Phase 1: no payment import / export.",
    )
    xsd_schema_set_id = fields.Many2one(
        'ir.attachment', string="XSD schema set", ondelete='restrict',
        help="Optional uploaded POHODA XSD schema set used to validate generated XML "
             "before production import.",
    )

    # ── Constraints ──────────────────────────────────────────────────────
    @api.constrains('send_advance_deduction_with_reference')
    def _check_advance_reference_required(self):
        for cfg in self:
            if not cfg.send_advance_deduction_with_reference:
                raise ValidationError(_(
                    "Advance deductions must always be exported with their "
                    "source-document reference. This option cannot be disabled."
                ))

    @api.constrains('phase_1_payment_export')
    def _check_phase_1_payment_export(self):
        # Phase-1 guard: relax / remove when Phase 2 payment export is implemented.
        for cfg in self:
            if cfg.phase_1_payment_export:
                raise ValidationError(_(
                    "Payment import / export is not available in Phase 1. "
                    "Keep 'Phase 1 payment export' disabled."
                ))

    @api.constrains('active', 'company_id')
    def _check_single_active_config(self):
        # Friendly mirror of the DB partial unique index (the index is the real guard).
        for cfg in self:
            if cfg.active and self.search_count([
                ('id', '!=', cfg.id),
                ('company_id', '=', cfg.company_id.id),
                ('active', '=', True),
            ]):
                raise ValidationError(_(
                    "There is already an active POHODA export configuration for "
                    "this company. Archive it before activating another."
                ))

    # ── DB-level invariants ──────────────────────────────────────────────
    def init(self):
        # One ACTIVE configuration per company (the "accounting unit" invariant).
        # Partial unique index so archived configs do not block creating a new one.
        self.env.cr.execute(sql.SQL(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "barani_pohoda_export_config_active_company_uniq "
            "ON {table} (company_id) WHERE active"
        ).format(table=sql.Identifier(self._table)))
