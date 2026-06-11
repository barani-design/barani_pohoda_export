# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 02 acceptance tests for the settlement advance-source resolver.
#
# The native source link (account.move.line.sale_line_ids -> invoice_lines ->
# move_id) is built deterministically here: a single sale.order.line is shared by
# the down-payment invoice's 324 line and the settlement's negative-324 deduction.
# That isolates the resolver's logic (link-walking, source classification, blocker
# selection) from the version-specific down-payment wizard, which is exercised
# end-to-end by the live trial fixture instead. Invoices are left in draft: the
# resolver, like the classifier, reads native fields and is posting-agnostic.

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models import constants as C


@tagged('post_install', '-at_install')
class TestSourceResolver(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'BPE Test Customer'})
        cls.classifier = cls.env['barani.pohoda.document.classifier']
        cls.resolver = cls.env['barani.pohoda.source.resolver']

        Account = cls.env['account.account']
        cls.income_account = Account.search(
            [('account_type', '=', 'income'), ('company_id', '=', cls.company.id)],
            limit=1)
        if not cls.income_account:
            cls.income_account = Account.create({
                'name': 'BPE Test Income', 'code': 'BPEINC',
                'account_type': 'income', 'company_id': cls.company.id,
            })
        # Received-advance account: the classifier identifies advances by '324' prefix.
        cls.advance_account = Account.search(
            [('code', '=like', '324%'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.advance_account:
            cls.advance_account = Account.create({
                'name': 'BPE Test Advances Received', 'code': '324999',
                'account_type': 'liability_current', 'company_id': cls.company.id,
            })

        # Reuse an existing NON-price-included positive sale tax: the build DB ships one
        # with working repartition lines/accounts, so VAT actually computes on draft
        # lines (creating a bare tax here risks computing no VAT). A minimal tax is made
        # only as a fallback. The rate/gross assertions below are rate-agnostic (derived
        # from the line amounts), so they hold whatever the reused tax's rate is.
        Tax = cls.env['account.tax']
        cls.tax_vat = Tax.search(
            [('type_tax_use', '=', 'sale'), ('amount', '>', 0),
             ('price_include', '=', False), ('company_id', '=', cls.company.id)],
            limit=1)
        if not cls.tax_vat:
            cls.tax_vat = Tax.create({
                'name': 'BPE Test VAT', 'amount': 23.0, 'amount_type': 'percent',
                'type_tax_use': 'sale', 'price_include': False,
                'company_id': cls.company.id,
            })

        Journal = cls.env['account.journal']
        cls.sale_journal = Journal.search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.sale_journal:
            cls.sale_journal = Journal.create({
                'name': 'BPE Test Sales', 'code': 'BPESJ', 'type': 'sale',
                'company_id': cls.company.id,
            })

        cls.product = cls.env['product.product'].create(
            {'name': 'BPE Test Product', 'type': 'service'})
        cls.pricelist = cls.env['product.pricelist'].search([], limit=1)
        if not cls.pricelist:
            cls.pricelist = cls.env['product.pricelist'].create(
                {'name': 'BPE Test Pricelist'})

    # ------------------------------------------------------------------ helpers
    def _so_line(self, price=10.0):
        """Create a minimal sale order and return its single order line."""
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'pricelist_id': self.pricelist.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id,
                'product_uom_qty': 1.0,
                'price_unit': price,
            })],
        })
        return order.order_line[:1]

    def _invoice(self, lines, move_type='out_invoice'):
        """Create a DRAFT move. Each ``lines`` spec: account / price_unit /
        (quantity) / (taxes) / (sale_lines = a sale.order.line recordset)."""
        commands = []
        for spec in lines:
            sale_lines = spec.get('sale_lines')
            commands.append((0, 0, {
                'name': spec.get('name', 'line'),
                'account_id': spec['account'].id,
                'price_unit': spec['price_unit'],
                'quantity': spec.get('quantity', 1.0),
                'tax_ids': [(6, 0, [t.id for t in spec.get('taxes', [])])],
                'sale_line_ids': [(6, 0, sale_lines.ids if sale_lines else [])],
            }))
        return self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(),
            'journal_id': self.sale_journal.id,
            'invoice_line_ids': commands,
        })

    def _advance(self, so_line, amount=5.0, taxes=None):
        """An advance invoice: one positive 324 line linked to ``so_line``.

        ``taxes`` None/[] => zero-VAT advance; pass [self.tax_vat] for a VAT-bearing one.
        """
        return self._invoice([{
            'account': self.advance_account, 'price_unit': amount,
            'taxes': taxes or [], 'sale_lines': so_line, 'name': 'Down payment',
        }])

    def _settlement(self, deductions):
        """Ordinary supply line + one negative-324 deduction per ``deductions`` entry.

        Each entry is ``(amount, so_line)`` or ``(amount, so_line, taxes)``; the optional
        third element is the deduction line's tax list (default no tax = zero-VAT).
        """
        lines = [{
            'account': self.income_account, 'price_unit': 20.0,
            'taxes': [self.tax_vat], 'name': 'Supply',
        }]
        for entry in deductions:
            amount, so_line = entry[0], entry[1]
            taxes = entry[2] if len(entry) > 2 else []
            lines.append({
                'account': self.advance_account, 'price_unit': -amount,
                'taxes': taxes, 'sale_lines': so_line, 'name': 'Advance deduction',
            })
        return self._invoice(lines)

    # -------------------------------------------------------------------- tests
    def test_resolves_single_zero_vat_advance(self):
        so_line = self._so_line()
        dpi = self._advance(so_line, amount=5.0)
        self.assertEqual(self.classifier.classify(dpi).doc_kind, 'advance_invoice')

        settlement = self._settlement([(5.0, so_line)])
        result = self.resolver.resolve(settlement)

        self.assertTrue(result.is_settlement)
        self.assertFalse(result.is_blocked)
        self.assertEqual(len(result.advances), 1)
        advance = result.advances[0]
        self.assertTrue(advance.is_resolved)
        self.assertEqual(advance.source_move.ids, dpi.ids)
        self.assertAlmostEqual(advance.amount, 5.0, places=2)  # zero-VAT: gross == base
        self.assertEqual(result.source_moves.ids, dpi.ids)

    def test_source_not_found_without_native_link(self):
        # Deduction with no sale_line_ids link at all.
        settlement = self._settlement([(5.0, self.env['sale.order.line'])])
        result = self.resolver.resolve(settlement)

        self.assertTrue(result.is_blocked)
        self.assertIn(C.BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_FOUND, result.blockers)
        self.assertFalse(result.advances[0].is_resolved)
        self.assertFalse(result.advances[0].source_move)
        self.assertFalse(result.source_moves)

    def test_resolves_vat_inclusive_advance_matching_rate(self):
        # OQ-1 v2 happy path: a VAT-bearing advance settled by a deduction at the SAME
        # rate resolves cleanly. amount is the gross applied amount (abs price_total).
        so_line = self._so_line()
        adv = self._advance(so_line, amount=5.0, taxes=[self.tax_vat])
        self.assertEqual(self.classifier.classify(adv).doc_kind, 'advance_invoice')

        settlement = self._settlement([(5.0, so_line, [self.tax_vat])])
        result = self.resolver.resolve(settlement)

        self.assertFalse(result.is_blocked)
        advance = result.advances[0]
        self.assertTrue(advance.is_resolved)
        self.assertEqual(advance.source_move.ids, adv.ids)
        # Rate-agnostic: amount is the GROSS applied amount (abs price_total), strictly
        # above the base, and the deduction reverses the advance at the SAME rate.
        ded = advance.deduction_line
        self.assertAlmostEqual(advance.amount, abs(ded.price_total), places=2)
        self.assertGreater(advance.amount, abs(ded.price_subtotal))   # gross > base => VAT-bearing
        self.assertGreater(advance.source_rate, 0.0)
        self.assertAlmostEqual(advance.deduction_rate, advance.source_rate, places=4)

    def test_vat_rate_mismatch_blocks(self):
        # The new no-double-count guard: a VAT-bearing advance settled by a zero-VAT
        # deduction resolves to the source but is blocked on the rate mismatch.
        so_line = self._so_line()
        adv = self._advance(so_line, amount=5.0, taxes=[self.tax_vat])
        self.assertEqual(self.classifier.classify(adv).doc_kind, 'advance_invoice')

        settlement = self._settlement([(5.0, so_line)])   # deduction has NO tax -> 0%
        result = self.resolver.resolve(settlement)

        self.assertTrue(result.is_blocked)
        self.assertIn(C.BLOCK_SETTLEMENT_ADVANCE_VAT_RATE_MISMATCH, result.blockers)
        advance = result.advances[0]
        self.assertFalse(advance.is_resolved)
        self.assertEqual(advance.source_move.ids, adv.ids)  # source WAS found
        self.assertAlmostEqual(advance.deduction_rate, 0.0, places=4)  # zero-VAT deduction
        self.assertGreater(advance.source_rate, 0.0)  # VAT-bearing source (any positive rate)

    def test_reverse_vat_rate_mismatch_blocks(self):
        # Reverse direction of the same guard: a zero-VAT advance may not be settled
        # by a VAT-bearing deduction line. The source remains visible for diagnostics.
        so_line = self._so_line()
        adv = self._advance(so_line, amount=5.0)
        self.assertEqual(self.classifier.classify(adv).doc_kind, 'advance_invoice')

        settlement = self._settlement([(5.0, so_line, [self.tax_vat])])
        result = self.resolver.resolve(settlement)

        self.assertTrue(result.is_blocked)
        self.assertIn(C.BLOCK_SETTLEMENT_ADVANCE_VAT_RATE_MISMATCH, result.blockers)
        advance = result.advances[0]
        self.assertFalse(advance.is_resolved)
        self.assertEqual(advance.source_move.ids, adv.ids)
        self.assertGreater(advance.deduction_rate, 0.0)  # VAT-bearing deduction (any positive rate)
        self.assertAlmostEqual(advance.source_rate, 0.0, places=4)  # zero-VAT source

    def test_source_not_valid_advance_when_source_is_ordinary(self):
        # The deduction links (via the shared sale line) only to an ordinary invoice,
        # which is not an advance_invoice -> NOT_VALID_ADVANCE.
        so_line = self._so_line()
        ordinary = self._invoice([{
            'account': self.income_account, 'price_unit': 5.0, 'taxes': [self.tax_vat],
            'sale_lines': so_line, 'name': 'Ordinary supply on the DP sale line',
        }])
        self.assertEqual(
            self.classifier.classify(ordinary).doc_kind, 'regular_invoice')

        settlement = self._settlement([(5.0, so_line)])
        result = self.resolver.resolve(settlement)

        self.assertTrue(result.is_blocked)
        self.assertIn(
            C.BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_VALID_ADVANCE, result.blockers)
        self.assertFalse(result.advances[0].is_resolved)

    def test_non_settlement_has_no_advances(self):
        regular = self._invoice([{
            'account': self.income_account, 'price_unit': 20.0,
            'taxes': [self.tax_vat], 'name': 'Supply',
        }])
        self.assertEqual(self.classifier.classify(regular).doc_kind, 'regular_invoice')

        result = self.resolver.resolve(regular)
        self.assertFalse(result.is_settlement)
        self.assertFalse(result.is_blocked)
        self.assertEqual(len(result.advances), 0)
        self.assertFalse(result.source_moves)

    def test_multi_advance_each_resolves(self):
        so_line_a = self._so_line()
        so_line_b = self._so_line()
        dpi_a = self._advance(so_line_a, amount=5.0)
        dpi_b = self._advance(so_line_b, amount=3.0)

        settlement = self._settlement([(5.0, so_line_a), (3.0, so_line_b)])
        result = self.resolver.resolve(settlement)

        self.assertFalse(result.is_blocked)
        self.assertEqual(len(result.advances), 2)
        self.assertTrue(all(advance.is_resolved for advance in result.advances))
        self.assertEqual(set(result.source_moves.ids), {dpi_a.id, dpi_b.id})

    def test_single_deduction_linked_to_two_advances_not_found(self):
        # One deduction line pointing at two valid advances is ambiguous: the XML
        # builder would not know which single sourceDocument to reference.
        so_line_a = self._so_line()
        so_line_b = self._so_line()
        self._advance(so_line_a, amount=5.0)
        self._advance(so_line_b, amount=3.0)
        combined_lines = so_line_a | so_line_b

        settlement = self._settlement([(8.0, combined_lines)])
        result = self.resolver.resolve(settlement)

        self.assertTrue(result.is_blocked)
        self.assertIn(C.BLOCK_SETTLEMENT_ADVANCE_SOURCE_NOT_FOUND, result.blockers)
        self.assertEqual(len(result.advances), 1)
        self.assertFalse(result.advances[0].is_resolved)
        self.assertFalse(result.advances[0].source_move)
        self.assertFalse(result.source_moves)

    def test_partial_settlement_noise_excluded(self):
        # One down-payment sale line is deducted on TWO settlements. Resolving
        # settlement A must ignore settlement B (a settlement, not a DPI) and still
        # resolve to the down-payment invoice.
        so_line = self._so_line()
        dpi = self._advance(so_line, amount=5.0)
        self._settlement([(2.0, so_line)])               # settlement B (noise)
        settlement_a = self._settlement([(3.0, so_line)])

        result = self.resolver.resolve(settlement_a)
        self.assertFalse(result.is_blocked)
        self.assertEqual(len(result.advances), 1)
        self.assertEqual(result.advances[0].source_move.ids, dpi.ids)
