# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 04 / Bucket B Step 3 — POHODA XSD validator.
#
# Validates the XML produced by the DOC 04 builder against the configured POHODA XSD
# schema set. The schema set is an uploaded ir.attachment referenced by
# ``config.xsd_schema_set_id`` and may be either:
#   * a single self-contained ``.xsd`` file, or
#   * a ``.zip`` of the Stormware schema set (data.xsd + invoice.xsd + type.xsd + ...).
#     The zip is extracted to a temp dir so the schemas' relative <xsd:import>
#     schemaLocation references resolve; the root schema (data.xsd by convention) is
#     loaded from that dir.
#
# Behaviour (acceptance test A17):
#   * No XSD configured  -> validation is SKIPPED (schema_present=False, ok=True, no
#     blocker). Phase 1 has no automatic POHODA send and the human POHODA import is the
#     real gate; a production go-live gate (DOC 05) can separately *require* the XSD.
#   * XSD configured, XML valid    -> ok=True, no blocker.
#   * XSD configured, XML invalid  -> ok=False, errors=<schema log>,
#                                     blockers=[BLOCK_XML_SCHEMA_VALIDATION_FAILED].
#   * XSD cannot be loaded / XML cannot be parsed -> ok=False (cannot confirm validity =>
#     do not export), same blocker, with the reason in ``errors``.
#
# Read-only / pure: reads the attachment + validates in memory (uses a temp dir only to
# resolve multi-file schema imports, cleaned up in a finally). Writes nothing, sends
# nothing.

import base64
import os
import shutil
import tempfile

from odoo import _, api, models
from odoo.exceptions import UserError

try:  # the manifest declares lxml in external_dependencies; guard import for safety.
    from lxml import etree
except ImportError:  # pragma: no cover
    etree = None

from ..models import constants as C

# Root schema filename by convention for the POHODA v2 set (the dataPack schema).
_ROOT_SCHEMA_CANDIDATES = ('data.xsd',)
_ZIP_MAGIC = b'PK\x03\x04'


class XsdValidationResult:
    """Outcome of validating one XML payload against the configured XSD set.

    Attributes:
        ok:             True if the XML validated, OR validation was skipped because no
                        XSD set is configured (see ``schema_present``).
        schema_present: True if an XSD set was configured and used; False => skipped.
        errors:         list of human-readable error strings (schema log / load / parse
                        errors). Empty on success or skip.
        blockers:       [BLOCK_XML_SCHEMA_VALIDATION_FAILED] on failure, else [].
    """

    __slots__ = ('ok', 'schema_present', 'errors', 'blockers')

    def __init__(self, ok, schema_present=True, errors=None, blockers=None):
        self.ok = ok
        self.schema_present = schema_present
        self.errors = errors or []
        self.blockers = blockers or []

    @property
    def is_blocked(self):
        return bool(self.blockers)

    def __repr__(self):
        return ('XsdValidationResult(ok=%s, schema_present=%s, errors=%s, blockers=%s)'
                % (self.ok, self.schema_present, len(self.errors), self.blockers))


