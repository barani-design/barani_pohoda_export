# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# Bucket C Step 4 — matrix-editor wiring smoke tests.
#
# The OWL component itself runs only in the web client (verified by click-test on the
# build); these tests pin everything the component depends on: the client action and
# its tag, the menu entry, the presence of the three asset files declared in the
# manifest, and the dictionary name_get contract ("CODE — label") the JS parses for
# the cell code display.

from odoo.modules.module import get_module_resource
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMatrixEditorWiring(TransactionCase):

    def test_client_action_and_tag(self):
        action = self.env.ref('barani_pohoda_export.action_pohoda_matrix_editor')
        self.assertEqual(action.type, 'ir.actions.client')
        self.assertEqual(action.tag, 'barani_pohoda_export.matrix_editor')

    def test_menu_points_to_action(self):
        menu = self.env.ref('barani_pohoda_export.menu_pohoda_matrix')
        action = self.env.ref('barani_pohoda_export.action_pohoda_matrix_editor')
        self.assertEqual(menu.action, action)
        self.assertEqual(
            menu.parent_id,
            self.env.ref('barani_pohoda_export.menu_pohoda_config'))

    def test_asset_files_exist(self):
        for fname in ('pohoda_matrix.js', 'pohoda_matrix.xml', 'pohoda_matrix.scss'):
            path = get_module_resource(
                'barani_pohoda_export', 'static/src/matrix', fname)
            self.assertTrue(path, "missing asset file: %s" % fname)

    def test_dictionary_name_get_contract(self):
        # The JS shows the dictionary CODE by splitting name_get on " — ".
        aa = self.env.ref('barani_pohoda_export.aa_1')
        display = aa.name_get()[0][1]
        self.assertTrue(display.startswith(aa.code + " — "),
                        "name_get no longer matches 'CODE — label': %r" % display)
