# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# Bucket C Step 1 — preflight orchestration tests.
#
# These assert the orchestration mechanics (sequence the services, skip classifier-
# blocked kinds, aggregate de-duplicated blockers per move and across moves), the
# collect_moves date/state filter, that a healthy move does NOT trip the amount
# reconciliation guard, and the two settlement line-presence guards. A fully green
# "exportable" path needs the complete profile->rule->cell mapping fixture and is
# asserted with the batch-lifecycle step (Bucket C Step 2); here the resolver paths are
# exercised through their blocker outcomes.

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models import constants as C
from ..services.classifier import ClassificationResult


@tagged('post_install', '-at_install')
class TestPreflight(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.preflight = cls.env['barani.pohoda.preflight']
        cls.config = cls.env.ref('barani_pohoda_export.config_pohoda')
        cls.company = cls.config.company_id
        cls.partner = cls.env['res.partner'].create({'name': 'BPE PF Customer'})
        Account = cls.env['account.account']
        cls.income_account = Account.search(
            [('account_type', '=', 'income'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.income_account:
            cls.income_account = Account.create({
                'name': 'BPE PF Income', 'code': 'BPEPFI',
                'account_type': 'income', 'company_id': cls.company.id})
        Journal = cls.env['account.journal']
        cls.sale_journal = Journal.search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.sale_journal:
            cls.sale_journal = Journal.create({
                'name': 'BPE PF Sales', 'code': 'BPEPFS', 'type': 'sale',
                'company_id': cls.company.id})
        cls.purchase_journal = Journal.search(
            [('type', '=', 'purchase'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.purchase_journal:
            cls.purchase_journal = Journal.create({
                'name': 'BPE PF Purchases', 'code': 'BPEPFP', 'type': 'purchase',
                'company_id': cls.company.id})

    def _invoice(self, invoice_date=None, post=False):
        m = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'journal_id': self.sale_journal.id,
            'invoice_date': invoice_date or fields.Date.today(),
            'invoice_line_ids': [(0, 0, {
                'name': 'x', 'quantity': 1, 'price_unit': 10.0,
                'account_id': self.income_account.id})],
        })
        if post:
            m.action_post()
        return m

    def _vendor_bill(self):
        # move_type != out_invoice/out_refund -> classifier 'unsupported' (no lines needed).
        return self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': self.partner.id,
            'journal_id': self.purchase_journal.id})

    # --- orchestration mechanics ----------------------------------------------
    def test_unsupported_move_reported_not_crashed(self):
        bill = self._vendor_bill()
        res = self.preflight.run(bill)
        self.assertEqual(len(res.move_results), 1)
        mr = res.move_results[0]
        self.assertEqual(mr.doc_kind, 'unsupported')
        self.assertFalse(mr.can_export)
        self.assertIn(C.BLOCK_UNSUPPORTED_MOVE_TYPE, mr.blockers)
        # classifier-blocked: resolvers are skipped, so no line resolutions.
        self.assertEqual(mr.line_resolutions, [])

    def test_regular_invoice_without_profile_blocks(self):
        inv = self._invoice()  # no fiscal position -> profile cannot resolve
        mr = self.preflight.run(inv).move_results[0]
        self.assertEqual(mr.doc_kind, 'regular_invoice')
        self.assertIn(C.BLOCK_MAPPING_PROFILE_NOT_FOUND, mr.blockers)
        self.assertFalse(mr.can_export)

    def test_aggregate_over_multiple_moves(self):
        bill = self._vendor_bill()
        inv = self._invoice()
        res = self.preflight.run(bill + inv)
        self.assertEqual(len(res.move_results), 2)
        self.assertTrue(res.is_blocked)
        self.assertEqual(res.exportable_moves, [])
        self.assertEqual(len(res.blocked_moves), 2)
        self.assertIn(C.BLOCK_UNSUPPORTED_MOVE_TYPE, res.all_blockers)
        self.assertIn(C.BLOCK_MAPPING_PROFILE_NOT_FOUND, res.all_blockers)

    def test_run_empty_recordset(self):
        res = self.preflight.run(self.env['account.move'])
        self.assertEqual(res.move_results, [])
        self.assertFalse(res.is_blocked)

    # --- amount reconciliation guard ------------------------------------------
    def test_amount_check_passes_for_healthy_move(self):
        # A well-formed Odoo move always reconciles (amount_untaxed == sum of subtotals);
        # the guard must not false-positive. The failure path is defensive (it requires a
        # corrupted move the ORM will not normally produce).
        inv = self._invoice()
        self.assertEqual(self.preflight._amount_check(inv), [])

    # --- settlement line-presence guards (direct) -----------------------------
    def test_settlement_missing_supply_guard(self):
        present = self._invoice().invoice_line_ids
        empty = self.env['account.move.line']
        cl = ClassificationResult('settlement_invoice', [], empty, empty, present, empty)
        out = self.preflight._settlement_line_checks(cl)
        self.assertIn(C.BLOCK_SETTLEMENT_SUPPLY_LINES_MISSING, out)
        self.assertNotIn(C.BLOCK_SETTLEMENT_DEDUCTION_LINES_MISSING, out)

    def test_settlement_missing_deduction_guard(self):
        present = self._invoice().invoice_line_ids
        empty = self.env['account.move.line']
        cl = ClassificationResult('settlement_invoice', [], present, empty, empty, empty)
        out = self.preflight._settlement_line_checks(cl)
        self.assertIn(C.BLOCK_SETTLEMENT_DEDUCTION_LINES_MISSING, out)
        self.assertNotIn(C.BLOCK_SETTLEMENT_SUPPLY_LINES_MISSING, out)

    def test_settlement_both_present_no_block(self):
        a = self._invoice().invoice_line_ids
        b = self._invoice().invoice_line_ids
        cl = ClassificationResult('settlement_invoice', [], a, self.env['account.move.line'], b,
                                  self.env['account.move.line'])
        self.assertEqual(self.preflight._settlement_line_checks(cl), [])

    # --- DOC 05 move-level gates ------------------------------------------------
    def test_draft_move_gets_not_posted(self):
        inv = self._invoice()  # draft
        mr = self.preflight.run(inv).move_results[0]
        self.assertIn(C.BLOCK_NOT_POSTED, mr.blockers)
        # Dry-run mode suppresses the posting gate but still reports mapping problems.
        mr2 = self.preflight.run(inv, require_posted=False).move_results[0]
        self.assertNotIn(C.BLOCK_NOT_POSTED, mr2.blockers)
        self.assertIn(C.BLOCK_MAPPING_PROFILE_NOT_FOUND, mr2.blockers)

    def test_posted_move_has_no_not_posted(self):
        inv = self._invoice(post=True)
        mr = self.preflight.run(inv).move_results[0]
        self.assertNotIn(C.BLOCK_NOT_POSTED, mr.blockers)

    def test_wrong_company_when_config_mismatches(self):
        company2 = self.env['res.company'].create({'name': 'BPE PF Other Co'})
        config2 = self.env['barani.pohoda.export.config'].create(
            {'company_id': company2.id})
        inv = self._invoice()  # main-company move vs other-company config
        mr = self.preflight.run(inv, config=config2).move_results[0]
        self.assertIn(C.BLOCK_WRONG_COMPANY, mr.blockers)

    def test_wrong_company_when_no_active_config(self):
        # TransactionCase rolls this back; deactivating the seeded config means the
        # move's company has no active export configuration.
        self.config.active = False
        inv = self._invoice()
        mr = self.preflight.run(inv).move_results[0]
        self.assertIn(C.BLOCK_WRONG_COMPANY, mr.blockers)

    def test_matching_company_has_no_wrong_company(self):
        inv = self._invoice()
        mr = self.preflight.run(inv, config=self.config).move_results[0]
        self.assertNotIn(C.BLOCK_WRONG_COMPANY, mr.blockers)

    # --- collect_moves ---------------------------------------------------------
    def test_collect_moves_journal_filter(self):
        journal2 = self.env['account.journal'].create({
            'name': 'BPE PF Sales 2', 'code': 'BPEPF2', 'type': 'sale',
            'company_id': self.company.id})
        inv_j1 = self._invoice(invoice_date='2026-04-10', post=True)
        inv_j2 = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'journal_id': journal2.id, 'invoice_date': '2026-04-12',
            'invoice_line_ids': [(0, 0, {
                'name': 'x', 'quantity': 1, 'price_unit': 10.0,
                'account_id': self.income_account.id})],
        })
        inv_j2.action_post()
        # Scope the config to journal 1 only (rolled back by TransactionCase).
        self.config.journal_ids = [(6, 0, [self.sale_journal.id])]
        moves = self.preflight.collect_moves(
            self.config, start_date='2026-04-01', end_date='2026-04-30')
        self.assertIn(inv_j1, moves)
        self.assertNotIn(inv_j2, moves)

    def test_collect_moves_filters_state_and_date(self):
        in_range = self._invoice(invoice_date='2026-03-15', post=True)
        out_range = self._invoice(invoice_date='2026-01-05', post=True)
        draft_in_range = self._invoice(invoice_date='2026-03-20', post=False)
        moves = self.preflight.collect_moves(
            self.config, start_date='2026-03-01', end_date='2026-03-31')
        self.assertIn(in_range, moves)
        self.assertNotIn(out_range, moves)       # outside the date window
        self.assertNotIn(draft_in_range, moves)  # not posted
