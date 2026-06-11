/** @odoo-module **/
/* Part of the BARANI POHODA Export module. See LICENSE file for full copyright and licensing details.
 *
 * DOC 03 / Bucket C Step 4 — the accountant-facing mapping-matrix editor.
 *
 * Rules (rows, sequence-ordered) x active fiscal profiles (dynamic columns) for one
 * document kind at a time. Each cell shows its enabled_state badge, the three code
 * columns (account / VAT / KV, by their dictionary CODE prefix), and an inline
 * warning when an active cell misses a required code (mirrors the resolver's
 * BLOCK_REQUIRED_CODE_MISSING logic, incl. MOSS on OSS profiles). Clicking a cell
 * opens its form in a dialog (the controlled CODE-dropdowns live there); clicking a
 * missing cell opens a prefilled create form. CSV export per DOC 03.
 *
 * Read paths go through the ORM service, so ACLs and the multi-company record rules
 * apply exactly as in the fallback list views.
 */

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

const CELL_MODEL = "barani.pohoda.export.rule.mapping.cell";

export class PohodaMatrix extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            kinds: [], // [[value, label], ...]
            kind: "invoice",
            stateLabels: {}, // enabled_state value -> label
            profiles: [],
            rules: [],
            cells: {}, // "ruleId|profileId|kind" -> cell record
        });
        onWillStart(() => this.loadAll());
    }

    async loadAll() {
        this.state.loading = true;
        const fg = await this.orm.call(CELL_MODEL, "fields_get", [
            ["document_kind", "enabled_state"],
            ["selection", "string"],
        ]);
        this.state.kinds = fg.document_kind.selection;
        this.state.stateLabels = Object.fromEntries(fg.enabled_state.selection);
        const [profiles, rules] = await Promise.all([
            this.orm.searchRead(
                "barani.pohoda.fiscal.profile",
                [["active", "=", true]],
                ["name", "is_oss"],
                { order: "name, id" }
            ),
            this.orm.searchRead(
                "barani.pohoda.export.rule",
                [["active", "=", true]],
                ["name", "sequence"],
                { order: "sequence, id" }
            ),
        ]);
        this.state.profiles = profiles;
        this.state.rules = rules;
        await this.loadCells();
        this.state.loading = false;
    }

    async loadCells() {
        const ruleIds = this.state.rules.map((r) => r.id);
        const cells = ruleIds.length
            ? await this.orm.searchRead(
                  CELL_MODEL,
                  [["rule_id", "in", ruleIds]],
                  [
                      "rule_id",
                      "fiscal_profile_id",
                      "document_kind",
                      "enabled_state",
                      "account_assignment_id",
                      "vat_classification_id",
                      "control_statement_code_id",
                      "moss_service_type_id",
                  ]
              )
            : [];
        const map = {};
        for (const c of cells) {
            map[`${c.rule_id[0]}|${c.fiscal_profile_id[0]}|${c.document_kind}`] = c;
        }
        this.state.cells = map;
    }

    cellFor(rule, profile) {
        return this.state.cells[`${rule.id}|${profile.id}|${this.state.kind}`];
    }

    /** Dictionary name_get is "CODE — label"; the matrix shows the CODE. */
    codeOf(m2o) {
        return m2o ? m2o[1].split(" — ")[0] : "·";
    }

    /** Mirror of the resolver's required-code check, for inline warnings. */
    warningFor(cell, profile) {
        if (!cell || cell.enabled_state !== "active") {
            return "";
        }
        const missing = [];
        if (!cell.account_assignment_id) missing.push("account");
        if (!cell.vat_classification_id) missing.push("VAT");
        if (!cell.control_statement_code_id) missing.push("KV");
        if (profile.is_oss && !cell.moss_service_type_id) missing.push("MOSS");
        return missing.length ? `Missing: ${missing.join(", ")}` : "";
    }

    setKind(ev) {
        this.state.kind = ev.target.value;
    }

    async openCell(rule, profile) {
        const cell = this.cellFor(rule, profile);
        const action = {
            type: "ir.actions.act_window",
            res_model: CELL_MODEL,
            views: [[false, "form"]],
            target: "new",
        };
        if (cell) {
            action.res_id = cell.id;
        } else {
            action.context = {
                default_rule_id: rule.id,
                default_fiscal_profile_id: profile.id,
                default_document_kind: this.state.kind,
            };
        }
        await this.action.doAction(action, { onClose: () => this.loadCells() });
    }

    exportCsv() {
        const quote = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
        const rows = [
            ["Rule", ...this.state.profiles.map((p) => p.name)].map(quote).join(","),
        ];
        for (const rule of this.state.rules) {
            const row = [`${rule.sequence}. ${rule.name}`];
            for (const profile of this.state.profiles) {
                const cell = this.cellFor(rule, profile);
                if (!cell) {
                    row.push("missing");
                    continue;
                }
                const warn = this.warningFor(cell, profile);
                row.push(
                    `${cell.enabled_state} | acc:${this.codeOf(cell.account_assignment_id)}` +
                        ` vat:${this.codeOf(cell.vat_classification_id)}` +
                        ` kv:${this.codeOf(cell.control_statement_code_id)}` +
                        (warn ? ` | ${warn}` : "")
                );
            }
            rows.push(row.map(quote).join(","));
        }
        const blob = new Blob([rows.join("\n")], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `pohoda_matrix_${this.state.kind}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    }
}

PohodaMatrix.template = "barani_pohoda_export.PohodaMatrix";

registry.category("actions").add("barani_pohoda_export.matrix_editor", PohodaMatrix);
