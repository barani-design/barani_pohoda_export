# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# Apps-Store publication smoke tests: the listing page exists and respects the store
# constraints (no <script>, no external image sources), every referenced image is a
# real file in static/description, and the manifest's 'images' cover entries exist.

import ast
import os
import re

from odoo.modules.module import get_module_resource
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPublicationAssets(TransactionCase):

    def _read_index(self):
        path = get_module_resource('barani_pohoda_export', 'static/description', 'index.html')
        self.assertTrue(path, "static/description/index.html is missing")
        with open(path, 'r', encoding='utf-8') as handle:
            return path, handle.read()

    def _manifest(self):
        # Read the manifest directly (ast.literal_eval, like Odoo's own loader does)
        # instead of importing manifest helpers whose names changed across Odoo
        # versions (get_module_info exists only from Odoo 17 on).
        path = get_module_resource('barani_pohoda_export', '__manifest__.py')
        self.assertTrue(path, "__manifest__.py is missing")
        with open(path, 'r', encoding='utf-8') as handle:
            return ast.literal_eval(handle.read())

    def test_index_html_respects_store_constraints(self):
        _path, html = self._read_index()
        self.assertNotIn('<script', html.lower(),
                         "apps.odoo.com strips/forbids JavaScript in index.html")
        for src in re.findall(r'<img[^>]+src="([^"]+)"', html):
            self.assertFalse(src.startswith(('http://', 'https://', '//')),
                             "external image source not allowed: %s" % src)

    def test_index_images_exist(self):
        path, html = self._read_index()
        base = os.path.dirname(path)
        for src in re.findall(r'<img[^>]+src="([^"]+)"', html):
            self.assertTrue(os.path.exists(os.path.join(base, src)),
                            "index.html references a missing image: %s" % src)

    def test_manifest_images_exist(self):
        info = self._manifest()
        images = info.get('images') or []
        self.assertTrue(images, "manifest 'images' (Apps Store cover) is empty")
        for rel in images:
            self.assertTrue(
                get_module_resource('barani_pohoda_export', *rel.split('/')),
                "manifest 'images' path does not exist: %s" % rel)
