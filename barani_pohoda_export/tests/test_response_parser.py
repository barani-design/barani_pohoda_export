# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# Bucket B Step 4 — POHODA import response parser (acceptance test A18).
#
# Pure-parse tests assert per-item state/message/record-id extraction and the two
# failure modes; the apply tests build a real export.batch + batch.move and assert the
# parsed outcome is written onto the matching document (by xml_item_id). The produced
# record-id element names are import-test-gated; the tests use the documented
# rsp/rdc shape.

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models import constants as C

RSP = 'http://www.stormware.cz/schema/version_2/response.xsd'
RDC = 'http://www.stormware.cz/schema/version_2/documentresponse.xsd'


def _pack(items_xml, state='ok', pack_id='BPEBATCH'):
    return ('<rsp:responsePack xmlns:rsp="%s" xmlns:rdc="%s" version="2.0" id="%s" '
            'state="%s" note="" programVersion="X">%s</rsp:responsePack>'
            % (RSP, RDC, pack_id, state, items_xml)).encode('windows-1250')


@tagged('post_install', '-at_install')
class TestResponseParser(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parser = cls.env['barani.pohoda.response.parser']
        cls.config = cls.env.ref('barani_pohoda_export.config_pohoda')
        cls.company = cls.config.company_id
        cls.partner = cls.env['res.partner'].create({'name': 'BPE RP Customer'})
        Account = cls.env['account.account']
        cls.income_account = Account.search(
            [('account_type', '=', 'income'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.income_account:
            cls.income_account = Account.create({
                'name': 'BPE RP Income', 'code': 'BPERPI',
                'account_type': 'income', 'company_id': cls.company.id})
        Journal = cls.env['account.journal']
        cls.sale_journal = Journal.search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.sale_journal:
            cls.sale_journal = Journal.create({
                'name': 'BPE RP Sales', 'code': 'BPERPS', 'type': 'sale',
                'company_id': cls.company.id})

    def _batch_with_move(self):
        move = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(), 'journal_id': self.sale_journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'x', 'quantity': 1, 'price_unit': 10.0,
                'account_id': self.income_account.id})],
        })
        batch = self.env['barani.pohoda.export.batch'].create({
            'name': 'RP test', 'company_id': self.company.id, 'config_id': self.config.id})
        line = self.env['barani.pohoda.export.batch.move'].create({
            'batch_id': batch.id, 'move_id': move.id,
            'xml_item_id': 'BPE%s' % move.id, 'state': 'sent'})
        return batch, line

    # --- pure parse ------------------------------------------------------------
    def test_parse_ok_item(self):
        res = self.parser.parse(_pack('<rsp:responsePackItem id="BPE42" state="ok"/>'))
        self.assertTrue(res.ok)
        self.assertFalse(res.is_blocked)
        self.assertEqual(res.pack_state, 'ok')
        self.assertEqual(len(res.items), 1)
        it = res.items[0]
        self.assertEqual(it.item_id, 'BPE42')
        self.assertEqual(it.state, 'ok')
        self.assertEqual(it.move_state, 'accepted')
        self.assertTrue(it.is_ok)

    def test_parse_error_item_message(self):
        res = self.parser.parse(_pack(
            '<rsp:responsePackItem id="BPE7" state="error" note="Cislo uz existuje"/>'))
        it = res.items[0]
        self.assertEqual(it.state, 'error')
        self.assertEqual(it.move_state, 'error')
        self.assertTrue(it.is_error)
        self.assertIn('Cislo uz existuje', it.message or '')

    def test_parse_warning_item(self):
        res = self.parser.parse(_pack(
            '<rsp:responsePackItem id="BPE8" state="warning" note="Doplnene"/>'))
        self.assertEqual(res.items[0].move_state, 'warning')

    def test_parse_produced_record_id(self):
        item = ('<rsp:responsePackItem id="BPE9" state="ok">'
                '<rdc:producedDetails><rdc:id>123</rdc:id>'
                '<rdc:number>FV2026001</rdc:number></rdc:producedDetails>'
                '</rsp:responsePackItem>')
        it = self.parser.parse(_pack(item)).items[0]
        self.assertEqual(it.record_id, '123')
        self.assertEqual(it.document_number, 'FV2026001')

    def test_malformed_blocks(self):
        res = self.parser.parse(b'<rsp:responsePack garbage')
        self.assertFalse(res.ok)
        self.assertIn(C.BLOCK_RESPONSE_PARSE_FAILED, res.blockers)

    def test_non_responsepack_blocks(self):
        res = self.parser.parse(b'<foo/>')
        self.assertFalse(res.ok)
        self.assertIn(C.BLOCK_RESPONSE_PARSE_FAILED, res.blockers)

    def test_empty_blocks(self):
        res = self.parser.parse(b'')
        self.assertIn(C.BLOCK_RESPONSE_PARSE_FAILED, res.blockers)

    # --- apply onto the batch documents ---------------------------------------
    def test_apply_writes_onto_move(self):
        batch, line = self._batch_with_move()
        item = ('<rsp:responsePackItem id="%s" state="ok"><rdc:producedDetails>'
                '<rdc:id>555</rdc:id></rdc:producedDetails></rsp:responsePackItem>'
                % line.xml_item_id)
        res = self.parser.apply_to_batch(batch, response=_pack(item))
        self.assertFalse(res.is_blocked)
        self.assertEqual(res.unmatched_item_ids, [])
        self.assertEqual(line.response_state, 'ok')
        self.assertEqual(line.state, 'accepted')
        self.assertEqual(line.pohoda_record_id, '555')

    def test_apply_error_sets_error_state(self):
        batch, line = self._batch_with_move()
        item = '<rsp:responsePackItem id="%s" state="error" note="Duplicate"/>' % line.xml_item_id
        self.parser.apply_to_batch(batch, response=_pack(item))
        self.assertEqual(line.state, 'error')
        self.assertIn('Duplicate', line.response_message or '')

    def test_apply_unmatched_item_is_reported(self):
        batch, line = self._batch_with_move()
        res = self.parser.apply_to_batch(
            batch, response=_pack('<rsp:responsePackItem id="BPE_NOPE_999" state="ok"/>'))
        self.assertIn('BPE_NOPE_999', res.unmatched_item_ids)
        self.assertNotEqual(line.state, 'accepted')  # the real move is untouched

    def test_apply_on_malformed_blocks_and_writes_nothing(self):
        batch, line = self._batch_with_move()
        res = self.parser.apply_to_batch(batch, response=b'<not valid')
        self.assertIn(C.BLOCK_RESPONSE_PARSE_FAILED, res.blockers)
        self.assertEqual(line.state, 'sent')  # unchanged
