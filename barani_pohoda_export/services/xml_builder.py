# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 04 — POHODA XML builder (Bucket B, Step 2).
#
# Builds a POHODA `dat:dataPack` import document for a set of classified, mapped
# `account.move` records. It is the structural heart of the export: one
# `dat:dataPackItem` / `inv:invoice` per move, with one `inv:invoiceItem` per
# exportable line (codes from the Step-1 rule resolver, amounts read verbatim off
# the Odoo line). A settlement's advance-deduction lines are emitted as ordinary
# (negative) `inv:invoiceItem`s — see the settlement model below.
#
# GROUNDED IN the official Stormware documentation (fetched 2026-06-09), NOT training:
#   * Schema      : stormware.cz/xml/schema/version_2/invoice.xsd
#   * Samples     : .../samples/version_2/import/Faktury/invoice_01_v2.0.xml (basic),
#                   invoice_08_v2.0.xml (linked advance deduction).
#   See POHODA_XML_RESEARCH_DOC04.md for the full reconciliation.
#
# Project decisions baked in here:
#   H2 — an Odoo down-payment invoice is a VAT-bearing document, so it is exported as
#        a regular POHODA `issuedInvoice` (Faktura) — NOT `issuedAdvanceInvoice`
#        (the non-tax "Zálohová faktura"). regular/advance/settlement => issuedInvoice;
#        only a credit note => issuedCreditNotice (test-gated).
#   SETTLEMENT MODEL (BARANI decision 2026-06-09; SUPERSEDES the earlier H1) — the RI
#        exports the *net-of-down-payments* base+VAT (the Odoo "Invoice total after
#        down payments"). Odoo already nets the advances on the RI via negative-324
#        lines, so every line (positive supply + negative advance deduction) is a
#        regular `inv:invoiceItem` and the document totals to the after-down-payments
#        figure. Each DPI is an INDEPENDENT POHODA document; they are linked only by
#        the shared Source/PF reference (emitted as `numberOrder`). NO
#        `inv:invoiceAdvancePaymentItem` / `sourceDocument` binding is produced — this
#        is what prevents double-counting base/VAT across the DPIs and the RI.
#
# IMPORT-TEST-GATED choices are isolated behind the module constants below (one-line
# changes once BARANI's POHODA import test / XSD set confirm them):
#   * RATE_VAT band mapping (Odoo % -> none/low/high/third) — Fork F-B.
#   * Whether to emit `inv:invoiceSummary` band totals vs let POHODA compute — F-D.
#   * `checkDuplicity` on numberRequested — F-D.
#   * MOSS service-type element shape (typ:MOSStype not shown in the samples).
#   * `numberOrder` as the home for the shared Source/PF reference.
#
# Read-only / pure: reads native fields + the matrix and the Step-1/Bucket-A services;
# writes nothing and sends nothing (Phase 1 has no automatic POHODA send).

from odoo import _, api, models
from odoo.exceptions import UserError

try:  # the manifest declares lxml in external_dependencies; guard import for safety.
    from lxml import etree
except ImportError:  # pragma: no cover
    etree = None

# ── POHODA v2 namespaces / envelope (confirmed: invoice.xsd, samples) ──────────
NS_DATA = 'http://www.stormware.cz/schema/version_2/data.xsd'
NS_INVOICE = 'http://www.stormware.cz/schema/version_2/invoice.xsd'
NS_TYPE = 'http://www.stormware.cz/schema/version_2/type.xsd'
NSMAP = {'dat': NS_DATA, 'inv': NS_INVOICE, 'typ': NS_TYPE}
PACK_VERSION = '2.0'

# invoiceType per classifier doc_kind (confirmed enum, invoice.xsd). H2: an Odoo
# VAT-bearing advance maps to a regular issuedInvoice.
INVOICE_TYPE_BY_KIND = {
    'regular_invoice': 'issuedInvoice',
    'advance_invoice': 'issuedInvoice',        # H2
    'settlement_invoice': 'issuedInvoice',
    'credit_note': 'issuedCreditNotice',       # test-gated
}

# Header title for an Odoo DPI exported under Architecture A v3 — so the POHODA
# document reads as a tax document to a received payment, not a generic invoice.
ADVANCE_DOC_TITLE = 'Down Payment Invoice / Daňový doklad k prijatej platbe'

# rateVAT band (typ:vatRateType). Fork F-B: the exact Odoo-rate -> band split for SK
# is import-test-gated. Conservative default: 0/exempt/reverse-charge -> 'none',
# anything positive -> 'high' (SK standard). Reduced rates (-> 'low'/'third') are
# configured against BARANI's POHODA after the import test.
RATE_VAT_NONE = 'none'
RATE_VAT_HIGH = 'high'
RATE_VAT_LOW = 'low'
RATE_VAT_THIRD = 'third'