class PohodaXsdValidator(models.AbstractModel):
    _name = 'barani.pohoda.xsd.validator'
    _description = "BARANI POHODA XSD validator (DOC 04, Step 3)"

    # ------------------------------------------------------------------ public
    @api.model
    def validate(self, xml_bytes, config=None, schema=None):
        """Validate ``xml_bytes`` against the configured POHODA XSD set.

        :param xml_bytes: the builder output (bytes) or an XML string.
        :param config:    a ``barani.pohoda.export.config`` record; its
                          ``xsd_schema_set_id`` supplies the schema when ``schema`` is
                          not given.
        :param schema:    an explicit ``ir.attachment`` holding the XSD (or .zip set),
                          overriding the config.
        :returns:         :class:`XsdValidationResult`.
        """
        if etree is None:  # pragma: no cover
            raise UserError(_("The Python 'lxml' library is required to validate POHODA XML."))

        attachment = schema or (config.xsd_schema_set_id if config else False)
        if not attachment:
            # No XSD configured -> skip (not a block). Flagged via schema_present=False.
            return XsdValidationResult(ok=True, schema_present=False)

        # Parse the XML payload first (a parse error is itself a validation failure).
        if isinstance(xml_bytes, str):
            xml_bytes = xml_bytes.encode('utf-8')
        try:
            doc = etree.fromstring(xml_bytes)
        except Exception as e:  # malformed XML => cannot be valid
            return self._fail(_("XML could not be parsed: %s") % str(e)[:500])

        # Build the XMLSchema from the attachment (single .xsd or .zip set).
        tmpdir = None
        try:
            schema_obj, tmpdir, load_error = self._load_schema(attachment)
            if load_error:
                return self._fail(_("XSD schema set could not be loaded: %s") % load_error)
            if schema_obj.validate(doc):
                return XsdValidationResult(ok=True, schema_present=True)
            errors = [str(err) for err in schema_obj.error_log]
            return self._fail(errors or [_("XML did not validate against the XSD set.")])
        finally:
            if tmpdir:
                shutil.rmtree(tmpdir, ignore_errors=True)

    # ----------------------------------------------------------------- helpers
    @api.model
    def _fail(self, errors):
        """Build a failed result carrying BLOCK_XML_SCHEMA_VALIDATION_FAILED."""
        if isinstance(errors, str):
            errors = [errors]
        return XsdValidationResult(
            ok=False, schema_present=True, errors=errors,
            blockers=[C.BLOCK_XML_SCHEMA_VALIDATION_FAILED])

    @api.model
    def _attachment_bytes(self, attachment):
        """Raw bytes of an ir.attachment (raw in Odoo 16; base64 datas fallback)."""
        raw = getattr(attachment, 'raw', False)
        if raw:
            return raw
        if attachment.datas:
            return base64.b64decode(attachment.datas)
        return b''

    @api.model
    def _load_schema(self, attachment):
        """Return ``(XMLSchema | None, tmpdir | None, error_str | None)``.

        A .zip set is extracted to a temp dir (so <xsd:import> schemaLocation paths
        resolve) and the root schema (data.xsd) is parsed from there; a single .xsd is
        parsed from its bytes. The caller removes ``tmpdir``.
        """
        data = self._attachment_bytes(attachment)
        if not data:
            return None, None, _("the schema attachment is empty")

        is_zip = data[:4] == _ZIP_MAGIC or (attachment.name or '').lower().endswith('.zip')
        if is_zip:
            return self._load_schema_from_zip(data)
        try:
            schema_doc = etree.fromstring(data)
            return etree.XMLSchema(schema_doc), None, None
        except Exception as e:
            # A single .xsd with unresolved external imports lands here too.
            return None, None, str(e)[:500]

    @api.model
    def _load_schema_from_zip(self, data):
        """Extract a zip of the XSD set and load the root schema with imports resolving."""
        import io
        import zipfile
        tmpdir = tempfile.mkdtemp(prefix='barani_pohoda_xsd_')
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                # Guard against path traversal in member names.
                for member in zf.namelist():
                    target = os.path.normpath(os.path.join(tmpdir, member))
                    if not target.startswith(os.path.abspath(tmpdir) + os.sep) \
                            and target != os.path.abspath(tmpdir):
                        return None, tmpdir, _("unsafe path in schema zip: %s") % member
                zf.extractall(tmpdir)
            root = self._find_root_schema(tmpdir)
            if not root:
                return None, tmpdir, _("no .xsd file found in the schema zip")
            schema_doc = etree.parse(root)  # base_url = root path => imports resolve
            return etree.XMLSchema(schema_doc), tmpdir, None
        except Exception as e:
            return None, tmpdir, str(e)[:500]

    @api.model
    def _find_root_schema(self, tmpdir):
        """Locate the root .xsd in an extracted set (prefer data.xsd, else any .xsd)."""
        found = []
        for base, _dirs, files in os.walk(tmpdir):
            for f in files:
                if f.lower().endswith('.xsd'):
                    found.append(os.path.join(base, f))
        if not found:
            return None
        for cand in _ROOT_SCHEMA_CANDIDATES:
            for path in found:
                if os.path.basename(path).lower() == cand:
                    return path
        return sorted(found)[0]
