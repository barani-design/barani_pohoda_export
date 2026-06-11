# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# Bucket C Step 2 — export batch lifecycle tests.
#
# End-to-end on a complete ACTIVE mapping chain: draft -> validated -> xml_generated ->
# sent -> done/warning/error, the service-level gates (credit notes Phase-1 gated,
# already-accepted re-export blocked), the 324-mapping config audit, the narrow
# batch.move service path (manager-or-su), attachment + SHA-256 archival, and the
# account.move status mirrors.

import base64
import hashlib

from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from ..models import constants as C
from .common import PohodaLifecycleFixture


@tagged('post_install', '-at_install')
class TestExportService(PohodaLifecycleFixture, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_lifecycle_fixture()

    # ------------------------------------------------------------- green path
    def test_full_lifecycle_green(self):
        move = self._regular()
        batch = self._batch()

        # validate
        batch.action_validate()
        self.assertEqual(batch.state, 'validated')
        self.assertEqual(len(batch.batch_move_ids), 1)
        line = batch.batch_move_ids
        self.assertEqual(line.state, 'pending')
        self.assertEqual(line.barani_doc_kind, 'regular_invoice')
        self.assertTrue(line.source_hash)
        self.assertIn("Exportable: 1", batch.validation_summary)
        self.assertEqual(move.barani_pohoda_doc_kind, 'regular_invoice')

        # generate
        batch.action_generate_xml()
        self.assertEqual(batch.state, 'xml_generated')
        att = batch.request_attachment_id
        self.assertTrue(att)
        raw = base64.b64decode(att.datas)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), batch.request_sha256)
        self.assertIn(b'dataPack', raw)
        self.assertEqual(line.state, 'xml_generated')
        self.assertEqual(line.xml_item_id, 'BPE%s' % move.id)
        self.assertTrue(batch.mapping_version_hash)
        self.assertEqual(move.barani_pohoda_export_state, 'xml_generated')
        self.assertEqual(move.barani_pohoda_export_hash, line.source_hash)

        # mark sent
        batch.action_mark_sent()
        self.assertEqual(batch.state, 'sent')
        self.assertTrue(batch.sent_by and batch.sent_at)
        self.assertEqual(line.state, 'sent')
        self.assertEqual(move.barani_pohoda_export_state, 'sent')

        # apply response (accepted, with a produced record id)
        response = self._response(self._ok_item(line.xml_item_id, record_id='777'))
        self.service.action_apply_response(batch, response)
        self.assertEqual(batch.state, 'done')
        self.assertEqual(line.state, 'accepted')
        self.assertEqual(line.pohoda_record_id, '777')
        self.assertEqual(
            hashlib.sha256(response).hexdigest(), batch.response_sha256)
        self.assertEqual(move.barani_pohoda_export_state, 'accepted')
        self.assertEqual(move.barani_pohoda_pohoda_record_id, '777')
        self.assertEqual(move.barani_pohoda_last_success_batch_id, batch)

    # ------------------------------------------------------------------ gates
    def test_generate_excludes_blocked_move(self):
        good = self._regular()
        bad = self._regular(fpos=False)  # no fiscal position -> profile blocker
        batch = self._batch()
        batch.action_validate()
        by_move = {l.move_id: l for l in batch.batch_move_ids}
        self.assertEqual(by_move[good].state, 'pending')
        self.assertEqual(by_move[bad].state, 'blocked')
        self.assertIn(C.BLOCK_MAPPING_PROFILE_NOT_FOUND,
                      by_move[bad].validation_error or '')
        self.assertEqual(bad.barani_pohoda_export_state, 'blocked')

        batch.action_generate_xml()
        raw = base64.b64decode(batch.request_attachment_id.datas)
        self.assertIn(b'BPE%d' % good.id, raw)
        self.assertNotIn(b'BPE%d' % bad.id, raw)

    def test_credit_note_is_gated(self):
        self._regular()
        self._invoice([(self.goods_product, 30.0, None)], move_type='out_refund')
        batch = self._batch()
        batch.action_validate()
        refund_line = batch.batch_move_ids.filtered(
            lambda l: l.barani_doc_kind == 'credit_note')
        self.assertEqual(refund_line.state, 'blocked')
        self.assertIn(C.BLOCK_CREDIT_NOTE_NOT_TESTED, refund_line.validation_error)

    def test_already_exported_is_blocked(self):
        move = self._regular()
        move.sudo().write({'barani_pohoda_export_state': 'accepted'})
        batch = self._batch()
        batch.action_validate()
        line = batch.batch_move_ids
        self.assertEqual(line.state, 'blocked')
        self.assertIn(C.BLOCK_ALREADY_EXPORTED, line.validation_error)

    # ------------------------------------------------------ 324 mapping audit
    def test_advance_mapping_not_324_blocks(self):
        # Misconfigure: the advance (DPI) cell maps to an ordinary assignment.
        self.cell_dp_adv.account_assignment_id = self.aa1
        self._advance()
        batch = self._batch()
        batch.action_validate()
        line = batch.batch_move_ids
        self.assertEqual(line.barani_doc_kind, 'advance_invoice')
        self.assertEqual(line.state, 'blocked')
        self.assertIn(C.BLOCK_ADVANCE_ACCOUNT_MAPPING_NOT_324, line.validation_error)

    def test_advance_mapping_324_passes(self):
        self._advance()  # cell_dp_adv carries aa_advance_324 (is_advance_account=True)
        batch = self._batch()
        batch.action_validate()
        line = batch.batch_move_ids
        self.assertEqual(line.state, 'pending')
        self.assertNotIn(C.BLOCK_ADVANCE_ACCOUNT_MAPPING_NOT_324,
                         line.validation_error or '')

    def test_deduction_mapping_not_324_blocks(self):
        self.cell_dp_ded.account_assignment_id = self.aa1
        self._settlement()
        batch = self._batch()
        batch.action_validate()
        line = batch.batch_move_ids
        self.assertEqual(line.barani_doc_kind, 'settlement_invoice')
        self.assertIn(C.BLOCK_SETTLEMENT_DEDUCTION_ACCOUNT_MAPPING_NOT_324,
                      line.validation_error)

    # ------------------------------------------------------- response outcomes
    def test_response_error_sets_error(self):
        move = self._regular()
        batch = self._batch()
        batch.action_validate()
        batch.action_generate_xml()
        batch.action_mark_sent()
        response = self._response(
            self._error_item('BPE%s' % move.id, note="Duplicate number"))
        self.service.action_apply_response(batch, response)
        self.assertEqual(batch.state, 'error')
        self.assertEqual(batch.batch_move_ids.state, 'error')
        self.assertEqual(move.barani_pohoda_export_state, 'error')
        self.assertIn('Duplicate number', move.barani_pohoda_last_error)

    def test_response_partial_sets_warning(self):
        m1 = self._regular()
        m2 = self._regular(invoice_date='2025-11-12')
        batch = self._batch()
        batch.action_validate()
        batch.action_generate_xml()
        batch.action_mark_sent()
        # Only m1 answered; m2's document stays 'sent' -> warning + named in summary.
        response = self._response(self._ok_item('BPE%s' % m1.id))
        self.service.action_apply_response(batch, response)
        self.assertEqual(batch.state, 'warning')
        self.assertIn(m2.name, batch.validation_summary)

    # ---------------------------------------------------------------- guards
    def test_state_guards(self):
        self._regular()
        batch = self._batch()
        with self.assertRaises(UserError):
            batch.action_generate_xml()        # draft, not validated
        with self.assertRaises(UserError):
            batch.action_mark_sent()           # draft, not xml_generated
        with self.assertRaises(UserError):
            self.service.action_apply_response(batch, b'<x/>')  # draft, not sent
        batch.action_validate()
        with self.assertRaises(UserError):
            batch.action_validate()            # already validated

    def test_validate_requires_moves_in_scope(self):
        batch = self._batch(start='2024-01-01', end='2024-01-31')
        with self.assertRaises(UserError):
            batch.action_validate()

    def test_non_manager_cannot_run_service(self):
        self._regular()
        batch = self._batch()
        user = self.env['res.users'].create({
            'name': 'BPE LC Plain user', 'login': 'bpe_lc_plain',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self.assertRaises(AccessError):
            self.service.with_user(user).action_validate(batch)

    def test_revalidate_replaces_rows(self):
        self._regular()
        batch = self._batch()
        batch.action_validate()
        self.assertEqual(len(batch.batch_move_ids), 1)
        batch.action_reset_to_draft()
        self.assertEqual(batch.state, 'draft')
        batch.action_validate()
        self.assertEqual(len(batch.batch_move_ids), 1)  # replaced, not duplicated

    def test_cancel_resets_mirror(self):
        move = self._regular()
        batch = self._batch()
        batch.action_validate()
        batch.action_cancel()
        self.assertEqual(batch.state, 'cancelled')
        self.assertEqual(move.barani_pohoda_export_state, 'not_exported')