# Fork F-D toggles (import-test-gated; safe defaults).
EMIT_INVOICE_SUMMARY = False   # False => let POHODA compute the document totals from the items
NUMBER_CHECK_DUPLICITY = False  # set True to add checkDuplicity="true" on numberRequested


class PohodaXmlBuilder(models.AbstractModel):
    _name = 'barani.pohoda.xml.builder'
    _description = "BARANI POHODA XML builder (DOC 04)"

    # ------------------------------------------------------------------ public
    @api.model
    def build(self, moves, test_mode=False):
        """Return POHODA `dataPack` XML (bytes) for ``moves``.

        ``test_mode=True`` permits building a credit note (`issuedCreditNotice`),
        which is otherwise Phase-1 test-gated. The builder assumes the moves have
        already passed preflight (posted, in scope, not blocked); it reads native
        fields + the seeded matrix and writes/sends nothing.
        """
        if etree is None:  # pragma: no cover
            raise UserError(_("The Python 'lxml' library is required to build POHODA XML."))
        moves = moves or self.env['account.move'].browse()
        config = self._config_for(moves)

        root = etree.Element(self._q(NS_DATA, 'dataPack'), nsmap=NSMAP)
        root.set('version', PACK_VERSION)
        root.set('id', 'BPE')
        root.set('application', 'BARANI POHODA Export')
        root.set('note', 'barani_pohoda_export')
        if config and config.pohoda_ico:
            root.set('ico', config.pohoda_ico)

        for move in moves:
            pack_item = etree.SubElement(root, self._q(NS_DATA, 'dataPackItem'))
            pack_item.set('version', PACK_VERSION)
            pack_item.set('id', self._pack_item_id(move))
            pack_item.append(self._build_invoice(move, config, test_mode=test_mode))

        encoding = (config.encoding if config and config.encoding else 'Windows-1250')
        return etree.tostring(root, xml_declaration=True, encoding=encoding,
                              pretty_print=True)

    # ----------------------------------------------------------------- invoice
    @api.model
    def _build_invoice(self, move, config, test_mode=False):
        classification = self.env['barani.pohoda.document.classifier'].classify(move)
        kind = classification.doc_kind

        if kind not in INVOICE_TYPE_BY_KIND:
            # blocked_mixed_324 / unsupported: a malformed move must not reach the
            # builder (preflight owns that). Refuse loudly rather than emit junk.
            raise UserError(_(
                "Cannot build POHODA XML for %(move)s: document kind '%(kind)s' is "
                "not exportable.", move=move.display_name, kind=kind))
        if (kind == 'credit_note' and not test_mode
                and (not config or config.advance_credit_note_policy != 'enabled')):
            raise UserError(_(
                "Credit note %(move)s is test-gated in Phase 1; build it only in test "
                "mode or after the credit-note flow is import-tested and enabled.",
                move=move.display_name))

        invoice_type = INVOICE_TYPE_BY_KIND[kind]
        invoice = etree.Element(self._q(NS_INVOICE, 'invoice'))
        invoice.set('version', PACK_VERSION)
        # inv:invoice is an xsd:sequence -> header, then detail, then summary.
        invoice.append(self._build_header(move, config, kind, invoice_type))
        invoice.append(self._build_detail(move, classification, kind))
        if EMIT_INVOICE_SUMMARY:
            invoice.append(self._build_summary(move))
        return invoice

    @api.model
    def _build_header(self, move, config, kind, invoice_type):
        # invoiceHeaderType is xsd:all (order-free); invoiceType + text are required.
        header = etree.Element(self._q(NS_INVOICE, 'invoiceHeader'))
        self._text(header, NS_INVOICE, 'invoiceType', invoice_type)

        # Requested document number (POHODA assigns from its own series if omitted).
        if move.name and move.name != '/':
            number = self._child(header, NS_INVOICE, 'number')
            requested = self._text(number, NS_TYPE, 'numberRequested', move.name)
            if NUMBER_CHECK_DUPLICITY:
                requested.set('checkDuplicity', 'true')

        symvar = self._symvar(move, config, kind)
        if symvar:
            self._text(header, NS_INVOICE, 'symVar', symvar)

        # The shared Source / PF / SO reference (Odoo invoice_origin, e.g. "Q2026357")
        # is what ties the down-payment invoices and their settlement together in the
        # BARANI model (in place of a POHODA advance binding). Carry it as the order
        # number for traceability. Field choice is flagged for the import test.
        if move.invoice_origin:
            self._text(header, NS_INVOICE, 'numberOrder', move.invoice_origin)

        # Only `date` is emitted; POHODA defaults dateTax / dateAccounting to it
        # (invoice.xsd). Per-source dates are added when the config requires them.
        if move.invoice_date:
            self._text(header, NS_INVOICE, 'date', move.invoice_date.isoformat())

        # Header VAT classification is a fallback only (DOC 04); the per-item
        # classificationVAT from the matrix is decisive.
        if config and config.document_header_vat_classification_id:
            cls_vat = self._child(header, NS_INVOICE, 'classificationVAT')
            self._text(cls_vat, NS_TYPE, 'ids',
                       config.document_header_vat_classification_id.code)

        # text is required by POHODA to create the document. For an Odoo DPI
        # (advance_invoice) emit a recognizable Daňový-doklad title (Architecture A v3)
        # so the document reads as a tax document to a received payment; append the
        # shared Source reference for traceability. Other kinds keep the doc's own ref.
        if kind == 'advance_invoice':
            header_text = ADVANCE_DOC_TITLE
            if move.invoice_origin:
                header_text = '%s / %s' % (header_text, move.invoice_origin)
        else:
            header_text = move.ref or move.invoice_origin or move.name or 'Faktúra'
        self._text(header, NS_INVOICE, 'text', header_text[:240])

        if (not config) or config.export_addresses:
            self._append_partner(header, move.partner_id)
        return header

    @api.model
    def _build_detail(self, move, classification, kind):
        detail = etree.Element(self._q(NS_INVOICE, 'invoiceDetail'))
        rule_result = self.env['barani.pohoda.rule.resolver'].resolve(
            move, classification=classification)

        # BARANI settlement model (decision 2026-06-09): the RI exports the
        # *net-of-down-payments* base+VAT — the values from the Odoo "Down payment
        # reconciliation" table's "Invoice total after down payments" line. Odoo
        # already nets the advances on the RI via negative-324 deduction lines, so
        # EVERY line (positive supply and negative advance deduction) is emitted as a
        # regular inv:invoiceItem and the document total equals the after-down-payments
        # total. Each down-payment invoice is an INDEPENDENT POHODA document (linked
        # only by the shared Source/PF reference, carried as numberOrder), so the
        # builder emits NO inv:invoiceAdvancePaymentItem and NO sourceDocument binding.
        # This prevents double-counting base/VAT across the DPIs and the RI.
        #
        # invoiceDetailType is an xsd:choice (occurrence order); the resolver orders
        # supply lines before deduction lines for a settlement.
        for line_res in rule_result.lines:
            detail.append(self._build_item(line_res))
        return detail

    # ------------------------------------------------------------------- items
    @api.model
    def _build_item(self, line_res):
        """A regular `inv:invoiceItem`.

        Handles supply lines, a DPI line (H2), and a settlement advance-deduction
        line (which is simply a negative line under the BARANI net-export model).
        Amounts (incl. negatives) are read verbatim off the Odoo line.
        """
        line = line_res.line
        item = etree.Element(self._q(NS_INVOICE, 'invoiceItem'))
        self._text(item, NS_INVOICE, 'text', (line.name or '')[:90])
        quantity = line.quantity or 1.0
        self._text(item, NS_INVOICE, 'quantity', self._fmt_qty(quantity))
        if line.product_uom_id:
            self._text(item, NS_INVOICE, 'unit', line.product_uom_id.name)
        self._text(item, NS_INVOICE, 'payVAT', 'false')  # prices below are net
        self._text(item, NS_INVOICE, 'rateVAT', self._rate_vat_band(line))

        base = line.price_subtotal
        vat = line.price_total - line.price_subtotal
        unit_net = base / quantity if quantity else base
        home = self._child(item, NS_INVOICE, 'homeCurrency')
        self._text(home, NS_TYPE, 'unitPrice', self._fmt_money(unit_net))
        self._text(home, NS_TYPE, 'price', self._fmt_money(base))
        self._text(home, NS_TYPE, 'priceVAT', self._fmt_money(vat))
        self._text(home, NS_TYPE, 'priceSum', self._fmt_money(line.price_total))

        self._append_item_codes(item, line_res)
        return item

    @api.model
    def _append_item_codes(self, item, line_res):
        """Emit the per-item POHODA codes resolved by the Step-1 rule resolver.

        invoiceItem carries per-item `accounting` (předkontace), `classificationVAT`,
        `classificationKVDPH` (SK) and `typeServiceMOSS` (invoice.xsd). Empty codes
        are omitted. A settlement deduction line carries the codes of its
        ``down_payment_deduction`` cell (resolved by the rule resolver).
        """
        if line_res.account_code:
            accounting = self._child(item, NS_INVOICE, 'accounting')
            self._text(accounting, NS_TYPE, 'ids', line_res.account_code)
        if line_res.vat_code:
            cls_vat = self._child(item, NS_INVOICE, 'classificationVAT')
            self._text(cls_vat, NS_TYPE, 'ids', line_res.vat_code)
        if line_res.kv_code:
            cls_kv = self._child(item, NS_INVOICE, 'classificationKVDPH')
            self._text(cls_kv, NS_TYPE, 'ids', line_res.kv_code)
        if line_res.moss_code:
            # typ:MOSStype shape is not shown in the public samples; emitted as a
            # ref (typ:ids) and flagged for the import test. OSS is out of the
            # canonical Phase-1 fixtures, so this path is exercised only for OSS.
            moss = self._child(item, NS_INVOICE, 'typeServiceMOSS')
            self._text(moss, NS_TYPE, 'ids', line_res.moss_code)

    @api.model
    def _build_summary(self, move):
        # Only built when EMIT_INVOICE_SUMMARY is on (F-D). Default path lets POHODA
        # compute the totals from the items, so this is a deliberately minimal stub
        # carrying the rounding mode; band totals are added after the import test.
        summary = etree.Element(self._q(NS_INVOICE, 'invoiceSummary'))
        self._text(summary, NS_INVOICE, 'roundingDocument', 'math2one')
        return summary

    # ------------------------------------------------------------- partner / VAT
    @api.model
    def _append_partner(self, header, partner):
        if not partner:
            return
        identity = self._child(header, NS_INVOICE, 'partnerIdentity')
        address = self._child(identity, NS_TYPE, 'address')
        # Field order follows the official sample (invoice_01): company, name, city,
        # street, zip, ico, dic. Best-effort mapping; confirm against the import test.
        if partner.is_company or partner.commercial_company_name:
            self._text(address, NS_TYPE, 'company',
                       partner.commercial_company_name or partner.name)
        self._text(address, NS_TYPE, 'name', partner.name or '')
        if partner.city:
            self._text(address, NS_TYPE, 'city', partner.city)
        if partner.street:
            self._text(address, NS_TYPE, 'street', partner.street)
        if partner.zip:
            self._text(address, NS_TYPE, 'zip', partner.zip)
        if partner.company_registry:
            self._text(address, NS_TYPE, 'ico', partner.company_registry)
        if partner.vat:
            self._text(address, NS_TYPE, 'dic', partner.vat)

    # ----------------------------------------------------------------- helpers
    @api.model
    def _config_for(self, moves):
        if not moves:
            return self.env['barani.pohoda.export.config'].browse()
        company = moves[:1].company_id
        return self.env['barani.pohoda.export.config'].search(
            [('company_id', '=', company.id), ('active', '=', True)], limit=1)

    @api.model
    def _symvar(self, move, config, kind):
        # Normal RI / settlement / credit note -> payment_reference.
        # Advance (DPI) -> dpi_symvar_policy (the customer paid using the source/PF ref).
        if kind == 'advance_invoice' and config:
            policy = config.dpi_symvar_policy
            if policy == 'source_order_or_pf_reference':
                return move.invoice_origin or ''
            if policy == 'blank_when_already_paid':
                return ''
            return move.payment_reference or ''   # odoo_payment_reference
        return move.payment_reference or ''

    @api.model
    def _rate_vat_band(self, line):
        """Map an Odoo line's effective tax rate to a POHODA rateVAT band (F-B)."""
        rate = self._effective_rate(line)
        if rate <= 0.0001:
            return RATE_VAT_NONE
        # Positive rate -> 'high' (SK standard) by default. Reduced-rate -> low/third
        # is configured after the import test; isolated here on purpose.
        return RATE_VAT_HIGH

    @api.model
    def _effective_rate(self, line):
        """Effective VAT % from the line's stored amounts (rate-agnostic, sign-safe)."""
        base = line.price_subtotal
        if not base:
            return 0.0
        return (line.price_total - base) / base * 100.0

    @api.model
    def _q(self, ns, tag):
        return '{%s}%s' % (ns, tag)

    @api.model
    def _child(self, parent, ns, tag):
        return etree.SubElement(parent, self._q(ns, tag))

    @api.model
    def _text(self, parent, ns, tag, text):
        element = etree.SubElement(parent, self._q(ns, tag))
        if text is not None:
            element.text = text
        return element

    @api.model
    def _fmt_money(self, value):
        # POHODA money: decimal point, 2 places. Normalise -0.00 to 0.00.
        amount = round(float(value or 0.0), 2) + 0.0
        return '%.2f' % amount

    @api.model
    def _fmt_qty(self, value):
        return ('%g' % float(value or 0.0))

    @api.model
    def _pack_item_id(self, move):
        # Stable, unique, auditable id for the dataPackItem (no whitespace).
        return 'BPE%s' % move.id
