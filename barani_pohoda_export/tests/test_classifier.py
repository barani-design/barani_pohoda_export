# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 02 acceptance tests for the document classifier.
#
# These exercise the *shape* classification on draft invoices. The classifier is
# posted-agnostic by design (posted / company / date-range gating is the DOC 05
# preflight), and settlement source-DPI resolution is the DOC 02 resolver — both
# are tested separately. Lines are intentionally product-less: the classifier only
# reads account code, amounts and VAT, so this keeps the fixtures minimal and
# independent of any chart-of-accounts localisation.

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models import constants as C


@tagged('post_install', '-at_install')
class TestClassifier(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({'name': 'BPE Test Customer'})
        cls.classifier = cls.env['barani.pohoda.document.classifier']

        Account = cls.env['account.account']
        # Reuse an existing income account if the company has one, else create one.
        cls.income_account = Account.search(
            [('account_type', '=', 'income'), ('company_id', '=', cls.company.id)],
            limit=1)
        if not cls.income_account:
            cls.income_account = Account.create({
                'name': 'BPE Test Income',
                'code': 'BPEINC',
                'account_type': 'income',
                'company_id': cls.company.id,
            })
        # Reuse an existing received-advance ('324...') account if present, else
        # create one. The classifier identifies advances purely by the '324' prefix.
        cls.advance_account = Account.search(
            [('code', '=like', '324%'), ('company_id', '=', cls.company.id)],
            limit=1)
        if not cls.advance_account:
            cls.advance_account = Account.create({
                'name': 'BPE Test Advances Received',
                'code': '324999',
                'account_type': 'liability_current',
                'company_id': cls.company.id,
            })

        # Reuse an existing NON-price-included positive sale tax (the build DB has one
        # with working repartition/accounts); create a minimal one only as a fallback.
        # The classifier reads account code + line sign, not the VAT rate, so its
        # outcomes are independent of which positive-rate sale tax this is.
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

        # A sale journal must exist for invoice creation on a minimal test DB.
        Journal = cls.env['account.journal']
        cls.sale_journal = Journal.search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.sale_journal:
            cls.sale_journal = Journal.create({
                'name': 'BPE Test Sales', 'code': 'BPESJ', 'type': 'sale',
                'company_id': cls.company.id,
            })

    # ------------------------------------------------------------------ helper
    def _invoice(self, lines, move_type='out_invoice'):
        """Create a DRAFT move. ``lines`` = list of dicts: account/price_unit/(quantity)/(taxes)."""
        commands = []
        for spec in lines:
            commands.append((0, 0, {
                'name': spec.get('name', 'line'),
                'account_id': spec['account'].id,
                'price_unit': spec['price_unit'],
                'quantity': spec.get('quantity', 1.0),
                'tax_ids': [(6, 0, [t.id for t in spec.get('taxes', [])])],
            }))
        return self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(),
            'journal_id': self.sale_journal.id,
            'invoice_line_ids': commands,
        })

    # ------------------------------------------------------------------- tests
    def test_regular_invoice(self):
        move = self._invoice([
            {'account': self.income_account, 'price_unit': 20.0, 'taxes': [self.tax_vat]},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'regular_invoice')
        self.assertFalse(res.blockers)
        self.assertFalse(res.is_blocked)

    def test_advance_invoice_zero_vat(self):
        # A zero-VAT advance (EU B2B / non-EU export) is a valid advance_invoice.
        move = self._invoice([
            {'account': self.advance_account, 'price_unit': 3.0, 'taxes': []},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'advance_invoice')
        self.assertFalse(res.blockers)
        self.assertEqual(len(res.dp_pos_lines), 1)

    def test_advance_invoice_with_vat(self):
        # OQ-1 v2: a VAT-bearing advance (domestic / OSS) is a VALID advance_invoice,
        # not a blocked DPI. VAT is read from the line; no shape blocker fires.
        move = self._invoice([
            {'account': self.advance_account, 'price_unit': 3.0, 'taxes': [self.tax_vat]},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'advance_invoice')
        self.assertFalse(res.blockers)
        self.assertFalse(res.is_blocked)
        self.assertEqual(len(res.dp_pos_lines), 1)

    def test_mixed_324_with_ordinary_blocks(self):
        move = self._invoice([
            {'account': self.income_account, 'price_unit': 20.0, 'taxes': [self.tax_vat]},
            {'account': self.advance_account, 'price_unit': 3.0, 'taxes': []},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'blocked_mixed_324')
        self.assertIn(C.BLOCK_MIXED_POSITIVE_324_AND_ORDINARY_LINES, res.blockers)

    def test_settlement_invoice(self):
        move = self._invoice([
            {'account': self.income_account, 'price_unit': 20.0, 'taxes': [self.tax_vat]},
            {'account': self.advance_account, 'price_unit': -3.0, 'taxes': []},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'settlement_invoice')
        # Shape is valid; the source-DPI resolution gate is exercised in 2.2.
        self.assertFalse(res.blockers)
        self.assertEqual(len(res.dp_neg_lines), 1)

    def test_settlement_with_vat_deduction_is_shape_valid(self):
        # OQ-1 v2: a VAT-bearing advance is reversed at its own rate, so a VAT-bearing
        # deduction is expected — the classifier raises NO shape blocker. The
        # deduction-vs-source rate match is the resolver's job (tested there).
        move = self._invoice([
            {'account': self.income_account, 'price_unit': 20.0, 'taxes': [self.tax_vat]},
            {'account': self.advance_account, 'price_unit': -3.0, 'taxes': [self.tax_vat]},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'settlement_invoice')
        self.assertFalse(res.blockers)
        self.assertFalse(res.is_blocked)
        self.assertEqual(len(res.dp_neg_lines), 1)

    def test_positive_and_negative_324_without_ordinary_blocks(self):
        move = self._invoice([
            {'account': self.advance_account, 'price_unit': 5.0, 'taxes': []},
            {'account': self.advance_account, 'price_unit': -3.0, 'taxes': []},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'blocked_mixed_324')

    def test_credit_note_is_test_gated(self):
        move = self._invoice([
            {'account': self.income_account, 'price_unit': 20.0, 'taxes': [self.tax_vat]},
        ], move_type='out_refund')
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'credit_note')
        self.assertTrue(res.is_test_gated)

    def test_vendor_bill_is_unsupported(self):
        # move_type is checked before any line splitting, so an in-memory record is
        # enough — no purchase journal needs to exist on the test DB.
        move = self.env['account.move'].new({
            'move_type': 'in_invoice',
            'company_id': self.company.id,
            'currency_id': self.company.currency_id.id,
        })
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'unsupported')
        self.assertIn(C.BLOCK_UNSUPPORTED_MOVE_TYPE, res.blockers)

    def test_empty_invoice_is_unsupported(self):
        # Truth-table row 0/0/0: no substantive ordinary or advance lines.
        move = self.env['account.move'].new({
            'move_type': 'out_invoice',
            'company_id': self.company.id,
            'currency_id': self.company.currency_id.id,
        })
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'unsupported')
        self.assertIn(C.BLOCK_UNSUPPORTED_MOVE_TYPE, res.blockers)
        self.assertTrue(res.is_blocked)

    def test_lone_negative_324_is_unsupported(self):
        # Truth-table row 0/0/1: a negative-324 line without ordinary supply is
        # not a settlement invoice and must not silently export.
        move = self._invoice([
            {'account': self.advance_account, 'price_unit': -3.0, 'taxes': []},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'unsupported')
        self.assertIn(C.BLOCK_UNSUPPORTED_MOVE_TYPE, res.blockers)
        self.assertEqual(len(res.dp_neg_lines), 1)

    def test_credit_note_with_mixed_324_stays_credit_note(self):
        # Credit notes are test-gated as credit notes; their 324 shape is not rerouted
        # into advance/settlement logic inside Bucket A.
        move = self._invoice([
            {'account': self.advance_account, 'price_unit': 5.0, 'taxes': [self.tax_vat]},
            {'account': self.advance_account, 'price_unit': -3.0, 'taxes': []},
        ], move_type='out_refund')
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'credit_note')
        self.assertTrue(res.is_test_gated)
        self.assertFalse(res.blockers)

    def test_zero_value_324_line_ignored(self):
        # Policy: a 0.00 advance line is non-substantive. ordinary + zero-324 stays
        # a regular invoice; the zero line is surfaced only in zero_lines.
        move = self._invoice([
            {'account': self.income_account, 'price_unit': 20.0, 'taxes': [self.tax_vat]},
            {'account': self.advance_account, 'price_unit': 0.0, 'taxes': []},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'regular_invoice')
        self.assertEqual(len(res.zero_lines), 1)
        self.assertFalse(res.is_blocked)

    def test_mixed_324_with_ordinary_beats_advance(self):
        # Branch-priority contract: positive 324 + ordinary is mixed_324 even when the
        # advance line carries VAT — the mixed-324 shape is checked before the advance
        # branch, so a positive 324 mixed with ordinary lines never becomes an advance.
        move = self._invoice([
            {'account': self.income_account, 'price_unit': 20.0, 'taxes': [self.tax_vat]},
            {'account': self.advance_account, 'price_unit': 3.0, 'taxes': [self.tax_vat]},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'blocked_mixed_324')

    def test_incomplete_ordinary_line_still_blocks_clean_advance(self):
        # Draft/onchange edge case: even if an ordinary product line has no account yet,
        # it is still substantive and must prevent the move from being classified as a
        # clean advance_invoice. Ignoring no-account product lines would be a dangerous
        # false positive.
        move = self.env['account.move'].new({
            'move_type': 'out_invoice',
            'partner_id': self.partner.id,
            'company_id': self.company.id,
            'currency_id': self.company.currency_id.id,
            'invoice_line_ids': [
                (0, 0, {'name': 'Incomplete ordinary line', 'price_unit': 20.0, 'quantity': 1.0}),
                (0, 0, {
                    'name': 'Advance line',
                    'account_id': self.advance_account.id,
                    'price_unit': 3.0,
                    'quantity': 1.0,
                }),
            ],
        })
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'blocked_mixed_324')
        self.assertIn(C.BLOCK_MIXED_POSITIVE_324_AND_ORDINARY_LINES, res.blockers)

    def test_positive_and_negative_324_with_vat_is_mixed(self):
        # OQ-1 v2: with the vat-bearing-DPI branch gone, a positive + negative 324 with
        # no ordinary line is blocked_mixed_324 whether or not the positive carries VAT.
        move = self._invoice([
            {'account': self.advance_account, 'price_unit': 5.0, 'taxes': [self.tax_vat]},
            {'account': self.advance_account, 'price_unit': -3.0, 'taxes': []},
        ])
        res = self.classifier.classify(move)
        self.assertEqual(res.doc_kind, 'blocked_mixed_324')
