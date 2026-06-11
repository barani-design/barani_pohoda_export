# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError, UserError, AccessError


@tagged('post_install', '-at_install')
class TestDataModel(TransactionCase):
    """DOC-01-level checks: seed data is present and the safety constraints fire.
    These do not exercise the export workflow (DOC 02-05); they verify the data
    model and the seed shipped in step 1.6."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.env.ref('barani_pohoda_export.config_pohoda')
        cls.main_company = cls.env.ref('base.main_company')

    # ---- seed presence ----------------------------------------------------
    def test_seed_dictionaries_present(self):
        self.assertEqual(self.env.ref('barani_pohoda_export.vat_un').code, 'UN')
        self.assertEqual(self.env.ref('barani_pohoda_export.aa_bez').code, 'Bez')
        self.assertGreaterEqual(
            self.env['barani.pohoda.account.assignment'].search_count([]), 4)
        self.assertGreaterEqual(
            self.env['barani.pohoda.vat.classification'].search_count([]), 6)
        self.assertGreaterEqual(
            self.env['barani.pohoda.control.statement.code'].search_count([]), 5)
        self.assertGreaterEqual(
            self.env['barani.pohoda.moss.service.type'].search_count([]), 1)

    def test_seed_fiscal_profiles_present(self):
        self.assertGreaterEqual(
            self.env['barani.pohoda.fiscal.profile'].search_count([]), 5)
        oss = self.env.ref('barani_pohoda_export.fp_oss_b2c_goods')
        self.assertTrue(oss.is_oss)
        self.assertEqual(oss.geography, 'oss')

    def test_seed_config_present_and_safe(self):
        self.assertTrue(self.config.active)
        self.assertEqual(self.config.company_id, self.main_company)
        # ships inert: advance flow blocked, payment export off, advance ref required
        self.assertEqual(self.config.advance_flow_mode, 'block_until_configured')
        self.assertFalse(self.config.phase_1_payment_export)
        self.assertTrue(self.config.send_advance_deduction_with_reference)
        self.assertEqual(
            self.config.document_header_vat_classification_id,
            self.env.ref('barani_pohoda_export.vat_un'))

    # ---- safety constraints (deterministic ValidationError) ---------------
    def test_phase1_payment_export_blocked(self):
        with self.assertRaises(ValidationError):
            self.config.write({'phase_1_payment_export': True})
            self.env.flush_all()

    def test_advance_reference_required(self):
        with self.assertRaises(ValidationError):
            self.config.write({'send_advance_deduction_with_reference': False})
            self.env.flush_all()

    def test_batch_date_range_blocked(self):
        with self.assertRaises(ValidationError):
            self.env['barani.pohoda.export.batch'].create({
                'config_id': self.config.id,
                'start_date': '2026-06-30',
                'end_date': '2026-06-01',
            })
            self.env.flush_all()

    def test_active_category_rule_requires_categories(self):
        with self.assertRaises(ValidationError):
            self.env['barani.pohoda.export.rule'].create({
                'config_id': self.config.id,
                'name': 'Test category rule',
                'match_mode': 'category',
                'active': True,
            })
            self.env.flush_all()

    # ---- batch audit guard ------------------------------------------------
    def test_batch_audit_fields_are_service_only(self):
        batch = self.env['barani.pohoda.export.batch'].create({
            'config_id': self.config.id,
        })
        # direct writes to service-owned audit fields are rejected (incl. state)
        with self.assertRaises(UserError):
            batch.write({'request_sha256': 'deadbeef'})
        with self.assertRaises(UserError):
            batch.write({'state': 'done'})
        # the private trusted-path method (used by the export service) writes them
        batch._service_write_audit_fields(
            {'request_sha256': 'deadbeef', 'state': 'validated'})
        self.assertEqual(batch.request_sha256, 'deadbeef')
        self.assertEqual(batch.state, 'validated')

    # ---- ACL (model-level access) -----------------------------------------
    def _make_user(self, group_xmlid):
        groups = [self.env.ref('base.group_user').id, self.env.ref(group_xmlid).id]
        return self.env['res.users'].create({
            'name': 'BPE test user',
            'login': 'bpe_%s' % group_xmlid.split('.')[-1],
            'groups_id': [(6, 0, groups)],
        })

    def test_export_user_cannot_create_batch(self):
        user = self._make_user('barani_pohoda_export.group_export_user')
        with self.assertRaises(AccessError):
            self.env['barani.pohoda.export.batch'].with_user(user).create({
                'config_id': self.config.id,
                'start_date': '2026-06-01',
                'end_date': '2026-06-30',
            })

    def test_export_manager_cannot_create_batch_move(self):
        user = self._make_user('barani_pohoda_export.group_export_manager')
        with self.assertRaises(AccessError):
            self.env['barani.pohoda.export.batch.move'].with_user(user).create({})
