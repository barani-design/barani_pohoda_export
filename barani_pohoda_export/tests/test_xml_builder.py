# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 04 acceptance tests for the POHODA XML builder (Bucket B, Step 2).
#
# These build real dataPack XML for the canonical document shapes and assert the
# structure + the *stored* Odoo amounts (OQ-1 v2 rule C: emit, don't re-derive),
# parsing the output with namespace-aware lxml. They reuse the Step-1 matrix
# fixtures (active rules + cells + a profile bound to a throwaway fiscal position)
# plus the Bucket-A source-resolver pattern (a shared sale.order.line links a DPI to
# the settlement deduction). Settlement moves are POSTED so `sourceDocument` /
# `numberRequested` assert against real POHODA-bound document numbers; the simpler
# cases stay in draft (the builder is posting-agnostic for everything but the number).

from lxml import etree

from odoo import fields
from odoo.tests import TransactionCase, tagged
from odoo.exceptions import UserError

NS = {
    'dat': 'http://www.stormware.cz/schema/version_2/data.xsd',
    'inv': 'http://www.stormware.cz/schema/version_2/invoice.xsd',
    'typ': 'http://www.stormware.cz/schema/version_2/type.xsd',
}


@tagged('post_install', '-at_install')
class TestXmlBuilder(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env.ref('barani_pohoda_export.config_pohoda')
        cls.company = cls.config.company_id
        # Give the config a target IČO so the dataPack carries one.
        cls.config.pohoda_ico = '12345678'
        cls.partner = cls.env['res.partner'].create({
            'name': 'BPE XB Customer', 'is_company': True,
            'city': 'Bratislava', 'street': 'Test 1', 'zip': '81101',
        })
        cls.builder = cls.env['barani.pohoda.xml.builder']
        cls.classifier = cls.env['barani.pohoda.document.classifier']

        Account = cls.env['account.account']
        cls.income_account = Account.search(
            [('account_type', '=', 'income'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.income_account:
            cls.income_account = Account.create({
                'name': 'BPE XB Income', 'code': 'BPEXBI',
                'account_type': 'income', 'company_id': cls.company.id})
        cls.advance_account = Account.search(
            [('code', '=like', '324%'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.advance_account:
            cls.advance_account = Account.create({
                'name': 'BPE XB Advances', 'code': '324999',
                'account_type': 'liability_current', 'company_id': cls.company.id})

        Journal = cls.env['account.journal']
        cls.sale_journal = Journal.search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.sale_journal:
            cls.sale_journal = Journal.create({
                'name': 'BPE XB Sales', 'code': 'BPEXBS', 'type': 'sale',
                'company_id': cls.company.id})

        Tax = cls.env['account.tax']
        cls.tax_vat = Tax.search(
            [('type_tax_use', '=', 'sale'), ('amount', '>', 0),
             ('price_include', '=', False), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.tax_vat:
            cls.tax_vat = Tax.create({
                'name': 'BPE XB VAT', 'amount': 23.0, 'amount_type': 'percent',
                'type_tax_use': 'sale', 'price_include': False, 'company_id': cls.company.id})

        cls.pricelist = cls.env['product.pricelist'].search([], limit=1)
        if not cls.pricelist:
            cls.pricelist = cls.env['product.pricelist'].create({'name': 'BPE XB Pricelist'})

        # Fiscal position + profile (column).
        cls.fpos = cls.env['account.fiscal.position'].create(
            {'name': 'BPE XB FPos', 'company_id': cls.company.id})
        cls.profile = cls.env['barani.pohoda.fiscal.profile'].create({
            'name': 'BPE XB Domestic', 'geography': 'domestic', 'customer_tax_status': 'any',
            'is_oss': False, 'account_fiscal_position_ids': [(6, 0, [cls.fpos.id])]})

        def ref(xmlid):
            return cls.env.ref('barani_pohoda_export.%s' % xmlid)
        cls.aa1, cls.aa2 = ref('aa_1'), ref('aa_2')
        cls.vat_ud, cls.vat_un = ref('vat_ud'), ref('vat_un')
        cls.kv_d2, cls.kv_kn = ref('kv_d2'), ref('kv_kn')

        Rule = cls.env['barani.pohoda.export.rule']
        Cell = cls.env['barani.pohoda.export.rule.mapping.cell']

        # Down-payment rule (product) + cell (account_assignment aa1, downpayment aa2).
        cls.dp_product = cls.env['product.product'].create(
            {'name': 'BPE XB Down payment', 'type': 'service'})
        cls.rule_dp = Rule.create({
            'config_id': cls.config.id, 'sequence': 1, 'name': 'XB Odpocet zalohy',
            'match_mode': 'product', 'product_ids': [(6, 0, [cls.dp_product.id])], 'active': True})
        Cell.create({
            'rule_id': cls.rule_dp.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'invoice', 'enabled_state': 'active',
            'account_assignment_id': cls.aa1.id, 'downpayment_account_assignment_id': cls.aa2.id,
            'vat_classification_id': cls.vat_ud.id, 'control_statement_code_id': cls.kv_d2.id})
        # Settlement deductions resolve through the explicit down_payment_deduction cell
        # (Bucket B Step 1 audit). Distinct codes (aa2 / UN / KN) prove document_kind
        # drives the lookup and that the deduction item carries the deduction cell's codes.
        cls.cell_dp_ded = Cell.create({
            'rule_id': cls.rule_dp.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'down_payment_deduction', 'enabled_state': 'active',
            'account_assignment_id': cls.aa2.id, 'downpayment_account_assignment_id': cls.aa2.id,
            'vat_classification_id': cls.vat_un.id, 'control_statement_code_id': cls.kv_kn.id})

        # Goods rule (category) + cell (account_assignment aa1).
        cls.cat_goods = cls.env['product.category'].create({'name': 'BPE XB Goods'})
        cls.goods_product = cls.env['product.product'].create(
            {'name': 'BPE XB Widget', 'type': 'consu', 'categ_id': cls.cat_goods.id})
        cls.rule_goods = Rule.create({
            'config_id': cls.config.id, 'sequence': 4, 'name': 'XB Vyrobky',
            'match_mode': 'category', 'category_ids': [(6, 0, [cls.cat_goods.id])], 'active': True})
        Cell.create({
            'rule_id': cls.rule_goods.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'invoice', 'enabled_state': 'active',
            'account_assignment_id': cls.aa1.id, 'downpayment_account_assignment_id': cls.aa1.id,
            'vat_classification_id': cls.vat_ud.id, 'control_statement_code_id': cls.kv_d2.id})

    # --------------------------------------------------------------- helpers
    def _so_line(self, price=5.0):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'pricelist_id': self.pricelist.id,
            'order_line': [(0, 0, {
                'product_id': self.dp_product.id, 'product_uom_qty': 1.0, 'price_unit': price})]})
        return order.order_line[:1]

    def _move(self, lines, move_type='out_invoice', fpos=None):
        commands = []
        for spec in lines:
            sale_lines = spec.get('sale_lines')
            commands.append((0, 0, {
                'name': spec.get('name', 'line'),
                'product_id': spec['product'].id,
                'account_id': spec['account'].id,
                'price_unit': spec['price_unit'],
                'quantity': spec.get('quantity', 1.0),
                'tax_ids': [(6, 0, [t.id for t in spec.get('taxes', [])])],
                'sale_line_ids': [(6, 0, sale_lines.ids if sale_lines else [])],
            }))
        vals = {
            'move_type': move_type, 'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(), 'journal_id': self.sale_journal.id,
            'invoice_line_ids': commands,
        }
        if fpos is not None:
            vals['fiscal_position_id'] = fpos.id
        move = self.env['account.move'].create(vals)
        product_lines = move.invoice_line_ids.filtered(
            lambda l: not l.display_type or l.display_type == 'product')
        for line, spec in zip(product_lines, lines):
            pin = {}
            if line.account_id.id != spec['account'].id:
                pin['account_id'] = spec['account'].id
            if line.price_unit != spec['price_unit']:
                pin['price_unit'] = spec['price_unit']
            if pin:
                line.write(pin)
        return move

    def _parse(self, xml_bytes):
        self.assertIsInstance(xml_bytes, bytes)
        return etree.fromstring(xml_bytes)

    # ----------------------------------------------------------------- tests
    def test_builds_well_formed_datapack(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0, 'taxes': [self.tax_vat]}], fpos=self.fpos)
        root = self._parse(self.builder.build(move))
        self.assertEqual(etree.QName(root).localname, 'dataPack')
        self.assertEqual(root.get('version'), '2.0')
        self.assertEqual(root.get('ico'), '12345678')
        items = root.findall('dat:dataPackItem', NS)
        self.assertEqual(len(items), 1)
        invoices = root.findall('.//inv:invoice', NS)
        self.assertEqual(len(invoices), 1)
        self.assertEqual(root.findall('.//inv:invoiceType', NS)[0].text, 'issuedInvoice')

    def test_regular_invoice_item_codes_and_amounts(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0, 'taxes': [self.tax_vat]}], fpos=self.fpos)
        line = move.invoice_line_ids[:1]
        root = self._parse(self.builder.build(move))

        item_els = root.findall('.//inv:invoiceItem', NS)
        self.assertEqual(len(item_els), 1)
        item = item_els[0]
        # Per-item codes resolved by the Step-1 rule resolver.
        self.assertEqual(item.findall('inv:accounting/typ:ids', NS)[0].text, '1')
        self.assertEqual(item.findall('inv:classificationVAT/typ:ids', NS)[0].text, 'UD')
        self.assertEqual(item.findall('inv:classificationKVDPH/typ:ids', NS)[0].text, 'D2')
        # Amounts emitted verbatim from the stored line (not re-derived).
        price = float(item.findall('inv:homeCurrency/typ:price', NS)[0].text)
        price_vat = float(item.findall('inv:homeCurrency/typ:priceVAT', NS)[0].text)
        price_sum = float(item.findall('inv:homeCurrency/typ:priceSum', NS)[0].text)
        self.assertAlmostEqual(price, line.price_subtotal, places=2)
        self.assertAlmostEqual(price_vat, line.price_total - line.price_subtotal, places=2)
        self.assertAlmostEqual(price_sum, line.price_total, places=2)

    def test_advance_invoice_exported_as_issued_invoice(self):
        # H2: an Odoo VAT-bearing DPI is a regular POHODA invoice, not an advance invoice.
        move = self._move(
            [{'account': self.advance_account, 'product': self.dp_product,
              'price_unit': 5.0, 'taxes': [self.tax_vat], 'name': 'Down payment'}], fpos=self.fpos)
        self.assertEqual(self.classifier.classify(move).doc_kind, 'advance_invoice')
        root = self._parse(self.builder.build(move))
        self.assertEqual(root.findall('.//inv:invoiceType', NS)[0].text, 'issuedInvoice')
        # The advance line is a normal invoiceItem; there is NO advance-payment item.
        self.assertEqual(len(root.findall('.//inv:invoiceItem', NS)), 1)
        self.assertEqual(len(root.findall('.//inv:invoiceAdvancePaymentItem', NS)), 0)

    def test_settlement_emits_negative_deduction_item_not_advance_payment(self):
        # BARANI net-export model: VAT-bearing DPI + same-rate deduction. The RI
        # exports the supply (+) and a NEGATIVE invoiceItem for the advance deduction
        # — NO invoiceAdvancePaymentItem and NO source binding — and the document
        # totals to the "Invoice total after down payments" figure (no double count).
        settlement = self._move([
            {'account': self.income_account, 'product': self.goods_product, 'price_unit': 20.0,
             'taxes': [self.tax_vat], 'name': 'Supply'},
            {'account': self.advance_account, 'product': self.dp_product, 'price_unit': -5.0,
             'taxes': [self.tax_vat], 'name': 'Advance deduction'},
        ], fpos=self.fpos)
        self.assertEqual(self.classifier.classify(settlement).doc_kind, 'settlement_invoice')

        root = self._parse(self.builder.build(settlement))
        self.assertEqual(root.findall('.//inv:invoiceType', NS)[0].text, 'issuedInvoice')
        self.assertEqual(len(root.findall('.//inv:invoiceAdvancePaymentItem', NS)), 0)
        items = root.findall('.//inv:invoiceItem', NS)
        self.assertEqual(len(items), 2)  # supply (+) and advance deduction (-)

        def price(it):
            return float(it.findall('inv:homeCurrency/typ:price', NS)[0].text)
        def price_sum(it):
            return float(it.findall('inv:homeCurrency/typ:priceSum', NS)[0].text)
        deductions = [it for it in items if price(it) < 0.0]
        self.assertEqual(len(deductions), 1)
        ded = deductions[0]
        # Negative, VAT-bearing deduction (its VAT reverses too).
        self.assertLess(float(ded.findall('inv:homeCurrency/typ:priceVAT', NS)[0].text), 0.0)
        self.assertEqual(ded.findall('inv:rateVAT', NS)[0].text, 'high')
        # Codes come from the explicit down_payment_deduction cell (aa2 / UN / KN).
        self.assertEqual(ded.findall('inv:accounting/typ:ids', NS)[0].text, '2')
        self.assertEqual(ded.findall('inv:classificationVAT/typ:ids', NS)[0].text, 'UN')
        self.assertEqual(ded.findall('inv:classificationKVDPH/typ:ids', NS)[0].text, 'KN')
        # No double counting: document total == Odoo's net-of-down-payments total.
        self.assertAlmostEqual(sum(price_sum(it) for it in items),
                               settlement.amount_total, places=2)

    def test_settlement_zero_vat_deduction(self):
        # Zero-rated advance (export / EU B2B): DPI + deduction carry no VAT, the
        # supply does. The deduction is a negative invoiceItem reversing base only.
        settlement = self._move([
            {'account': self.income_account, 'product': self.goods_product, 'price_unit': 20.0,
             'taxes': [self.tax_vat], 'name': 'Supply'},
            {'account': self.advance_account, 'product': self.dp_product, 'price_unit': -5.0,
             'taxes': [], 'name': 'Advance deduction'},
        ], fpos=self.fpos)
        root = self._parse(self.builder.build(settlement))
        self.assertEqual(len(root.findall('.//inv:invoiceAdvancePaymentItem', NS)), 0)
        items = root.findall('.//inv:invoiceItem', NS)

        def price(it):
            return float(it.findall('inv:homeCurrency/typ:price', NS)[0].text)
        ded = [it for it in items if price(it) < 0.0][0]
        self.assertEqual(ded.findall('inv:rateVAT', NS)[0].text, 'none')
        self.assertAlmostEqual(
            float(ded.findall('inv:homeCurrency/typ:priceVAT', NS)[0].text), 0.0, places=2)

    def test_number_order_carries_shared_source_reference(self):
        # The shared Source/PF reference (Odoo invoice_origin) is emitted as
        # numberOrder — the link between the DPIs and their settlement.
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0, 'taxes': [self.tax_vat]}], fpos=self.fpos)
        move.invoice_origin = 'Q2026357'
        root = self._parse(self.builder.build(move))
        self.assertEqual(root.findall('.//inv:numberOrder', NS)[0].text, 'Q2026357')

    def test_rate_vat_band_zero_vs_positive(self):
        taxed = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0, 'taxes': [self.tax_vat]}], fpos=self.fpos)
        root = self._parse(self.builder.build(taxed))
        self.assertEqual(root.findall('.//inv:invoiceItem/inv:rateVAT', NS)[0].text, 'high')

        untaxed = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0, 'taxes': []}], fpos=self.fpos)
        root2 = self._parse(self.builder.build(untaxed))
        self.assertEqual(root2.findall('.//inv:invoiceItem/inv:rateVAT', NS)[0].text, 'none')

    def test_credit_note_refused_outside_test_mode(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0, 'taxes': [self.tax_vat]}],
            move_type='out_refund', fpos=self.fpos)
        self.assertEqual(self.classifier.classify(move).doc_kind, 'credit_note')
        with self.assertRaises(UserError):
            self.builder.build(move)
        # In test mode it builds, as a POHODA credit note.
        root = self._parse(self.builder.build(move, test_mode=True))
        self.assertEqual(root.findall('.//inv:invoiceType', NS)[0].text, 'issuedCreditNotice')
