# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# DOC 03 acceptance tests for the mapping-matrix rule resolver (Bucket B, Step 1).
#
# These build a small live matrix on top of the seeded config: a few ACTIVE rules
# (the seed ships its template rules inactive) and their cells, plus fiscal profiles
# linked to throwaway Odoo fiscal positions, then assert the resolver picks the right
# profile (column), rule (row) and cell, reads the right code off the right field, and
# emits the correct mapping blockers. Invoices are left in draft: the resolver, like
# the classifier and source resolver, reads native fields and is posting-agnostic.
#
# Fixtures reuse the seeded config (config_pohoda) and align the move company to it so
# the single-active-config-per-company invariant is respected and rule/move companies
# match. Lines set product_id (for rule matching) AND an explicit account_id /
# price_unit, which are re-pinned after create to guard against product-driven
# recompute (so the classifier's 324-prefix + price_subtotal-sign split is deterministic).

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..models import constants as C


@tagged('post_install', '-at_install')
class TestRuleResolver(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Reuse the seeded config; align the move company to it (guarantees rule
        # company == move company and the one-active-config invariant holds).
        cls.config = cls.env.ref('barani_pohoda_export.config_pohoda')
        cls.company = cls.config.company_id
        cls.partner = cls.env['res.partner'].create({'name': 'BPE RR Customer'})
        cls.classifier = cls.env['barani.pohoda.document.classifier']
        cls.resolver = cls.env['barani.pohoda.rule.resolver']

        Account = cls.env['account.account']
        cls.income_account = Account.search(
            [('account_type', '=', 'income'), ('company_id', '=', cls.company.id)],
            limit=1)
        if not cls.income_account:
            cls.income_account = Account.create({
                'name': 'BPE RR Income', 'code': 'BPERRI',
                'account_type': 'income', 'company_id': cls.company.id,
            })
        # Advances are identified by the classifier purely on the '324' code prefix.
        cls.advance_account = Account.search(
            [('code', '=like', '324%'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.advance_account:
            cls.advance_account = Account.create({
                'name': 'BPE RR Advances Received', 'code': '324999',
                'account_type': 'liability_current', 'company_id': cls.company.id,
            })

        Journal = cls.env['account.journal']
        cls.sale_journal = Journal.search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.sale_journal:
            cls.sale_journal = Journal.create({
                'name': 'BPE RR Sales', 'code': 'BPERRS', 'type': 'sale',
                'company_id': cls.company.id,
            })
        # A purchase journal is needed to build an 'unsupported' (vendor bill) move:
        # Odoo's _check_journal_move_type rejects a purchase move in a sale journal.
        cls.purchase_journal = Journal.search(
            [('type', '=', 'purchase'), ('company_id', '=', cls.company.id)], limit=1)
        if not cls.purchase_journal:
            cls.purchase_journal = Journal.create({
                'name': 'BPE RR Purchases', 'code': 'BPERRP', 'type': 'purchase',
                'company_id': cls.company.id,
            })

        # A 23% sale VAT tax, to build TAXABLE lines for the Architecture A v3
        # taxable-advance / taxable-deduction-UN guard tests. Rate value is not asserted.
        Tax = cls.env['account.tax']
        cls.tax_vat = Tax.search(
            [('type_tax_use', '=', 'sale'), ('amount', '>', 0),
             ('company_id', '=', cls.company.id)], limit=1)
        if not cls.tax_vat:
            cls.tax_vat = Tax.create({
                'name': 'BPE RR VAT', 'amount': 23.0, 'amount_type': 'percent',
                'type_tax_use': 'sale', 'price_include': False,
                'company_id': cls.company.id,
            })

        # Fiscal positions (company-scoped) and fiscal profiles (global) linked to them.
        FPos = cls.env['account.fiscal.position']
        cls.fpos = FPos.create({'name': 'BPE RR FPos Domestic', 'company_id': cls.company.id})
        cls.fpos_oss = FPos.create({'name': 'BPE RR FPos OSS', 'company_id': cls.company.id})
        cls.fpos_unmapped = FPos.create(
            {'name': 'BPE RR FPos Unmapped', 'company_id': cls.company.id})

        Profile = cls.env['barani.pohoda.fiscal.profile']
        cls.profile = Profile.create({
            'name': 'BPE RR Domestic', 'geography': 'domestic',
            'customer_tax_status': 'any', 'is_oss': False,
            'account_fiscal_position_ids': [(6, 0, [cls.fpos.id])],
        })
        cls.profile_oss = Profile.create({
            'name': 'BPE RR OSS', 'geography': 'oss',
            'customer_tax_status': 'no_vat_id', 'is_oss': True,
            'account_fiscal_position_ids': [(6, 0, [cls.fpos_oss.id])],
        })

        # Seeded controlled-vocabulary codes (global).
        def ref(xmlid):
            return cls.env.ref('barani_pohoda_export.%s' % xmlid)
        cls.aa1, cls.aa2, cls.aa3 = ref('aa_1'), ref('aa_2'), ref('aa_3')
        cls.vat_ud, cls.vat_un = ref('vat_ud'), ref('vat_un')
        cls.kv_d2, cls.kv_kn = ref('kv_d2'), ref('kv_kn')
        cls.moss_gd = ref('moss_gd')

        Rule = cls.env['barani.pohoda.export.rule']
        Cell = cls.env['barani.pohoda.export.rule.mapping.cell']

        # --- down-payment rule (Odpočet zálohy analog): product match, sequence 1 ---
        cls.dp_product = cls.env['product.product'].create(
            {'name': 'BPE RR Down payment', 'type': 'service'})
        cls.rule_dp = Rule.create({
            'config_id': cls.config.id, 'sequence': 1, 'name': 'RR Odpocet zalohy',
            'match_mode': 'product', 'product_ids': [(6, 0, [cls.dp_product.id])],
            'active': True,
        })
        # account_assignment = aa1 ('1'); downpayment = aa2 ('2') -> DIFFERENT, so the
        # tests can prove the resolver reads the role-appropriate field.
        cls.cell_dp = Cell.create({
            'rule_id': cls.rule_dp.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'invoice', 'enabled_state': 'active',
            'account_assignment_id': cls.aa1.id,
            'downpayment_account_assignment_id': cls.aa1.id,
            'vat_classification_id': cls.vat_ud.id,
            'control_statement_code_id': cls.kv_d2.id,
        })
        cls.cell_dp_ded = Cell.create({
            'rule_id': cls.rule_dp.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'down_payment_deduction', 'enabled_state': 'active',
            # Different from invoice cell so the test proves document_kind drives lookup.
            'account_assignment_id': cls.aa2.id,
            'downpayment_account_assignment_id': cls.aa2.id,
            'vat_classification_id': cls.vat_un.id,
            'control_statement_code_id': cls.kv_kn.id,
        })

        # A second domestic profile whose down-payment ADVANCE (invoice-kind) cell still
        # carries UN — a deliberate misconfiguration to exercise the taxable-advance-UN
        # guard (a taxable advance mapped to UN must block).
        cls.fpos_un = FPos.create(
            {'name': 'BPE RR FPos UN-adv', 'company_id': cls.company.id})
        cls.profile_un = Profile.create({
            'name': 'BPE RR UN-adv', 'geography': 'domestic',
            'customer_tax_status': 'any', 'is_oss': False,
            'account_fiscal_position_ids': [(6, 0, [cls.fpos_un.id])],
        })
        cls.cell_dp_un_adv = Cell.create({
            'rule_id': cls.rule_dp.id, 'fiscal_profile_id': cls.profile_un.id,
            'document_kind': 'invoice', 'enabled_state': 'active',
            'account_assignment_id': cls.aa1.id,
            'downpayment_account_assignment_id': cls.aa1.id,
            'vat_classification_id': cls.vat_un.id,
            'control_statement_code_id': cls.kv_kn.id,
        })

        # --- goods rule (Výrobky analog): category match, sequence 4 ---
        cls.cat_goods = cls.env['product.category'].create({'name': 'BPE RR Goods'})
        cls.goods_product = cls.env['product.product'].create(
            {'name': 'BPE RR Widget', 'type': 'consu', 'categ_id': cls.cat_goods.id})
        cls.rule_goods = Rule.create({
            'config_id': cls.config.id, 'sequence': 4, 'name': 'RR Vyrobky',
            'match_mode': 'category', 'category_ids': [(6, 0, [cls.cat_goods.id])],
            'active': True,
        })
        cls.cell_goods = Cell.create({
            'rule_id': cls.rule_goods.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'invoice', 'enabled_state': 'active',
            'account_assignment_id': cls.aa1.id,
            'downpayment_account_assignment_id': cls.aa1.id,
            'vat_classification_id': cls.vat_ud.id,
            'control_statement_code_id': cls.kv_d2.id,
        })

        # --- residual services rule (Služby analog): category, sequence 6, also
        #     matches the goods product explicitly -> used by the rule-order test. ---
        cls.cat_services = cls.env['product.category'].create({'name': 'BPE RR Services'})
        cls.rule_services = Rule.create({
            'config_id': cls.config.id, 'sequence': 6, 'name': 'RR Sluzby',
            'match_mode': 'category', 'category_ids': [(6, 0, [cls.cat_services.id])],
            'residual_only': True, 'also_match_products': True,
            'product_ids': [(6, 0, [cls.goods_product.id])], 'active': True,
        })

        # --- repairs rule (name carries the 'repair' marker): category, OSS cell blocked ---
        cls.cat_repairs = cls.env['product.category'].create({'name': 'BPE RR Repairs'})
        cls.repair_product = cls.env['product.product'].create(
            {'name': 'BPE RR Repair svc', 'type': 'service', 'categ_id': cls.cat_repairs.id})
        cls.rule_repairs = Rule.create({
            'config_id': cls.config.id, 'sequence': 3, 'name': 'RR Repairs and Calibration',
            'match_mode': 'category', 'category_ids': [(6, 0, [cls.cat_repairs.id])],
            'active': True,
        })
        cls.cell_repairs_oss = Cell.create({
            'rule_id': cls.rule_repairs.id, 'fiscal_profile_id': cls.profile_oss.id,
            'document_kind': 'invoice', 'enabled_state': 'blocked',
        })

        # --- scenario product rules for the blocker-path tests ---
        cls.prod_blocked, cls.rule_blocked, _ = cls._scenario(
            'Blocked', cls.profile, seq=51, state='blocked')
        cls.prod_review, cls.rule_review, _ = cls._scenario(
            'Review', cls.profile, seq=52, state='review_required')
        # active cell deliberately missing its VAT classification -> required-code-missing
        cls.prod_incomplete, cls.rule_incomplete, _ = cls._scenario(
            'Incomplete', cls.profile, seq=53, state='active',
            account=cls.aa1, kv=cls.kv_d2)
        # active OSS cell deliberately missing its MOSS type -> required-code-missing
        cls.prod_oss_incomplete, cls.rule_oss_incomplete, _ = cls._scenario(
            'OSS incomplete', cls.profile_oss, seq=54, state='active',
            account=cls.aa3, vat=cls.vat_un, kv=cls.kv_kn)
        # rule matches but no cell at all -> cell-missing
        cls.prod_nocell, cls.rule_nocell, _ = cls._scenario(
            'No cell', cls.profile, seq=55, make_cell=False)
        # a product with no active rule whatsoever -> cell-missing (no rule)
        cls.prod_unmatched = cls.env['product.product'].create(
            {'name': 'BPE RR Unmatched', 'type': 'service'})

        # An archived config with an active matching rule must NOT leak into resolution.
        cls.prod_archived = cls.env['product.product'].create(
            {'name': 'BPE RR Archived-rule prod', 'type': 'service'})
        cls.archived_config = cls.env['barani.pohoda.export.config'].create({
            'name': 'BPE RR Archived config',
            'company_id': cls.company.id,
            'active': False,
            'document_header_vat_classification_id': cls.vat_un.id,
        })
        cls.rule_archived = Rule.create({
            'config_id': cls.archived_config.id, 'sequence': 0,
            'name': 'RR Archived Rule Should Not Match',
            'match_mode': 'product', 'product_ids': [(6, 0, [cls.prod_archived.id])],
            'active': True,
        })
        Cell.create({
            'rule_id': cls.rule_archived.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'invoice', 'enabled_state': 'active',
            'account_assignment_id': cls.aa1.id,
            'vat_classification_id': cls.vat_ud.id,
            'control_statement_code_id': cls.kv_d2.id,
        })

    # --------------------------------------------------------------- fixtures
    @classmethod
    def _scenario(cls, name, profile, seq=50, state='active', account=None,
                  dp_account=None, vat=None, kv=None, moss=None, make_cell=True):
        """Create a (product, product-rule, optional cell) triple for a blocker path.

        Each scenario product is matched only by its own product rule (deterministic),
        so the test moves stay simple. ``make_cell=False`` leaves the rule cell-less.
        """
        prod = cls.env['product.product'].create(
            {'name': 'BPE RR %s prod' % name, 'type': 'service'})
        rule = cls.env['barani.pohoda.export.rule'].create({
            'config_id': cls.config.id, 'sequence': seq, 'name': 'RR %s' % name,
            'match_mode': 'product', 'product_ids': [(6, 0, [prod.id])], 'active': True,
        })
        cell = cls.env['barani.pohoda.export.rule.mapping.cell'].browse()
        if make_cell:
            vals = {
                'rule_id': rule.id, 'fiscal_profile_id': profile.id,
                'document_kind': 'invoice', 'enabled_state': state,
            }
            if account:
                vals['account_assignment_id'] = account.id
            if dp_account:
                vals['downpayment_account_assignment_id'] = dp_account.id
            if vat:
                vals['vat_classification_id'] = vat.id
            if kv:
                vals['control_statement_code_id'] = kv.id
            if moss:
                vals['moss_service_type_id'] = moss.id
            cell = cls.env['barani.pohoda.export.rule.mapping.cell'].create(vals)
        return prod, rule, cell

    def _move(self, lines, move_type='out_invoice', fpos=None):
        """Create a DRAFT move. Each ``lines`` spec: product / account / price_unit /
        (quantity) / (taxes) / (name). ``fpos`` sets the move's fiscal position.

        After create, the product line accounts and unit prices are re-pinned to the
        requested values so the classifier's 324-prefix + price_subtotal-sign split is
        deterministic regardless of any product-driven recompute on create.
        """
        commands = []
        for spec in lines:
            commands.append((0, 0, {
                'name': spec.get('name', 'line'),
                'product_id': spec['product'].id,
                'account_id': spec['account'].id,
                'price_unit': spec['price_unit'],
                'quantity': spec.get('quantity', 1.0),
                'tax_ids': [(6, 0, [t.id for t in spec.get('taxes', [])])],
            }))
        journal = (self.purchase_journal
                   if move_type in ('in_invoice', 'in_refund')
                   else self.sale_journal)
        vals = {
            'move_type': move_type,
            'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(),
            'journal_id': journal.id,
            'invoice_line_ids': commands,
        }
        if fpos is not None:
            vals['fiscal_position_id'] = fpos.id
        move = self.env['account.move'].create(vals)
        product_lines = move.invoice_line_ids.filtered(
            lambda l: not l.display_type or l.display_type == 'product')
        for line, spec in zip(product_lines, lines):
            pin = {}
            if line.account_id.id != spec['account'].id:
                pin['account_id'] = spec['account'].id
            if line.price_unit != spec['price_unit']:
                pin['price_unit'] = spec['price_unit']
            if pin:
                line.write(pin)
        return move

    # ------------------------------------------------------- profile resolution
    def test_profile_resolves_from_fiscal_position(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'regular_invoice')
        self.assertEqual(res.fiscal_profile, self.profile)
        self.assertNotIn(C.BLOCK_MAPPING_PROFILE_NOT_FOUND, res.blockers)

    def test_unknown_fiscal_position_blocks_profile_not_found(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0}], fpos=self.fpos_unmapped)
        res = self.resolver.resolve(move)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_MAPPING_PROFILE_NOT_FOUND, res.blockers)
        self.assertFalse(res.fiscal_profile)
        # No line resolution is attempted without a profile (no matrix column).
        self.assertEqual(len(res.lines), 0)

    def test_no_fiscal_position_blocks_profile_not_found(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0}])  # no fiscal position
        res = self.resolver.resolve(move)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_MAPPING_PROFILE_NOT_FOUND, res.blockers)

    # ----------------------------------------------------------- happy paths
    def test_regular_invoice_resolves_supply_codes(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertFalse(res.is_blocked)
        self.assertTrue(res.is_resolved)
        self.assertEqual(len(res.lines), 1)
        ln = res.lines[0]
        self.assertEqual(ln.role, 'supply')
        self.assertEqual(ln.document_kind, 'invoice')
        self.assertEqual(ln.rule, self.rule_goods)
        self.assertEqual(ln.account_code, '1')   # account_assignment (aa1)
        self.assertEqual(ln.vat_code, 'UD')
        self.assertEqual(ln.kv_code, 'D2')
        self.assertTrue(ln.is_resolved)

    def test_advance_invoice_reads_account_assignment(self):
        move = self._move(
            [{'account': self.advance_account, 'product': self.dp_product,
              'price_unit': 5.0, 'name': 'Down payment'}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'advance_invoice')
        self.assertFalse(res.is_blocked)
        self.assertEqual(len(res.lines), 1)
        ln = res.lines[0]
        self.assertEqual(ln.role, 'advance')
        self.assertEqual(ln.rule, self.rule_dp)
        # A standalone advance line uses the invoice document-kind cell.
        self.assertEqual(ln.document_kind, 'invoice')
        self.assertEqual(ln.account_code, '1')

    def test_settlement_deduction_reads_downpayment_account(self):
        move = self._move([
            {'account': self.income_account, 'product': self.goods_product,
             'price_unit': 20.0, 'name': 'Supply'},
            {'account': self.advance_account, 'product': self.dp_product,
             'price_unit': -5.0, 'name': 'Advance deduction'},
        ], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'settlement_invoice')
        self.assertFalse(res.is_blocked)
        self.assertEqual(len(res.lines), 2)
        by_role = {ln.role: ln for ln in res.lines}
        self.assertIn('supply', by_role)
        self.assertIn('deduction', by_role)
        # Supply reads its goods cell's account_assignment.
        self.assertEqual(by_role['supply'].rule, self.rule_goods)
        self.assertEqual(by_role['supply'].account_code, '1')
        # The deduction matches the down-payment rule but uses the explicit
        # down_payment_deduction cell, not the standalone-advance invoice cell.
        ded = by_role['deduction']
        self.assertEqual(ded.rule, self.rule_dp)
        self.assertEqual(ded.document_kind, 'down_payment_deduction')
        self.assertEqual(ded.cell, self.cell_dp_ded)
        self.assertEqual(ded.account_code, '2')
        self.assertEqual(ded.vat_code, 'UN')

    # --- Architecture A v3: taxable advance / deduction must not be 'UN' -----------
    def test_taxable_advance_deduction_un_blocks(self):
        # A settlement whose negative-324 deduction is TAXABLE (23%) but resolves to a
        # 'UN' down_payment_deduction cell must block (UN would re-declare the full
        # supply VAT instead of netting the already-declared advance VAT). A04/A11.
        move = self._move([
            {'account': self.income_account, 'product': self.goods_product,
             'price_unit': 20.0, 'taxes': [self.tax_vat], 'name': 'Supply'},
            {'account': self.advance_account, 'product': self.dp_product,
             'price_unit': -5.0, 'taxes': [self.tax_vat], 'name': 'Advance deduction'},
        ], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'settlement_invoice')
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_TAXABLE_ADVANCE_DEDUCTION_CLASSIFICATION_UN, res.blockers)
        # The supply line (role 'supply') is taxable too but is NOT caught by the guard.
        self.assertNotIn(C.BLOCK_TAXABLE_ADVANCE_CLASSIFICATION_UN, res.blockers)

    def test_taxable_advance_un_blocks(self):
        # A standalone DPI (advance_invoice) whose positive-324 line is TAXABLE (23%)
        # but resolves to a 'UN' advance cell must block. A04.
        move = self._move([
            {'account': self.advance_account, 'product': self.dp_product,
             'price_unit': 5.0, 'taxes': [self.tax_vat], 'name': 'Down payment'},
        ], fpos=self.fpos_un)
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'advance_invoice')
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_TAXABLE_ADVANCE_CLASSIFICATION_UN, res.blockers)

    def test_taxable_advance_ud_does_not_block(self):
        # The same taxable DPI under the domestic profile whose advance cell carries the
        # output-VAT code 'UD' must NOT raise the taxable-UN guard (no over-firing).
        move = self._move([
            {'account': self.advance_account, 'product': self.dp_product,
             'price_unit': 5.0, 'taxes': [self.tax_vat], 'name': 'Down payment'},
        ], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'advance_invoice')
        self.assertNotIn(C.BLOCK_TAXABLE_ADVANCE_CLASSIFICATION_UN, res.blockers)
        self.assertNotIn(C.BLOCK_TAXABLE_ADVANCE_DEDUCTION_CLASSIFICATION_UN, res.blockers)
        self.assertFalse(res.is_blocked)

    def test_rule_order_specific_beats_residual(self):
        # The goods product matches rule_goods (seq 4, category) AND rule_services
        # (seq 6, residual, also_match_products) -> the lower-sequence specific rule wins.
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertEqual(res.lines[0].rule, self.rule_goods)

    # ------------------------------------------------------------- blockers
    def test_no_matching_rule_blocks_cell_missing(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.prod_unmatched,
              'price_unit': 20.0}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_MAPPING_CELL_MISSING, res.blockers)
        self.assertFalse(res.lines[0].rule)

    def test_missing_cell_blocks(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.prod_nocell,
              'price_unit': 20.0}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_MAPPING_CELL_MISSING, res.blockers)
        self.assertEqual(res.lines[0].rule, self.rule_nocell)  # rule matched...
        self.assertFalse(res.lines[0].cell)                    # ...but no cell

    def test_missing_down_payment_deduction_cell_blocks_settlement_deduction(self):
        # A settlement deduction must have its own down_payment_deduction cell.
        # An invoice cell for the same down-payment rule is not silently reused.
        prod = self.env['product.product'].create(
            {'name': 'BPE RR DP without deduction cell', 'type': 'service'})
        rule = self.env['barani.pohoda.export.rule'].create({
            'config_id': self.config.id, 'sequence': 2,
            'name': 'RR DP missing deduction kind',
            'match_mode': 'product', 'product_ids': [(6, 0, [prod.id])], 'active': True,
        })
        self.env['barani.pohoda.export.rule.mapping.cell'].create({
            'rule_id': rule.id, 'fiscal_profile_id': self.profile.id,
            'document_kind': 'invoice', 'enabled_state': 'active',
            'account_assignment_id': self.aa1.id,
            'vat_classification_id': self.vat_ud.id,
            'control_statement_code_id': self.kv_d2.id,
        })
        move = self._move([
            {'account': self.income_account, 'product': self.goods_product,
             'price_unit': 20.0, 'name': 'Supply'},
            {'account': self.advance_account, 'product': prod,
             'price_unit': -5.0, 'name': 'Advance deduction'},
        ], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_MAPPING_CELL_MISSING, res.blockers)
        ded = [ln for ln in res.lines if ln.role == 'deduction'][0]
        self.assertEqual(ded.document_kind, 'down_payment_deduction')
        self.assertEqual(ded.rule, rule)
        self.assertFalse(ded.cell)

    def test_archived_config_rules_do_not_match(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.prod_archived,
              'price_unit': 20.0}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_MAPPING_CELL_MISSING, res.blockers)
        self.assertFalse(res.lines[0].rule)

    def test_blocked_cell_blocks(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.prod_blocked,
              'price_unit': 20.0}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_MAPPING_CELL_BLOCKED, res.blockers)
        # Not OSS and not a repairs rule -> the generic block, not the repairs+OSS one.
        self.assertNotIn(C.BLOCK_REPAIRS_OSS, res.blockers)

    def test_review_required_cell_blocks(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.prod_review,
              'price_unit': 20.0}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_MAPPING_CELL_REVIEW_REQUIRED, res.blockers)

    def test_active_cell_missing_required_code_blocks(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.prod_incomplete,
              'price_unit': 20.0}], fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_REQUIRED_CODE_MISSING, res.blockers)
        self.assertIn('vat_classification', res.lines[0].missing_codes)

    def test_oss_active_cell_requires_moss(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.prod_oss_incomplete,
              'price_unit': 20.0}], fpos=self.fpos_oss)
        res = self.resolver.resolve(move)
        self.assertEqual(res.fiscal_profile, self.profile_oss)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_REQUIRED_CODE_MISSING, res.blockers)
        self.assertIn('moss_service_type', res.lines[0].missing_codes)

    def test_repairs_oss_blocks_with_specific_code(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.repair_product,
              'price_unit': 20.0}], fpos=self.fpos_oss)
        res = self.resolver.resolve(move)
        self.assertEqual(res.fiscal_profile, self.profile_oss)
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_REPAIRS_OSS, res.blockers)
        # The specific code replaces the generic blocked code.
        self.assertNotIn(C.BLOCK_MAPPING_CELL_BLOCKED, res.blockers)
        self.assertEqual(res.lines[0].rule, self.rule_repairs)

    # -------------------------------------------------------- kind boundaries
    def test_credit_note_maps_to_credit_note_kind(self):
        # The classifier returns 'credit_note' with empty buckets; the resolver derives
        # the lines and looks them up under document_kind='credit_note'. With no
        # credit_note cells configured this is CELL_MISSING (truthful "not configured");
        # the Phase-1 credit-note test-gate itself is the DOC 05 preflight's job.
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0}], move_type='out_refund', fpos=self.fpos)
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'credit_note')
        self.assertEqual(len(res.lines), 1)
        self.assertEqual(res.lines[0].document_kind, 'credit_note')
        self.assertTrue(res.is_blocked)
        self.assertIn(C.BLOCK_MAPPING_CELL_MISSING, res.blockers)

    def test_blocked_mixed_324_has_no_line_resolutions(self):
        # Mixed positive-324 + ordinary is a *classifier* block; the resolver maps
        # nothing and adds no mapping blocker (the classifier owns that block).
        move = self._move([
            {'account': self.income_account, 'product': self.goods_product,
             'price_unit': 20.0},
            {'account': self.advance_account, 'product': self.dp_product,
             'price_unit': 5.0},
        ], fpos=self.fpos)
        self.assertEqual(self.classifier.classify(move).doc_kind, 'blocked_mixed_324')
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'blocked_mixed_324')
        self.assertEqual(len(res.lines), 0)
        self.assertFalse(res.is_blocked)    # no *mapping* blocker
        self.assertFalse(res.is_resolved)   # vacuously not resolved (no mappable lines)

    def test_blocked_mixed_324_without_fiscal_position_has_no_mapping_profile_blocker(self):
        move = self._move([
            {'account': self.income_account, 'product': self.goods_product,
             'price_unit': 20.0},
            {'account': self.advance_account, 'product': self.dp_product,
             'price_unit': 5.0},
        ])
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'blocked_mixed_324')
        self.assertEqual(len(res.lines), 0)
        self.assertEqual(res.blockers, [])

    def test_unsupported_without_fiscal_position_has_no_mapping_profile_blocker(self):
        move = self._move(
            [{'account': self.income_account, 'product': self.goods_product,
              'price_unit': 20.0}], move_type='in_invoice')
        res = self.resolver.resolve(move)
        self.assertEqual(res.doc_kind, 'unsupported')
        self.assertEqual(len(res.lines), 0)
        self.assertEqual(res.blockers, [])
