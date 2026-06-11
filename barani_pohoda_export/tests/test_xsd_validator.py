# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# Bucket B Step 3 — XSD validator (acceptance test A17).
#
# These exercise the validator MECHANISM against a tiny self-contained XSD (single
# .xsd and a .zip set), plus the skip-when-unconfigured path. Validating real builder
# output against the actual Stormware POHODA XSD set is the live import-test gate
# (A17) and is performed once BARANI supplies the schema set; that is flagged, not
# faked here.

import io
import zipfile

from odoo.tests import TransactionCase, tagged

from ..models import constants as C

# A tiny self-contained XSD: <t:root version="..."> with one or more <item> children.
# elementFormDefault defaults to 'unqualified' => root is namespaced, item is not.
MINIMAL_XSD = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           targetNamespace="urn:barani:test" xmlns:t="urn:barani:test">
  <xs:element name="root">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="item" type="xs:string" maxOccurs="unbounded"/>
      </xs:sequence>
      <xs:attribute name="version" type="xs:string" use="required"/>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""

GOOD_XML = b'<t:root xmlns:t="urn:barani:test" version="2.0"><item>x</item></t:root>'
# missing the required @version AND an undeclared child element
BAD_XML = b'<t:root xmlns:t="urn:barani:test"><nope>x</nope></t:root>'
MALFORMED_XML = b'<t:root xmlns:t="urn:barani:test"><item>x</item>'  # unclosed root


@tagged('post_install', '-at_install')
class TestXsdValidator(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.validator = cls.env['barani.pohoda.xsd.validator']
        Att = cls.env['ir.attachment']
        cls.xsd_att = Att.create({'name': 'pohoda_min.xsd', 'raw': MINIMAL_XSD})
        cls.broken_att = Att.create({'name': 'broken.xsd', 'raw': b'<xs:schema not xsd'})

        # A .zip of the schema set whose root is named data.xsd (POHODA convention).
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('data.xsd', MINIMAL_XSD.decode('utf-8'))
        cls.zip_att = Att.create({'name': 'pohoda_xsd_set.zip', 'raw': buf.getvalue()})

        cls.config = cls.env.ref('barani_pohoda_export.config_pohoda')

    # --- valid / invalid / malformed ------------------------------------------
    def test_valid_xml_passes(self):
        res = self.validator.validate(GOOD_XML, schema=self.xsd_att)
        self.assertTrue(res.ok)
        self.assertTrue(res.schema_present)
        self.assertFalse(res.is_blocked)
        self.assertEqual(res.errors, [])

    def test_invalid_xml_blocks(self):
        res = self.validator.validate(BAD_XML, schema=self.xsd_att)
        self.assertFalse(res.ok)
        self.assertIn(C.BLOCK_XML_SCHEMA_VALIDATION_FAILED, res.blockers)
        self.assertTrue(res.errors)

    def test_malformed_xml_blocks(self):
        res = self.validator.validate(MALFORMED_XML, schema=self.xsd_att)
        self.assertFalse(res.ok)
        self.assertIn(C.BLOCK_XML_SCHEMA_VALIDATION_FAILED, res.blockers)

    # --- skip when no schema configured (Phase 1: human import is the gate) ----
    def test_no_schema_skips(self):
        res = self.validator.validate(GOOD_XML)  # no schema, no config
        self.assertTrue(res.ok)
        self.assertFalse(res.schema_present)
        self.assertFalse(res.is_blocked)

    # --- schema resolved from config.xsd_schema_set_id ------------------------
    def test_config_supplies_schema(self):
        self.config.xsd_schema_set_id = self.xsd_att
        res = self.validator.validate(GOOD_XML, config=self.config)
        self.assertTrue(res.ok)
        self.assertTrue(res.schema_present)

    # --- a .zip schema set (extracted, root data.xsd, imports would resolve) --
    def test_zip_schema_set(self):
        ok = self.validator.validate(GOOD_XML, schema=self.zip_att)
        self.assertTrue(ok.ok)
        self.assertFalse(ok.is_blocked)
        bad = self.validator.validate(BAD_XML, schema=self.zip_att)
        self.assertFalse(bad.ok)
        self.assertIn(C.BLOCK_XML_SCHEMA_VALIDATION_FAILED, bad.blockers)

    # --- a broken XSD set is a failure, not a silent pass ---------------------
    def test_broken_xsd_blocks(self):
        res = self.validator.validate(GOOD_XML, schema=self.broken_att)
        self.assertFalse(res.ok)
        self.assertIn(C.BLOCK_XML_SCHEMA_VALIDATION_FAILED, res.blockers)
