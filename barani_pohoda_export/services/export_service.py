# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 05 / Bucket C Step 2 — export batch lifecycle service.
#
# Drives an export.batch through its life:
#
#   draft --validate--> validated --generate--> xml_generated --mark sent--> sent
#        --apply response--> done | warning | error          (cancel from pre-sent states)
#
# * validate:  collect posted moves in the batch scope, run the read-only preflight
#   (with the DOC 05 gates), apply the service-level gates that need batch context
#   (credit notes Phase-1 gated; already-accepted moves blocked per DOC 05 edge 14),
#   and (re)create the per-document audit rows with doc_kind / blocked state /
#   blocker codes / idempotency source_hash.
# * generate:  build the dataPack for the pending documents, XSD-validate it, archive
#   the request as an attachment with its SHA-256 plus mapping/config version hashes.
# * mark sent: the human has imported the file into POHODA (no auto-send in Phase 1).
# * apply response: archive the response + SHA-256, parse it, write per-document
#   outcomes, derive the batch end state, and mirror the latest status onto the moves.
#
# Security model: batch audit fields go through batch._service_write_audit_fields();
# per-document rows go through batch.move._service_create/_service_write/_service_unlink
# (the narrow sudo path: manager-or-su + company checks). The account.move mirror
# fields (module-owned barani_pohoda_* only) are written with a scoped sudo so an
# Export Manager does not need accounting write rights for the mirror to update; the
# values written are derived exclusively from module state, never from user input.

import base64
import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..models import constants as C


