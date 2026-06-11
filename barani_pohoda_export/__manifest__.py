# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
{
    # ── Identity ──────────────────────────────────────────────────────────
    'name': "BARANI POHODA Export",
    'version': '16.0.1.11.4',
    'summary': "Export invoices, tax-config-driven advance invoices and settlements from Odoo to POHODA XML",
    'description': """
BARANI POHODA Export
====================
Exports BARANI customer invoices, tax-configuration-driven down-payment
(advance) invoices, settlement invoices and approved credit notes from Odoo 16
to POHODA XML.

Classifies and resolves document shape only from native Odoo accounting facts.
It does not depend on, import from, or call any DDS module at runtime.

Current code implements the full service core: data model, document classifier,
settlement advance-source resolver, mapping-matrix rule resolver, POHODA XML
builder, XSD validator, import-response parser, and the read-only export
preflight that sequences them and aggregates every blocker. The batch
lifecycle, export wizard and matrix editor follow; POHODA field details remain
import-test gated and there is no automatic POHODA send in Phase 1. All mapping
values are migration seeds requiring accountant verification and a POHODA
import test before production use. See README.rst for details.
""",
    'author': "BARANI DESIGN Technologies s. r. o.",
    'maintainer': "BARANI DESIGN Technologies s. r. o.",
    'website': "https://www.baranidesign.com",  # domain confirmed via sales@baranidesign.com contact
    'license': 'LGPL-3',
    'category': 'Accounting/Localizations',
    # Apps Store cover (hero); per convention the first 'images' entry / the
    # *_screenshot file under static/description/images is used as the thumbnail.
    'images': ['static/description/images/main_screenshot.png'],

    # ── Dependencies ──────────────────────────────────────────────────────
    # 'base' listed explicitly (publishing-checklist preference); 'account',
    # 'product' and 'mail' are also pulled transitively by 'sale_management'.
    'depends': [
        'base',
        'account',
        'sale_management',
        'product',
        'mail',
    ],
    # Forbidden runtime deps (must NEVER appear here): dds_down_payments_sk, dds_export_pohoda.

    'external_dependencies': {
        # Used by the DOC 04 XML builder / XSD validation. lxml ships with Odoo,
        # so this is effectively always satisfied; declared to document intent.
        'python': ['lxml'],
    },

    # ── Data files (loaded in dependency order: groups -> ACL -> infra -> seed -> views) ──
    'data': [
        # --- step 1.2: security (groups + ACLs); security.xml MUST load before the ACL CSV ---
        'security/security.xml',
        'security/ir.model.access.csv',
        # --- step 1.4: infrastructure data ---
        'data/sequences.xml',
        # --- step 1.6: seed data (dictionaries before initial_config: config refs the UN VAT code) ---
        'data/dictionaries.xml',
        'data/fiscal_profiles.xml',
        'data/initial_config.xml',
        # --- DOC 03: matrix seed (rules need config; cells need rules+profiles+dicts) ---
        'data/export_rules.xml',
        'data/mapping_cells.xml',
        # --- step 1.5: views & menu (menu.xml last: it references the actions above) ---
        'views/config_views.xml',
        'views/dictionaries_views.xml',
        'views/matrix_views.xml',
        'views/export_rule_views.xml',
        'views/export_batch_views.xml',
        'views/account_move_views.xml',
        'views/export_wizard_views.xml',   # wizard forms + the batch button that references them
        'views/menu.xml',
    ],
    # 'demo': [],  # No demo data in Phase 1.

    'assets': {
        # DOC 03 OWL matrix editor (client action). XML here are OWL templates.
        'web.assets_backend': [
            'barani_pohoda_export/static/src/matrix/pohoda_matrix.js',
            'barani_pohoda_export/static/src/matrix/pohoda_matrix.xml',
            'barani_pohoda_export/static/src/matrix/pohoda_matrix.scss',
        ],
    },

    # ── Behaviour ─────────────────────────────────────────────────────────
    'application': True,
    'installable': True,
    'auto_install': False,
}
