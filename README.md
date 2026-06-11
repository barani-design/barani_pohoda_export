# BARANI POHODA Export (Odoo 16)

Export customer invoices, tax-configuration-driven down-payment (advance)
invoices, settlement invoices and approved credit notes from **Odoo 16** to
**POHODA XML** (Stormware POHODA, CZ/SK accounting software).

The module classifies document shape and resolves settlement advance sources
only from native Odoo `account.move` / `account.move.line` facts and native
sale-line links. It has **no third-party module dependencies** — only core
Odoo (`base`, `account`, `sale_management`, `product`, `mail`).

## Status

Phase 1, in active development. The service core is implemented: data model,
document classifier, settlement advance-source resolver, mapping-matrix rule
resolver, POHODA XML builder, XSD validator, import-response parser, and a
read-only export preflight that aggregates every blocker.

**All seeded mapping values are migration seeds that require accountant
verification and a POHODA import test before production use.** There is no
automatic sending to POHODA in Phase 1 — the generated XML is imported into
POHODA manually, which is the real validation gate.

## Installation

* **Odoo.sh / source install:** add this repository (branch `16.0`) to your
  addons, update the apps list, and install *BARANI POHODA Export*.
* The module ships with `installable: True` and is a top-level application
  (menu: BARANI POHODA Export).

Full functional documentation lives in
[`barani_pohoda_export/README.rst`](barani_pohoda_export/README.rst).

## Compatibility

* Odoo 16.0 (Community and Enterprise).
* XSD validation is optional: upload the official Stormware schema set as an
  attachment in the module configuration. Schemas are **not** bundled and the
  module makes **no network calls**.

## License

[LGPL-3](LICENSE). Contributions and improvement reports are welcome.

## Support

BARANI DESIGN Technologies s. r. o. — <sales@baranidesign.com> —
<https://www.baranidesign.com>
