# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# Bucket C Step 3 — wizard layer tests on the shared lifecycle fixture: the export
# wizard (create + validate in one step), the response-import wizard, and the
# re-export authorization (DOC 05 edge 14: manager + reason + idempotency-hash check).

import base64
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from ..models import constants as C
from .common import PohodaLifecycleFixture


@tagged('post_install', '-at_install')
class TestWizards(PohodaLifecycleFixture, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_lifecycle_fixture()
        cls.ExportWizard = cls.env['barani.pohoda.export.wizard']
        cls.ResponseWizard = cls.env['barani.pohoda.response.import.wizard']
        cls.ReexportWizard = cls.env['barani.pohoda.reexport.wizard']

    def _accepted_move(self):
        """Run a full green lifecycle; returns (move, batch) with the move accepted."""
        move = self._regular()
        batch = self._batch()
        batch.action_validate()
        batch.action_generate_xml()
        batch.action_mark_sent()
        self.service.action_apply_response(
            batch, self._response(self._ok_item('BPE%s' % move.id, record_id='42')))
        self.assertEqual(move.barani_pohoda_export_state, 'accepted')
        return move, batch

    # ----------------------------------------------------------- export wizard
    def test_export_wizard_defaults(self):
        wiz = self.ExportWizard.create({})
        self.assertEqual(wiz.config_id, self.config)
        today = fields.Date.context_today(wiz)
        expected_end = today.replace(day=1) - timedelta(days=1)
        self.assertEqual(wiz.end_date, expected_end)
        self.assertEqual(wiz.start_date, expected_end.replace(day=1))
        self.assertEqual(wiz.date_field, self.config.key_date)

    def test_export_wizard_creates_validated_batch(self):
        self._regular()
        wiz = self.ExportWizard.create({
            'config_id': self.config.id,
            'start_date': '2025-11-01', 'end_date': '2025-11-30',
            'date_field': 'invoice_date',
        })
        action = wiz.action_create_batch()
        self.assertEqual(action['res_model'], 'barani.pohoda.export.batch')
        batch = self.env['barani.pohoda.export.batch'].browse(action['res_id'])
        self.assertEqual(batch.state, 'validated')
        self.assertEqual(batch.start_date, fields.Date.to_date('2025-11-01'))
        self.assertEqual(len(batch.batch_move_ids), 1)

    def test_export_wizard_no_moves_raises(self):
        wiz = self.ExportWizard.create({
            'config_id': self.config.id,
            'start_date': '2024-01-01', 'end_date': '2024-01-31',
            'date_field': 'invoice_date',
        })
        with self.assertRaises(UserError):
            wiz.action_create_batch()

    # --------------------------------------------------- response import wizard
    def test_response_wizard_applies(self):
        move = self._regular()
        batch = self._batch()
        batch.action_validate()
        batch.action_generate_xml()
        batch.action_mark_sent()
        response = self._response(self._ok_item('BPE%s' % move.id))
        wiz = self.ResponseWizard.create({
            'batch_id': batch.id,
            'response_file': base64.b64encode(response),
            'response_filename': 'response.xml',
        })
        action = wiz.action_import()
        self.assertEqual(action['res_id'], batch.id)
        self.assertEqual(batch.state, 'done')

    def test_response_wizard_bad_xml_raises(self):
        self._regular()
        batch = self._batch()
        batch.action_validate()
        batch.action_generate_xml()
        batch.action_mark_sent()
        wiz = self.ResponseWizard.create({
            'batch_id': batch.id,
            'response_file': base64.b64encode(b'<not a responsePack'),
        })
        with self.assertRaises(UserError):
            wiz.action_import()
        self.assertEqual(batch.state, 'sent')  # unchanged

    # ------------------------------------------------------- re-export wizard
    def test_reexport_summary_flags_unchanged_and_changed(self):
        move, _batch = self._accepted_move()
        changed = self._regular(invoice_date='2025-11-12')
        # Second accepted move whose content then changes (payment ref is writable
        # on a posted move and is part of the source hash).
        changed.sudo().write({'barani_pohoda_export_state': 'accepted',
                              'barani_pohoda_export_hash':
                                  self.service._source_hash(changed)})
        changed.payment_reference = 'VS-CHANGED-1'
        wiz = self.ReexportWizard.create({
            'move_ids': [(6, 0, [move.id, changed.id])], 'reason': 'x'})
        self.assertTrue(wiz.any_unchanged)
        self.assertIn('UNCHANGED', wiz.summary)
        self.assertIn('content changed', wiz.summary)

    def test_reexport_authorize_clears_gate(self):
        move, _batch = self._accepted_move()
        wiz = self.ReexportWizard.create({
            'move_ids': [(6, 0, [move.id])],
            'reason': 'Customer name corrected after acceptance.',
        })
        wiz.action_authorize()
        self.assertEqual(move.barani_pohoda_export_state, 'not_exported')
        body = " ".join(move.message_ids.mapped('body'))
        self.assertIn('Customer name corrected', body)
        self.assertIn('re-export', body.lower())
        # The next batch picks it up without the already-exported gate.
        batch2 = self._batch()
        batch2.action_validate()
        line = batch2.batch_move_ids.filtered(lambda l: l.move_id == move)
        self.assertEqual(line.state, 'pending')
        self.assertNotIn(C.BLOCK_ALREADY_EXPORTED, line.validation_error or '')

    def test_reexport_requires_accepted(self):
        move = self._regular()  # never exported
        wiz = self.ReexportWizard.create({
            'move_ids': [(6, 0, [move.id])], 'reason': 'x'})
        self.assertIn('will be skipped', wiz.summary)
        with self.assertRaises(UserError):
            wiz.action_authorize()

    def test_reexport_blank_reason_raises(self):
        move, _batch = self._accepted_move()
        wiz = self.ReexportWizard.create({
            'move_ids': [(6, 0, [move.id])], 'reason': '   '})
        with self.assertRaises(UserError):
            wiz.action_authorize()

    def test_wizards_are_manager_only(self):
        user = self.env['res.users'].create({
            'name': 'BPE WZ Plain user', 'login': 'bpe_wz_plain',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.ExportWizard.with_user(user).create({})
        with self.assertRaises(AccessError):
            self.ReexportWizard.with_user(user).create(
                {'move_ids': [(6, 0, [])], 'reason': 'x'})
