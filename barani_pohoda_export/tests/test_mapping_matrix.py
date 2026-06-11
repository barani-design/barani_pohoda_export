# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 03 seed verification. Confirms the matrix seed loaded: the 6 template rules
# (seeded INACTIVE) and the 30 mapping cells, plus a few spot-checks of states and
# resolved dictionary codes. A broken ref in the seed already fails the install; this
# additionally pins the intended values so a future edit cannot silently drift them.

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMappingMatrixSeed(TransactionCase):

    def _ref(self, xmlid):
        return self.env.ref('barani_pohoda_export.%s' % xmlid)

    def test_rules_seeded_inactive(self):
        Rule = self.env['barani.pohoda.export.rule']
        # Rules ship inactive (templates), so they are only visible with active_test off.
        rules = Rule.with_context(active_test=False).search([])
        self.assertGreaterEqual(len(rules), 6)
        odpocet = self._ref('rule_odpocet_zalohy')
        self.assertFalse(odpocet.active)
        self.assertEqual(odpocet.match_mode, 'product')
        self.assertEqual(odpocet.sequence, 1)
        sluzby = self._ref('rule_sluzby')
        self.assertTrue(sluzby.residual_only)

    def test_cells_seeded(self):
        Cell = self.env['barani.pohoda.export.rule.mapping.cell']
        # Total stays >= 30 so future credit-note cells don't break this.
        self.assertGreaterEqual(Cell.search_count([]), 30)
        # The seeded invoice-layer matrix is exactly 6 rules x 5 profiles = 30.
        self.assertEqual(Cell.search_count([('document_kind', '=', 'invoice')]), 30)
        # Bucket B Step 1 adds explicit settlement-deduction cells for the down-payment rule.
        self.assertEqual(Cell.search_count([('document_kind', '=', 'down_payment_deduction')]), 5)

    def test_blocked_cell(self):
        cell = self._ref('cell_repairs_calibration_oss_b2c_goods')
        self.assertEqual(cell.enabled_state, 'blocked')
        # A blocked cell carries no export codes.
        self.assertFalse(cell.account_assignment_id)
        self.assertFalse(cell.vat_classification_id)

    def test_active_cell_codes_resolve(self):
        cell = self._ref('cell_shipping_goods_sk_domestic_no_vat_id')
        self.assertEqual(cell.enabled_state, 'active')
        self.assertEqual(cell.document_kind, 'invoice')
        self.assertEqual(cell.account_assignment_id.code, '2')
        self.assertEqual(cell.vat_classification_id.code, 'UD')
        self.assertEqual(cell.control_statement_code_id.code, 'D2')

    def test_oq1_domestic_advance_cells_are_review_required(self):
        inv_cell = self._ref('cell_odpocet_zalohy_sk_domestic_vat_payer')
        ded_cell = self._ref('cell_odpocet_zalohy_deduction_sk_domestic_vat_payer')
        self.assertEqual(inv_cell.enabled_state, 'review_required')
        self.assertEqual(ded_cell.document_kind, 'down_payment_deduction')
        self.assertEqual(ded_cell.enabled_state, 'review_required')

    def test_oss_cell_has_moss_type(self):
        cell = self._ref('cell_tovar_oss_b2c_goods')
        self.assertEqual(cell.moss_service_type_id.code, 'GD')

    def test_review_required_cell(self):
        cell = self._ref('cell_sluzby_oss_b2c_goods')
        self.assertEqual(cell.enabled_state, 'review_required')