class PohodaExportService(models.AbstractModel):
    _name = 'barani.pohoda.export.service'
    _description = "BARANI POHODA export batch lifecycle service (DOC 05, Bucket C)"

    # ------------------------------------------------------------------ guards
    @api.model
    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group(
                'barani_pohoda_export.group_export_manager'):
            raise AccessError(_(
                "Only Export Managers may run the POHODA export service."))

    @api.model
    def _require_state(self, batch, expected, action):
        if batch.state != expected:
            raise UserError(_(
                "%(action)s requires the batch to be in state '%(expected)s' "
                "(current: '%(state)s').") % {
                    'action': action, 'expected': expected, 'state': batch.state})

    # ---------------------------------------------------------------- validate
    @api.model
    def action_validate(self, batch):
        """Collect the batch scope, run the preflight, (re)build the document rows."""
        batch.ensure_one()
        self._check_manager()
        self._require_state(batch, 'draft', _("Validate"))
        config = batch.config_id

        Preflight = self.env['barani.pohoda.preflight']
        moves = Preflight.collect_moves(
            config, start_date=batch.start_date, end_date=batch.end_date,
            date_field=batch.date_field)
        if not moves:
            raise UserError(_(
                "No posted customer invoices/refunds match the batch scope."))

        report = Preflight.run(moves, config=config)

        # Re-validation in draft replaces the previous rows.
        if batch.batch_move_ids:
            batch.batch_move_ids._service_unlink()

        BatchMove = self.env['barani.pohoda.export.batch.move']
        n_blocked = 0
        blocker_counts = {}
        vals_list = []
        for mr in report.move_results:
            blockers = list(mr.blockers)
            # Phase 1: credit notes are import-test gated (DOC 02 / config policy);
            # the builder refuses them outside test_mode, so gate them here visibly.
            if mr.is_test_gated:
                blockers.append(C.BLOCK_CREDIT_NOTE_NOT_TESTED)
            # DOC 05 edge 14: an already-accepted move is not silently re-exported.
            if mr.move.barani_pohoda_export_state == 'accepted':
                blockers.append(C.BLOCK_ALREADY_EXPORTED)

            blocked = bool(blockers) or not mr.can_export
            n_blocked += 1 if blocked else 0
            for code in blockers:
                blocker_counts[code] = blocker_counts.get(code, 0) + 1
            vals_list.append({
                'batch_id': batch.id,
                'move_id': mr.move.id,
                'barani_doc_kind': mr.doc_kind,
                'state': 'blocked' if blocked else 'pending',
                'validation_error_code': blockers[0] if blockers else False,
                'validation_error': "\n".join(blockers) if blockers else False,
                'source_hash': self._source_hash(mr.move),
            })
        lines = BatchMove._service_create(vals_list)

        summary = self._validation_summary(len(lines), n_blocked, blocker_counts)
        batch._service_write_audit_fields({
            'state': 'validated',
            'validation_summary': summary,
        })
        # Mirror doc kind + blocked status onto the moves.
        for line in lines:
            mirror = {'barani_pohoda_doc_kind': line.barani_doc_kind,
                      'barani_pohoda_last_batch_id': batch.id}
            if line.state == 'blocked':
                mirror['barani_pohoda_export_state'] = 'blocked'
                mirror['barani_pohoda_last_error'] = line.validation_error
            self._write_move_mirror(line.move_id, mirror)
        return lines

    # ---------------------------------------------------------------- generate
    @api.model
    def action_generate_xml(self, batch):
        """Build + XSD-validate the dataPack for the pending documents; archive it."""
        batch.ensure_one()
        self._check_manager()
        self._require_state(batch, 'validated', _("Generate XML"))
        config = batch.config_id

        pending = batch.batch_move_ids.filtered(lambda l: l.state == 'pending')
        if not pending:
            raise UserError(_(
                "There are no exportable documents in this batch — every document "
                "is blocked. Review the validation summary."))

        Builder = self.env['barani.pohoda.xml.builder']
        moves = pending.mapped('move_id')
        xml_bytes = Builder.build(moves)

        xsd = self.env['barani.pohoda.xsd.validator'].validate(xml_bytes, config=config)
        if not xsd.ok:
            # Roll back nothing persistent: fail loudly so the schema/mapping is fixed
            # and Generate is retried. (The batch stays 'validated'.)
            raise UserError(_(
                "The generated XML failed XSD validation:\n%s"
            ) % "\n".join(xsd.errors[:10]))

        attachment = self.env['ir.attachment'].create({
            'name': '%s_request.xml' % (batch.name or 'pohoda_export'),
            'datas': base64.b64encode(xml_bytes),
            'res_model': batch._name,
            'res_id': batch.id,
            'mimetype': 'application/xml',
        })
        batch._service_write_audit_fields({
            'state': 'xml_generated',
            'request_attachment_id': attachment.id,
            'request_sha256': hashlib.sha256(xml_bytes).hexdigest(),
            'mapping_version_hash': self._mapping_version_hash(config),
            'config_version_hash': self._config_version_hash(config),
        })
        for line in pending:
            line._service_write({
                'state': 'xml_generated',
                'xml_item_id': Builder._pack_item_id(line.move_id),
            })
            self._write_move_mirror(line.move_id, {
                'barani_pohoda_export_state': 'xml_generated',
                'barani_pohoda_export_hash': line.source_hash,
                'barani_pohoda_last_batch_id': batch.id,
            })
        return attachment

    # --------------------------------------------------------------- mark sent
    @api.model
    def action_mark_sent(self, batch):
        """Record that the request file was imported into POHODA (manual, Phase 1)."""
        batch.ensure_one()
        self._check_manager()
        self._require_state(batch, 'xml_generated', _("Mark sent"))
        if not batch.request_attachment_id:
            raise UserError(_("Generate the request XML before marking the batch sent."))
        batch._service_write_audit_fields({
            'state': 'sent',
            'sent_by': self.env.user.id,
            'sent_at': fields.Datetime.now(),
        })
        lines = batch.batch_move_ids.filtered(lambda l: l.state == 'xml_generated')
        lines._service_write({'state': 'sent'})
        for line in lines:
            self._write_move_mirror(line.move_id, {
                'barani_pohoda_export_state': 'sent',
                'barani_pohoda_exported_at': fields.Datetime.now(),
            })
        return True

    # ----------------------------------------------------------- apply response
    @api.model
    def action_apply_response(self, batch, response):
        """Archive + parse the POHODA response; derive line, move and batch outcomes."""
        batch.ensure_one()
        self._check_manager()
        self._require_state(batch, 'sent', _("Apply response"))
        if isinstance(response, str):
            response = response.encode('utf-8')

        Parser = self.env['barani.pohoda.response.parser']
        result = Parser.parse(response)
        if result.is_blocked:
            raise UserError(_(
                "The POHODA response could not be parsed:\n%s"
            ) % "\n".join(result.errors[:5]))

        attachment = self.env['ir.attachment'].create({
            'name': '%s_response.xml' % (batch.name or 'pohoda_export'),
            'datas': base64.b64encode(response),
            'res_model': batch._name,
            'res_id': batch.id,
            'mimetype': 'application/xml',
        })
        result = Parser.apply_to_batch(batch, result=result)

        # Derive the batch end state from the per-document outcomes.
        states = set(batch.batch_move_ids.mapped('state'))
        relevant = states - {'blocked'}  # blocked documents were never sent
        if relevant and relevant <= {'accepted'} and not result.unmatched_item_ids:
            end_state = 'done'
        elif 'error' in relevant:
            end_state = 'error'
        else:
            end_state = 'warning'

        summary = batch.validation_summary or ""
        if result.unmatched_item_ids:
            summary += _("\nResponse items without a matching document: %s") % (
                ", ".join(result.unmatched_item_ids))
        sent_without_answer = batch.batch_move_ids.filtered(
            lambda l: l.state == 'sent')
        if sent_without_answer:
            summary += _("\nDocuments without a response item: %s") % (
                ", ".join(sent_without_answer.mapped('move_id.name')))

        batch._service_write_audit_fields({
            'state': end_state,
            'response_attachment_id': attachment.id,
            'response_sha256': hashlib.sha256(response).hexdigest(),
            'validation_summary': summary,
        })

        for line in batch.batch_move_ids:
            if line.state == 'accepted':
                self._write_move_mirror(line.move_id, {
                    'barani_pohoda_export_state': 'accepted',
                    'barani_pohoda_last_success_batch_id': batch.id,
                    'barani_pohoda_pohoda_record_id': line.pohoda_record_id or False,
                    'barani_pohoda_last_error': False,
                })
            elif line.state in ('warning', 'error'):
                self._write_move_mirror(line.move_id, {
                    'barani_pohoda_export_state': line.state,
                    'barani_pohoda_last_error': line.response_message or False,
                })
        return result

    # ------------------------------------------------------------------ cancel
    @api.model
    def action_cancel(self, batch):
        """Cancel a batch that has not been sent; reset the mirrors it set."""
        batch.ensure_one()
        self._check_manager()
        if batch.state not in ('draft', 'validated', 'xml_generated'):
            raise UserError(_(
                "A batch that was already sent to POHODA is an audit record and "
                "cannot be cancelled."))
        for line in batch.batch_move_ids:
            move = line.move_id
            if move.barani_pohoda_last_batch_id == batch and \
                    move.barani_pohoda_export_state != 'accepted':
                self._write_move_mirror(move, {
                    'barani_pohoda_export_state': 'not_exported',
                    'barani_pohoda_last_error': False,
                })
        batch._service_write_audit_fields({'state': 'cancelled'})
        return True

    @api.model
    def action_reset_to_draft(self, batch):
        """Back to draft (from validated only) so the scope can be changed."""
        batch.ensure_one()
        self._check_manager()
        self._require_state(batch, 'validated', _("Reset to draft"))
        batch._service_write_audit_fields({'state': 'draft'})
        return True

    # ----------------------------------------------------------------- helpers
    @api.model
    def _write_move_mirror(self, move, vals):
        """Scoped sudo write of the module-owned barani_pohoda_* mirror fields.

        Restricted to this module's fields by construction (assert, not trust);
        values are derived from module state, never from user input.
        """
        assert all(k.startswith('barani_pohoda_') for k in vals), \
            "move mirror writes are restricted to barani_pohoda_* fields"
        move.sudo().write(vals)

    @api.model
    def _source_hash(self, move):
        """Deterministic idempotency hash of the move's exportable content.

        Changes when anything the XML depends on changes (header refs, dates,
        amounts, line content); used by DOC 05 edge 14 to detect that a document
        differs from what POHODA accepted.
        """
        real = move.invoice_line_ids.filtered(
            lambda l: (not l.display_type) or l.display_type == 'product')
        parts = [
            move.name or '', str(move.move_type), str(move.partner_id.id),
            str(move.invoice_date or ''), str(move.invoice_date_due or ''),
            move.invoice_origin or '', move.payment_reference or '',
            str(move.currency_id.name or ''),
            '%.2f' % move.amount_untaxed, '%.2f' % move.amount_tax,
            '%.2f' % move.amount_total,
        ]
        for line in real.sorted('id'):
            parts.append('|'.join([
                str(line.id), line.name or '', '%.4f' % (line.quantity or 0.0),
                '%.4f' % (line.price_unit or 0.0), '%.2f' % line.price_subtotal,
                '%.2f' % line.price_total,
                (line.account_id.code or '') if line.account_id else '',
                ','.join(str(t) for t in sorted(line.tax_ids.ids)),
            ]))
        return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()

    @api.model
    def _mapping_version_hash(self, config):
        """Hash of the company's mapping matrix as of now (rules + cells)."""
        Rule = self.env['barani.pohoda.export.rule'].sudo()
        Cell = self.env['barani.pohoda.export.rule.mapping.cell'].sudo()
        rules = Rule.search([('company_id', '=', config.company_id.id)])
        cells = Cell.search([('rule_id', 'in', rules.ids)])
        parts = ['R|%s|%s|%s' % (r.id, r.active, r.write_date) for r in rules.sorted('id')]
        parts += ['C|%s|%s|%s' % (c.id, c.enabled_state, c.write_date)
                  for c in cells.sorted('id')]
        return hashlib.sha256('\n'.join(parts).encode('utf-8')).hexdigest()

    @api.model
    def _config_version_hash(self, config):
        return hashlib.sha256(
            ('%s|%s' % (config.id, config.write_date)).encode('utf-8')).hexdigest()

    @api.model
    def _validation_summary(self, n_total, n_blocked, blocker_counts):
        lines = [
            _("Documents collected: %s") % n_total,
            _("Exportable: %s") % (n_total - n_blocked),
            _("Blocked: %s") % n_blocked,
        ]
        for code in sorted(blocker_counts):
            lines.append("  %s: %s" % (code, blocker_counts[code]))
        return "\n".join(lines)
