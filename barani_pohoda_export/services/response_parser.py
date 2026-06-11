# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 04 / Bucket B Step 4 — POHODA import response parser.
#
# After a human imports the generated dataPack into POHODA, POHODA writes a response
# file: a <rsp:responsePack> whose @id mirrors the request dataPack @id, wrapping one
# <rsp:responsePackItem> per document. Each item's @id mirrors the request
# dataPackItem @id (our builder sets that to "BPE<move.id>"), @state is "ok"/"error"
# (and "warning"), and @note carries the message; per-item detail children may carry
# further messages and, for a created record, a produced id/number.
# Confirmed against Stormware docs (xmlzpracovani, response.xsd) and a real sample:
#   <rsp:responsePack version="2.0" id="..." state="ok" note="" programVersion="...">
#     <rsp:responsePackItem version="2.0" id="BPE42" state="error" note="...">
#
# This service:
#   * parse(response)            -> ResponseParseResult   (pure; reads, writes nothing)
#   * apply_to_batch(batch, ...) -> ResponseParseResult   (writes per-move audit fields)
#
# Matching is responsePackItem @id -> export.batch.move.xml_item_id. apply_to_batch
# writes only the per-document (batch.move) fields, which are ungated; the batch-level
# state + response-attachment archival are the orchestration's job (Bucket C, where the
# batch's service-only write path lives). A malformed / non-responsePack payload yields
# BLOCK_RESPONSE_PARSE_FAILED. The exact produced-record-id element names are agenda /
# version dependent and IMPORT-TEST-GATED — isolated in the constants below.

from odoo import _, api, models
from odoo.exceptions import UserError

try:  # the manifest declares lxml in external_dependencies; guard import for safety.
    from lxml import etree
except ImportError:  # pragma: no cover
    etree = None

from ..models import constants as C

# Envelope local names. We match by LOCAL NAME (namespace-agnostic) so both the
# version_2 response.xsd and the non-versioned response.xsd parse.
_RESPONSE_PACK = 'responsePack'
_RESPONSE_PACK_ITEM = 'responsePackItem'

# POHODA item @state values, and the map to our batch.move.state.
_STATE_OK = 'ok'
_STATE_WARNING = 'warning'
_STATE_ERROR = 'error'
_STATE_TO_MOVE_STATE = {
    _STATE_OK: 'accepted',
    _STATE_WARNING: 'warning',
    _STATE_ERROR: 'error',
}

# IMPORT-TEST-GATED: descendant local-names that may carry the POHODA-assigned record id
# / number after a successful import, and the wrapper they live in. Isolated here so they
# are a one-line change once a real POHODA response is captured.
_PRODUCED_DETAILS_LOCALNAMES = ('producedDetails', 'importDetails')
_RECORD_ID_LOCALNAMES = ('id',)
_DOCUMENT_NUMBER_LOCALNAMES = ('number', 'numberRequested')
# Descendant local-names whose text is collected into the human-readable message.
_MESSAGE_LOCALNAMES = ('note', 'message', 'detail', 'statusText', 'state')


class ParsedResponseItem:
    """One responsePackItem outcome."""

    __slots__ = ('item_id', 'state', 'move_state', 'record_id', 'document_number',
                 'code', 'message')

    def __init__(self, item_id, state, record_id=None, document_number=None,
                 code=None, message=None):
        self.item_id = item_id
        self.state = state
        self.move_state = _STATE_TO_MOVE_STATE.get((state or '').lower(), 'warning')
        self.record_id = record_id
        self.document_number = document_number
        self.code = code
        self.message = message

    @property
    def is_ok(self):
        return (self.state or '').lower() == _STATE_OK

    @property
    def is_error(self):
        return (self.state or '').lower() == _STATE_ERROR

    def __repr__(self):
        return ('ParsedResponseItem(id=%s, state=%s, record_id=%s)'
                % (self.item_id, self.state, self.record_id))


class ResponseParseResult:
    """Outcome of parsing one POHODA response file."""

    __slots__ = ('ok', 'pack_state', 'items', 'unmatched_item_ids', 'errors', 'blockers')

    def __init__(self, ok, pack_state=None, items=None, unmatched_item_ids=None,
                 errors=None, blockers=None):
        self.ok = ok
        self.pack_state = pack_state
        self.items = items or []
        self.unmatched_item_ids = unmatched_item_ids or []
        self.errors = errors or []
        self.blockers = blockers or []

    @property
    def is_blocked(self):
        return bool(self.blockers)

    @property
    def all_ok(self):
        """True if parsing succeeded AND every item is 'ok'."""
        return self.ok and bool(self.items) and all(i.is_ok for i in self.items)

    def __repr__(self):
        return ('ResponseParseResult(ok=%s, pack_state=%s, items=%s, unmatched=%s, blockers=%s)'
                % (self.ok, self.pack_state, len(self.items),
                   len(self.unmatched_item_ids), self.blockers))


