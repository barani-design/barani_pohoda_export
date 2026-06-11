======================
BARANI POHODA Export
======================

Export BARANI customer invoices, tax-configuration-driven down-payment
(advance) invoices, settlement invoices and approved credit notes from Odoo 16
to POHODA XML.

This module replaces the legacy DDS-based POHODA export path. It
classifies document shape and resolves settlement advance sources only from
native Odoo account.move/account.move.line facts and native sale-line links. It
never depends on, imports from, or calls any DDS module at runtime.

.. WARNING::
   XML generation, XSD validation and response parsing exist as **dormant, read-only
   services**: nothing builds, sends or imports XML automatically, and no automatic
   sending to POHODA is performed in Phase 1. POHODA field details (rateVAT bands,
   produced-record-id elements, encoding) remain import-test gated. All mapping values
   are migration seeds and require accountant verification and a successful POHODA
   import test before production use.

Features
------------------------------
* Data model for POHODA configuration, dictionaries, fiscal profiles, mapping
  cells, export batches and account.move export-state fields.
* Document classifier: ``regular_invoice``, ``advance_invoice``,
  ``settlement_invoice``, ``credit_note``, ``blocked_mixed_324`` and
  ``unsupported``. There is one advance document kind; VAT may be 0 or positive
  according to the Odoo tax configuration.
* Settlement source resolver: one negative-324 deduction line resolves
  to one source ``advance_invoice`` through native ``sale_line_ids ->
  invoice_lines -> move_id`` links, with a VAT-rate-match guard.
* Rule resolver maps exportable lines to rule/profile/cell POHODA codes or explicit blockers.
* POHODA XML builder: ``dat:dataPack`` with one ``inv:invoice`` per move
  (``issuedInvoice`` / ``issuedAdvanceInvoice``; settlements emit negative
  advance-deduction items; credit notes are test-gated).
* XSD validator (single ``.xsd`` or a zip of the Stormware schema set) and an
  import-response parser that records per-document accepted/warning/error state.
* Read-only export preflight that sequences classify -> source-resolve ->
  rule-resolve over a recordset or date range and aggregates every blocker,
  including the not-posted / wrong-company / amount-reconciliation gates.
* Mapping matrix seed and dictionary seed are present but accountant/import-test
  gated.

Installation
------------------------------
* Place the ``barani_pohoda_export`` folder in your Odoo 16 addons path.
* Update the Apps list and install **BARANI POHODA Export**.

Configuration
------------------------------
#. Open **POHODA Export → Configuration → Settings** and complete the company
   configuration (IČO, journals to export, key date, EU country group, OSS).
#. Review the code dictionaries (account assignments / předkontace, VAT
   classifications, control-statement and MOSS codes) with your accountant.
   Mark the received-advance (324) assignment(s) — the preflight audits it.
#. Link your ``account.fiscal.position`` records to **Fiscal Profiles** (the
   matrix columns) and activate the **Export Rules** you need (the seed ships
   them inactive on purpose).
#. Fill the **Mapping Matrix** for each document kind; resolve every
   ``review_required`` cell before production.
#. Run **New Export** for a test period, import the generated file into a
   POHODA *trial* unit, and import the response back — only then enable the
   flow for production periods.

Known issues / Roadmap
------------------------------
* Multi-company record rules and company-consistency checks are in place
  (config, rules, mapping cells, batches, account assignments). Fiscal profiles
  and the VAT/KV/MOSS dictionaries are intentionally shared across companies;
  review this against your own multi-company setup before go-live.
* The configuration UI uses the technical fallback views; the custom matrix
  widget is a later step.
* The ``advance_flow_mode`` selection still contains legacy zero-VAT wording for
  compatibility with the pre-pivot seed. It is a configuration placeholder, not
  the classifier's ``advance_invoice`` document kind.
* (to be completed)

Installation notes
------------------------------
* Install on a **fresh** database. The seed data (POHODA code dictionaries, fiscal
  profiles, and the starter configuration) is created with fixed external IDs under
  ``noupdate="1"``.
* Updating a database where the same dictionary codes, or an active configuration,
  were already created **manually** can conflict with the seed (unique codes; one
  active configuration per company). A reconciliation migration for that case will be
  added when there is an installed base; until then, use a fresh database for the
  first run.
* External dependency: ``lxml`` (ships with Odoo).

Bug Tracker
------------------------------
* Internal.

Credits
------------------------------
Author / Maintainer: BARANI DESIGN Technologies s. r. o.

License
------------------------------
This module is licensed under LGPL-3. See the LICENSE file for the full text.
