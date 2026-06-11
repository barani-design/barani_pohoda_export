# -*- coding: utf-8 -*-
# Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
#
# Shared test fixture for the Bucket C lifecycle tests (and later the wizard tests):
# a complete, ACTIVE mapping chain (fiscal position -> profile -> rule -> cell) on top
# of the seeded config, plus invoice/batch/response helpers.
#
# Isolation: the fixture creates a DEDICATED sale journal and scopes the seeded
# config's journal_ids to it (rolled back at class teardown), so collect_moves can
# never pick up demo data or invoices from other test classes.

RSP = 'http://www.stormware.cz/schema/version_2/response.xsd'
RDC = 'http://www.stormware.cz/schema/version_2/documentresponse.xsd'


class PohodaLifecycleFixture:
    """Mixin for TransactionCase classes; call ``_setup_lifecycle_fixture()`` from
    ``setUpClass`` after ``super().setUpClass()``."""

    @classmethod
    def _setup_lifecycle_fixture(cls):
        cls.config = cls.env.ref('barani_pohoda_export.config_pohoda')
        cls.company = cls.config.company_id
        cls.partner = cls.env['res.partner'].create({'name': 'BPE LC Customer'})
        cls.preflight = cls.env['barani.pohoda.preflight']
        cls.service = cls.env['barani.pohoda.export.service']

        Account = cls.env['account.account']
        cls.income_account = Account.search(
            [('account_type', '=', 'income'), ('company_id', '=', cls.company.id)],
            limit=1) or Account.create({
                'name': 'BPE LC Income', 'code': 'BPELCI',
                'account_type': 'income', 'company_id': cls.company.id})
        cls.advance_account = Account.search(
            [('code', '=like', '324%'), ('company_id', '=', cls.company.id)],
            limit=1) or Account.create({
                'name': 'BPE LC Advances Received', 'code': '324998',
                'account_type': 'liability_current', 'company_id': cls.company.id})

        # Dedicated journal + scope the config to it (isolation from demo data).
        cls.journal = cls.env['account.journal'].create({
            'name': 'BPE LC Sales', 'code': 'BPELC', 'type': 'sale',
            'company_id': cls.company.id})
        cls.config.journal_ids = [(6, 0, [cls.journal.id])]

        # Fiscal position -> profile (the matrix column).
        cls.fpos = cls.env['account.fiscal.position'].create(
            {'name': 'BPE LC FPos', 'company_id': cls.company.id})
        cls.profile = cls.env['barani.pohoda.fiscal.profile'].create({
            'name': 'BPE LC Domestic', 'geography': 'domestic',
            'customer_tax_status': 'any', 'is_oss': False,
            'account_fiscal_position_ids': [(6, 0, [cls.fpos.id])],
        })

        def ref(xmlid):
            return cls.env.ref('barani_pohoda_export.%s' % xmlid)
        cls.aa1 = ref('aa_1')                       # ordinary revenue assignment
        cls.aa_advance = ref('aa_advance_324')      # is_advance_account=True (seed)
        cls.vat_ud, cls.vat_un = ref('vat_ud'), ref('vat_un')
        cls.kv_d2, cls.kv_kn = ref('kv_d2'), ref('kv_kn')

        Rule = cls.env['barani.pohoda.export.rule']
        Cell = cls.env['barani.pohoda.export.rule.mapping.cell']

        cls.goods_product = cls.env['product.product'].create(
            {'name': 'BPE LC Goods', 'type': 'service'})
        cls.dp_product = cls.env['product.product'].create(
            {'name': 'BPE LC Down payment', 'type': 'service'})

        cls.rule_goods = Rule.create({
            'config_id': cls.config.id, 'sequence': 5, 'name': 'LC Goods',
            'match_mode': 'product', 'product_ids': [(6, 0, [cls.goods_product.id])],
            'active': True,
        })
        cls.cell_goods = Cell.create({
            'rule_id': cls.rule_goods.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'invoice', 'enabled_state': 'active',
            'account_assignment_id': cls.aa1.id,
            'vat_classification_id': cls.vat_ud.id,
            'control_statement_code_id': cls.kv_d2.id,
        })

        cls.rule_dp = Rule.create({
            'config_id': cls.config.id, 'sequence': 1, 'name': 'LC Odpocet zalohy',
            'match_mode': 'product', 'product_ids': [(6, 0, [cls.dp_product.id])],
            'active': True,
        })
        cls.cell_dp_adv = Cell.create({
            'rule_id': cls.rule_dp.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'invoice', 'enabled_state': 'active',
            'account_assignment_id': cls.aa_advance.id,
            'vat_classification_id': cls.vat_un.id,
            'control_statement_code_id': cls.kv_kn.id,
        })
        cls.cell_dp_ded = Cell.create({
            'rule_id': cls.rule_dp.id, 'fiscal_profile_id': cls.profile.id,
            'document_kind': 'down_payment_deduction', 'enabled_state': 'active',
            'account_assignment_id': cls.aa_advance.id,
            'vat_classification_id': cls.vat_un.id,
            'control_statement_code_id': cls.kv_kn.id,
        })

    # ----------------------------------------------------------------- helpers
    @classmethod
    def _line(cls, product, price, account=None):
        return (0, 0, {
            'product_id': product.id, 'name': product.name, 'quantity': 1,
            'price_unit': price,
            'account_id': (account or cls.income_account).id,
            'tax_ids': [(6, 0, [])],
        })

    @classmethod
    def _pin(cls, move, specs):
        """Re-pin account/price after create (guards against product recompute)."""
        for line, (product, price, account) in zip(
                move.invoice_line_ids.sorted('id'), specs):
            line.write({'price_unit': price,
                        'account_id': (account or cls.income_account).id,
                        'tax_ids': [(6, 0, [])]})
        return move

    @classmethod
    def _invoice(cls, specs, move_type='out_invoice', invoice_date='2025-11-10',
                 fpos=True, post=True, origin=None):
        """``specs``: list of (product, price, account-or-None) tuples."""
        move = cls.env['account.move'].create({
            'move_type': move_type, 'partner_id': cls.partner.id,
            'journal_id': cls.journal.id, 'invoice_date': invoice_date,
            'fiscal_position_id': cls.fpos.id if fpos else False,
            'invoice_origin': origin or 'Q2026355',
            'invoice_line_ids': [cls._line(*s) for s in specs],
        })
        cls._pin(move, specs)
        if post:
            move.action_post()
        return move

    @classmethod
    def _regular(cls, **kw):
        return cls._invoice([(cls.goods_product, 100.0, None)], **kw)

    @classmethod
    def _advance(cls, price=50.0, **kw):
        return cls._invoice([(cls.dp_product, price, cls.advance_account)], **kw)

    @classmethod
    def _settlement(cls, **kw):
        return cls._invoice([
            (cls.goods_product, 100.0, None),
            (cls.dp_product, -50.0, cls.advance_account),
        ], **kw)

    @classmethod
    def _batch(cls, start='2025-11-01', end='2025-11-30'):
        return cls.env['barani.pohoda.export.batch'].create({
            'config_id': cls.config.id, 'company_id': cls.company.id,
            'start_date': start, 'end_date': end})

    @staticmethod
    def _response(items_xml, state='ok'):
        return ('<rsp:responsePack xmlns:rsp="%s" xmlns:rdc="%s" version="2.0" '
                'id="BPEBATCH" state="%s" note="" programVersion="X">%s'
                '</rsp:responsePack>' % (RSP, RDC, state, items_xml)).encode('utf-8')

    @staticmethod
    def _ok_item(item_id, record_id=None):
        produced = ('<rdc:producedDetails><rdc:id>%s</rdc:id></rdc:producedDetails>'
                    % record_id) if record_id else ''
        return ('<rsp:responsePackItem version="2.0" id="%s" state="ok">%s'
                '</rsp:responsePackItem>' % (item_id, produced))

    @staticmethod
    def _error_item(item_id, note="Import failed"):
        return ('<rsp:responsePackItem version="2.0" id="%s" state="error" '
                'note="%s"/>' % (item_id, note))