class PohodaResponseParser(models.AbstractModel):
    _name = 'barani.pohoda.response.parser'
    _description = "BARANI POHODA import response parser (DOC 04, Step 4)"

    # ------------------------------------------------------------------ public
    @api.model
    def parse(self, response):
        """Parse a POHODA import response (bytes or str) into per-item results.

        Pure: reads the XML, writes nothing. A parse error or a root that is not a
        ``responsePack`` yields ``BLOCK_RESPONSE_PARSE_FAILED``.
        """
        if etree is None:  # pragma: no cover
            raise UserError(_("The Python 'lxml' library is required to parse POHODA responses."))
        if isinstance(response, str):
            response = response.encode('utf-8')
        if not response:
            return self._fail(_("Empty POHODA response."))
        try:
            root = etree.fromstring(response)
        except Exception as e:
            return self._fail(_("Response XML could not be parsed: %s") % str(e)[:500])

        if self._local(root) != _RESPONSE_PACK:
            return self._fail(
                _("Root element is not a responsePack (got '%s').") % (self._local(root) or '?'))

        pack_state = root.get('state')
        items = [self._parse_item(el) for el in root.iter()
                 if self._local(el) == _RESPONSE_PACK_ITEM]
        return ResponseParseResult(ok=True, pack_state=pack_state, items=items)

    @api.model
    def apply_to_batch(self, batch, response=None, result=None):
        """Write per-item state/code/message + record id onto the batch's documents.

        Matches each responsePackItem @id to ``export.batch.move.xml_item_id`` and writes
        the (ungated) per-move audit fields + the move state (ok->accepted, warning->
        warning, error->error). Returns the :class:`ResponseParseResult` with
        ``unmatched_item_ids`` populated. The batch-level state and response-attachment
        archival are left to the orchestration (Bucket C).
        """
        if result is None:
            result = self.parse(response)
        if result.is_blocked:
            return result

        by_item = {m.xml_item_id: m for m in batch.batch_move_ids if m.xml_item_id}
        unmatched = []
        for item in result.items:
            line = by_item.get(item.item_id)
            if not line:
                unmatched.append(item.item_id)
                continue
            # batch.move rows are service-owned (ACL denies direct writes even to
            # managers); go through the model's narrow, group+company-checked path.
            line._service_write({
                'response_state': item.state or False,
                'response_code': item.code or False,
                'response_message': item.message or False,
                'pohoda_record_id': item.record_id or line.pohoda_record_id,
                'pohoda_document_number': (item.document_number
                                           or line.pohoda_document_number),
                'state': item.move_state,
            })
        result.unmatched_item_ids = unmatched
        return result

    # ----------------------------------------------------------------- helpers
    @api.model
    def _fail(self, error):
        return ResponseParseResult(
            ok=False, errors=[error], blockers=[C.BLOCK_RESPONSE_PARSE_FAILED])

    @staticmethod
    def _local(el):
        """Local name of an element, or '' for comments / processing instructions."""
        if not isinstance(getattr(el, 'tag', None), str):
            return ''
        return etree.QName(el).localname

    @api.model
    def _parse_item(self, item_el):
        item_id = item_el.get('id')
        state = item_el.get('state')
        code = item_el.get('code') or item_el.get('errno')

        # Message: the @note attribute plus any descendant message-element text.
        messages = []
        note_attr = item_el.get('note')
        if note_attr:
            messages.append(note_attr.strip())
        for el in item_el.iter():
            if el is item_el:
                continue
            if self._local(el) in _MESSAGE_LOCALNAMES:
                text = (el.text or '').strip()
                if text:
                    messages.append(text)
            note = el.get('note')
            if note and note.strip():
                messages.append(note.strip())
        # de-duplicate while preserving order
        seen = set()
        uniq = []
        for m in messages:
            if m not in seen:
                seen.add(m)
                uniq.append(m)
        message = ' | '.join(uniq) if uniq else None

        record_id, document_number = self._find_produced(item_el)
        return ParsedResponseItem(
            item_id=item_id, state=state, record_id=record_id,
            document_number=document_number, code=code, message=message)

    @api.model
    def _find_produced(self, item_el):
        """Best-effort extraction of the produced record id / document number.

        Scoped to a producedDetails / importDetails subtree to avoid false matches.
        Element names are IMPORT-TEST-GATED (see the constants above).
        """
        record_id = None
        document_number = None
        for el in item_el.iter():
            if self._local(el) not in _PRODUCED_DETAILS_LOCALNAMES:
                continue
            for sub in el.iter():
                ln = self._local(sub)
                text = (sub.text or '').strip()
                if not text:
                    continue
                if record_id is None and ln in _RECORD_ID_LOCALNAMES:
                    record_id = text
                elif document_number is None and ln in _DOCUMENT_NUMBER_LOCALNAMES:
                    document_number = text
            if record_id or document_number:
                break
        return record_id, document_number
