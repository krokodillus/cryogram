// The step drawer: what a step does, its checks, its recorded evidence
import { escapeHtml, sentence, NODE_TYPE_LABEL } from "./util.js";
import * as api from "./api.js";

function stat(k, v) {
  return `<div class="d-stat"><div class="k">${k}</div><div class="v">${v ?? "-"}</div></div>`;
}

// The drawer's run counters for this step
function statsBlock(node) {
  const s = node.stats;
  if (!s) return "";
  const rate = s.success_rate === null || s.success_rate === undefined
    ? "n/a"
    : `${Math.round(s.success_rate * 100)}% <span class="d-stat-n">(${s.successes}/${s.runs})</span>`;
  const cells = [stat("Runs", s.runs ?? 0)];
  if (node.type === "ai" || node.type === "code" || node.type === "connector" || node.type === "browser")
    cells.push(stat("Success rate", rate));
  let note = "";
  if ((node.type === "code" || node.type === "connector" || node.type === "browser") && s.failure_count > 0) {
    const bits = [];
    if (s.failed_checks) bits.push(`${s.failed_checks} from checks`);
    if (s.failed_errors) bits.push(`${s.failed_errors} ${s.failed_errors === 1 ? "error" : "errors"}`);
    note = `<p class="d-stat-note">${s.failure_count}
      ${s.failure_count === 1 ? "failure" : "failures"}${bits.length ? ": " + bits.join(", ") : ""}</p>`;
  }
  return `<div class="d-section"><h3>Stats</h3><div class="d-stats">
    ${cells.join("")}
  </div>${note}</div>`;
}

const STD_CHECK = {
  text: "must be text", longtext: "must be text",
  number: "must be a number", boolean: "must be yes/no",
  date: "must be an ISO date (YYYY-MM-DD)",
  record: "must be a structured record",
  list: "must be a list",
  file: "must be an uploaded file that still exists (or inline text content)",
};

// A list's per-item field as one plain phrase
function itemFieldPhrase(f) {
  const t = f.type === "enum" && f.options?.length
    ? `one of ${f.options.join("/")}` : (f.type || "not typed yet");
  return `${f.name} (${t})`;
}

// Workflows a schema fragment to the drawer's plain type vocabulary
function fieldTypeOf(sub) {
  if (!sub || typeof sub !== "object") return "";
  if (Array.isArray(sub.enum)) return "enum";
  if (sub.type === "string") return sub.format === "date" ? "date" : (sub["x-file"] ? "file" : "text");
  return { number: "number", boolean: "boolean", object: "record", array: "list" }[sub.type] || "";
}
// A record schema's fields as drawer rows
function schemaFields(obj) {
  if (!obj || obj.type !== "object" || !obj.properties) return [];
  const req = new Set(obj.required || []);
  return Object.entries(obj.properties).map(([name, sub]) => ({
    name, type: fieldTypeOf(sub), optional: !req.has(name),
    options: Array.isArray(sub.enum) ? sub.enum : undefined,
  }));
}
// A list port's per-item fields, whichever way they were declared
function itemFieldsOf(p) {
  if (p.item_fields?.length) return p.item_fields;
  const s = p.schema;
  return (s && s.type === "array") ? schemaFields(s.items) : [];
}
// A record port's fields
function recordFieldsOf(p) {
  const s = p.schema;
  return (s && s.type === "object") ? schemaFields(s) : [];
}

// The derived shape checks as plain sentences
function standardChecksBlock(node) {
  const line = (p) => itemFieldsOf(p).length
    ? `must be a list where every item carries: ${itemFieldsOf(p).map(itemFieldPhrase).join(", ")}`
    : recordFieldsOf(p).length
      ? `must be a record carrying: ${recordFieldsOf(p).map(itemFieldPhrase).join(", ")}`
    : p.type === "enum"
      ? `must be one of: ${(p.options || []).join(", ")}`
      : STD_CHECK[p.type];
  const rows = [];
  (node.inputs || []).forEach((p) => { const l = line(p); if (l) rows.push([p, l, "input"]); });
  (node.outputs || []).forEach((p) => { const l = line(p); if (l) rows.push([p, l, "output"]); });
  if (!rows.length) return "";

  const html = rows.map(([p, l]) => `<div class="crit">
      <div class="c-expr"><code>${escapeHtml(p.name)}</code> ${escapeHtml(l)}</div>
    </div>`).join("");
  return html;
}

// Authored checks by their human labels; raw expressions never render
function criteriaBlock(criteria) {

  if (!criteria || !criteria.length) return "";
  const rows = criteria.map((c) => {
    const just = c.justification
      ? `<div class="c-meta">${escapeHtml(c.justification)}</div>` : "";
    return `<div class="crit">
      <div class="c-expr">${escapeHtml(c.label || c.expr)}</div>${just}
    </div>`;
  }).join("");
  return rows;
}

// A native details section, collapsed by default
function collapsible(title, inner, open = false) {
  return `<details class="d-section d-collapse"${open ? " open" : ""}>
    <summary class="d-summary">${escapeHtml(title)}</summary>${inner}</details>`;
}

// The human name of a port - users never see keys
function portName(p) { return p.label || p.name; }

// The plain type on a port row's summary line
function typeLabel(p) {
  if (itemFieldsOf(p).length) return "list of records";
  return sentence(p.type || "not typed yet");
}

// Every step reachable backwards along the lines - where an input's value can come from
function earlierSteps(workflow, nodeId) {
  const inc = new Map();
  (workflow?.edges || []).forEach((e) =>
    inc.set(e.dst, [...(inc.get(e.dst) || []), e.src]));
  const seen = new Set(), stack = [...(inc.get(nodeId) || [])];
  while (stack.length) {
    const s = stack.pop();
    if (seen.has(s)) continue;
    seen.add(s);
    stack.push(...(inc.get(s) || []));
  }
  return seen;
}

// Port rows: name and type at a glance, expandable to the full shape
function portList(ports, workflow, node) {

  if (!ports || !ports.length) return `<div class="hint">(none declared)</div>`;
  const earlier = node ? earlierSteps(workflow, node.id) : new Set();
  const producerOf = (name) => {
    const hits = (workflow?.nodes || []).filter(
      (n) => earlier.has(n.id) && (n.outputs || []).some((q) => q.name === name));
    return hits.length === 1 ? hits[0].name : "";
  };
  return `<ul class="port-list">` + ports.map((p) => {
    const desc = p.description
      ? `<div class="kv"><span class="k">Description</span><span>${escapeHtml(p.description)}</span></div>` : "";
    const fieldRows = (list) => `<ul class="item-fields">${list.map((f) => {
      const d = f.description ? ` - ${f.description}` : "";
      const opt = f.optional ? " (may be empty)" : "";
      return `<li>${escapeHtml(itemFieldPhrase(f))}${escapeHtml(opt)}${escapeHtml(d)}</li>`;
    }).join("")}</ul>`;
    const items = itemFieldsOf(p).length
      ? `<div class="kv"><span class="k">Each item carries</span></div>${fieldRows(itemFieldsOf(p))}`
      : recordFieldsOf(p).length
        ? `<div class="kv"><span class="k">Carries the fields</span></div>${fieldRows(recordFieldsOf(p))}`
        : "";
    const opts = (p.type === "enum" && p.options?.length)
      ? `<div class="kv"><span class="k">Options</span><span>${escapeHtml((p.options || []).join(", "))}</span></div>` : "";
    const from = node ? producerOf(p.name) : "";
    const src = from
      ? `<div class="kv"><span class="k">Comes from</span><span>${escapeHtml(from)}</span></div>` : "";
    return `<li class="port-item"><details class="port-x">
      <summary class="port-name-h">${escapeHtml(portName(p))}
        <span class="tag port-type">${escapeHtml(typeLabel(p))}</span></summary>
      ${desc}${items}${opts}${src}</details></li>`;
  }).join("") + `</ul>`;
}

// The Interface section: inputs and outputs
function interfaceBlock(node, workflow) {

  return `<div class="d-section"><h3>Interface</h3>
    <div class="io-sub">Inputs</div>${portList(node.inputs, workflow, node)}
    <div class="io-sub">Outputs</div>${portList(node.outputs, workflow)}</div>`;
}

// The one Checks section: what every run must pass, or it stops at this step
function checksBlock(node) {
  const std = standardChecksBlock(node);
  const crit = criteriaBlock((node.config || {}).criteria);

  const firstRun = ((node.stats || {}).runs || 0) === 0
    ? `<p class="hint">This step hasn't run yet - the first real run is what tries it.</p>` : "";
  const policy = invalidItemsBlock(node);
  if (!std && !crit && !firstRun && !policy)
    return collapsible("Checks", `<div class="hint">none yet</div>`, true);
  const lead = `<p class="hint">Every run must pass these - a failure stops the run at this step.</p>`;
  return collapsible("Checks", `${lead}${firstRun}${std}${crit}${policy}`, true);
}

const INVALID_ITEMS_TEXT = {
  stop: "stop the run and show them",
  proceed: "set them aside, carry on with the rest, and raise an issue",
  log: "set them aside, carry on, and only note them in the run history",
};
// The if-an-item-doesn't-fit select, for list outputs
function invalidItemsBlock(node) {
  if (node.type === "user-input") return "";
  const listy = (node.outputs || []).some((p) => itemFieldsOf(p).length);
  if (!listy) return "";
  const cur = (node.config || {}).on_invalid_items || "stop";
  return `<div class="kv invalid-items"><span class="k">If an item doesn't fit</span>
    <select id="ndInvalidItems" class="var-input">
      ${Object.entries(INVALID_ITEMS_TEXT).map(([k, t]) =>
        `<option value="${k}"${k === cur ? " selected" : ""}>${escapeHtml(t)}</option>`).join("")}
    </select></div>`;
}

// The Evidence section: the recorded proving run, fetched when opened
function evidenceBlock() {
  return `<details class="d-section d-collapse" id="ndEvidence">
    <summary class="d-summary">Evidence</summary>
    <p class="hint">From a real run while building - what the data actually looks like. Kept so future changes can be tested against it.</p>
    <div id="ndEvidenceBody"><div class="hint">Loading&hellip;</div></div>
  </details>`;
}

// The drawer body per step type; a shape-only shared step gets no interior sections
function bodyForType(node, workflow) {

  const shapeOnly = node.config === undefined && node.type !== "user-input";
  const cfg = node.config || {};
  if (node.type === "connector" || node.type === "browser") {
    const write = node.read_only === false;
    const suppressed = node.approval_suppressed === true;
    const access = node.read_only
      ? "read-only (GET) - runs without approval"
      : suppressed
        ? "writes externally - approval turned OFF by you"
        : "writes externally (POST/PUT/PATCH/DELETE) - asks for approval";
    const toggle = write ? `<div class="d-section"><h3>Approval</h3>
        <label class="approval-toggle"><input type="checkbox" id="approvalToggle" ${suppressed ? "" : "checked"} /> Require my approval before this runs</label>
        <p class="hint">Toggle any time. Choosing "don't ask again" at run time sets this too.</p>
      </div>` : "";
    const impact = node.external_impact
      ? `<div class="kv"><span class="k">External impact</span><span>${escapeHtml(node.external_impact)}</span></div>` : "";
    return `<div class="d-section"><h3>Connector</h3>
        <div class="kv"><span class="k">Access</span><span>${escapeHtml(access)}</span></div>${impact}</div>
      ${toggle}
      ${shapeOnly ? "" : collapsible("Request", `<pre class="code">${escapeHtml(cfg.code || "...")}</pre>`)}
      `;
  }
  if (node.type === "code") {
    if (shapeOnly) return "";
    return collapsible("Code",
      `<pre class="code">${escapeHtml(cfg.code || "// (not yet written)")}</pre>`);
  }
  if (node.type === "ai") {
    if (shapeOnly) return "";
    const m = cfg.model || {};

    const model = `<div class="kv"><span class="k">Model</span>
          <span class="model-pick">
            <select id="ndModelSel" disabled>
              <option>${escapeHtml(m.model || "(not set up yet)")}</option>
            </select>
            <button type="button" id="ndModelApply" class="btn btn-secondary btn-sm" disabled>Change</button>
          </span></div>
        <div class="kv"><span class="k">Temperature</span>
          <span class="model-pick" id="ndTempRow">
            <input id="ndTemp" type="number" min="0" max="1" step="0.1"
                   value="${escapeHtml(String(m.temperature ?? 0))}">
            <button type="button" id="ndTempApply" class="btn btn-secondary btn-sm" disabled>Change</button>
          </span></div>
        <div class="kv"><span class="k">Token limit</span>
          <span class="model-pick">
            <input id="ndTokens" type="number" min="1" step="1"
                   value="${cfg.max_tokens ? escapeHtml(String(cfg.max_tokens)) : ""}"
                   placeholder="${workflow?._ai_defaults?.max_tokens ? Number(workflow._ai_defaults.max_tokens).toLocaleString() + " (standard)" : "standard"}">
            <button type="button" id="ndTokensApply" class="btn btn-secondary btn-sm" disabled>Change</button>
          </span></div>
        <p class="hint">Models are grouped by provider, your default provider first and
          cheapest first where they have a Cost. Change any of these at any time - the
          step tries the new setting on your next run. Temperature runs from 0, the
          same answer every time, to 1; a blank token limit means the standard room.</p>`;
    // The prompt is readable in full and editable; inputs/outputs stay fixed here, so an edit changes meaning, never structure
    const prompt = `
      <textarea id="ndPrompt" class="nd-prompt" rows="14" spellcheck="false"
        placeholder="(no prompt yet)">${escapeHtml(cfg.prompt || "")}</textarea>
      <div class="nd-prompt-actions">
        <button type="button" id="ndPromptSave" class="btn btn-secondary btn-sm" disabled>Save</button>
        <button type="button" id="ndPromptReset" class="btn btn-secondary btn-sm" disabled>Reset to the Builder Agent's version</button>
      </div>
      <p class="hint">The step's inputs and outputs are set by the Builder Agent, and the
        answer's structure is enforced separately - editing the wording here can
        change what the step asks for, not the shape of what comes back. Your
        next run is where the new wording gets its first real try.</p>`;
    return collapsible("Model", model) + collapsible("Prompt", prompt);
  }

  const ports = (node.outputs && node.outputs.length ? node.outputs : node.inputs) || [];
  return `<div class="d-section"><h3>Inputs the user provides</h3>
    ${portList(ports)}
    <p class="hint">Each type renders its own field in the run form (date picker, file upload, ...).</p></div>`;
}

// Opens the step drawer
export function openNodeDetail(node, workflow, onChange, extras = {}) {
  const d = document.getElementById("nodeDetail");
  const approval = ((node.type === "connector" || node.type === "browser") && node.read_only === false)
    ? (node.approval_suppressed
        ? `<span class="status-badge planned">approval off</span>`
        : `<span class="status-badge irrev">needs approval</span>`)
    : "";
  d.innerHTML = `
    <button class="d-close" aria-label="Close">&times;</button>
    <div class="n-type" data-type="${escapeHtml(node.type)}">${NODE_TYPE_LABEL[node.type] || node.type}</div>
    <h2>${escapeHtml(node.name)}</h2>
    <p class="d-desc">${escapeHtml(node.description || "")}</p>
    ${node.how ? `<p class="d-desc d-how">${escapeHtml(node.how)}</p>` : ""}
    <div class="n-pills">
      ${approval}
    </div>
    ${statsBlock(node)}
    ${interfaceBlock(node, workflow)}
    ${bodyForType(node, workflow)}
    ${checksBlock(node)}
    ${node.type === "user-input" ? "" : evidenceBlock()}
  `;
  d.querySelector(".d-close").onclick = closeNodeDetail;

  const ev = d.querySelector("#ndEvidence");
  if (ev) ev.addEventListener("toggle", async () => {
    if (!ev.open || ev.dataset.loaded) return;
    ev.dataset.loaded = "1";
    const body = d.querySelector("#ndEvidenceBody");
    try {
      const { sample } = await api.getNodeEvidence(workflow.id, node.id);
      if (!sample) {
        body.innerHTML = `<div class="hint">No recorded run kept for this step yet.</div>`;
        return;
      }
      const pre = (v) => `<pre class="code">${escapeHtml(JSON.stringify(v ?? null, null, 2))}</pre>`;
      body.innerHTML = `<div class="io-sub">What went in</div>${pre(sample.inputs)}
        <div class="io-sub">What came out</div>${pre(sample.outputs)}`;
    } catch {
      body.innerHTML = `<div class="hint">Couldn't load the recorded run.</div>`;
    }
  });

  const pol = d.querySelector("#ndInvalidItems");
  if (pol) pol.onchange = async () => {
    await api.setNodeInvalidItems(workflow.id, node.id, pol.value).catch(() => {});
    node.config = { ...(node.config || {}), on_invalid_items: pol.value === "stop" ? undefined : pol.value };
    if (onChange) onChange();
  };

  const at = d.querySelector("#approvalToggle");
  if (at) at.onchange = async () => {
    const suppressed = !at.checked;
    await api.setNodeApproval(workflow.id, node.id, suppressed);
    node.approval_suppressed = suppressed;
    if (onChange) onChange();
    openNodeDetail(node, workflow, onChange, extras);
  };

  // Save is live only when the text differs; Reset only when the agent's version exists and something differs from it
  const ta = d.querySelector("#ndPrompt");
  const pSave = d.querySelector("#ndPromptSave");
  const pReset = d.querySelector("#ndPromptReset");
  if (ta && pSave && pReset && extras.onEditPrompt) {
    const cfg = node.config || {};
    const base = cfg.prompt_agent || "";
    const sync = () => {
      pSave.disabled = ta.value === (cfg.prompt || "") || !ta.value.trim();
      pReset.disabled = !base || ((cfg.prompt || "") === base && ta.value === base);
    };
    ta.addEventListener("input", sync);
    sync();
    pSave.onclick = () => {
      pSave.disabled = true;
      extras.onEditPrompt(node.id, { prompt: ta.value });
    };
    pReset.onclick = () => {
      pReset.disabled = true;
      extras.onEditPrompt(node.id, { reset: true });
    };
  }
  const sel = d.querySelector("#ndModelSel");
  const applyBtn = d.querySelector("#ndModelApply");
  if (sel && applyBtn && extras.onChangeModel) {
    api.getWorkflowModels().then(({ models }) => {
      const ready = (models || []).filter((m) => m.ready);
      if (!ready.length) return;
      // An option is one model under one provider; the provider shows only where the same name is offered by more than one
      const ref = (node.config || {}).model || {};
      const cur = ref.model || "";
      const curProv = ref.provider_id || "";
      const key = (m) => `${m.provider_id}|${m.name}`;

      const current = ready.find((m) => m.name === cur && (!curProv || m.provider_id === curProv));
      const curKey = current ? key(current) : "";
      // A step that sends a file to its model offers only models that read that kind of file; the others are greyed, and the line under the picker says why
      const kinds = ((node.config || {}).sends_file || {}).kinds || [];
      const wanted = kinds.filter((k) => k === "pdf" || k === "image");
      const readsAll = (m) => wanted.every((k) => (m.reads || []).includes(k));
      const kindWord = { pdf: "a PDF", image: "an image" };
      const kindPlural = { pdf: "PDFs", image: "images" };
      // One group per provider, in the list's own order (the default provider first), so the same model name under two providers reads apart
      const groups = new Map();
      ready.forEach((m) => {
        if (!groups.has(m.provider_id)) groups.set(m.provider_id, { label: m.provider, rows: [] });
        groups.get(m.provider_id).rows.push(m);
      });
      const option = (m) => {
        const ok = readsAll(m);
        const why = ok ? "" : ` (does not read ${wanted.filter((k) => !(m.reads || []).includes(k)).map((k) => kindPlural[k]).join(" or ")})`;
        return `<option value="${escapeHtml(key(m))}"${key(m) === curKey ? " selected" : ""}${ok ? "" : " disabled"}>${escapeHtml(m.name)}${why}</option>`;
      };
      sel.innerHTML = [...groups.values()].map((g) =>
        `<optgroup label="${escapeHtml(g.label)}">${g.rows.map(option).join("")}</optgroup>`).join("");

      if (current?.no_temperature) {
        const row = d.querySelector("#ndTempRow");
        if (row) row.innerHTML = `<span class="muted">Not available</span>`;
      }
      if (wanted.length && ready.some((m) => !readsAll(m))) {
        const k = wanted[0];
        sel.closest(".kv")?.insertAdjacentHTML("afterend",
          `<p class="hint">This step sends ${kindWord[k]} to the AI model, so only models that read ${kindPlural[k]} can be selected. To choose from more models, ask the Builder Agent to add a step that reads the file first. That may make the file analysis less accurate.</p>`);
      }
      if (cur && !current)
        sel.insertAdjacentHTML("afterbegin",
          `<option value="" selected>${escapeHtml(cur)} (not set up)</option>`);
      sel.disabled = false;
      sel.onchange = () => { applyBtn.disabled = !sel.value || sel.value === curKey; };
      applyBtn.onclick = () => {
        closeNodeDetail();
        const [providerId, name] = sel.value.split("|");
        extras.onChangeModel(node.id, name, providerId);
      };
    }).catch(() => {});
  }
  // Temperature and token limit apply the same way the model does: a box, Change, the step tries it on the next run
  const wireAnswerSetting = (inputId, btnId, key, current) => {
    const input = d.querySelector(`#${inputId}`);
    const btn = d.querySelector(`#${btnId}`);
    if (!input || !btn || !extras.onChangeAnswer) return;
    input.oninput = () => { btn.disabled = input.value.trim() === String(current ?? ""); };
    btn.onclick = () => {
      closeNodeDetail();
      extras.onChangeAnswer(node.id, { [key]: input.value.trim() });
    };
  };
  if (node.type === "ai") {
    const cfg = node.config || {};
    wireAnswerSetting("ndTemp", "ndTempApply", "temperature", (cfg.model || {}).temperature ?? 0);
    wireAnswerSetting("ndTokens", "ndTokensApply", "max_tokens", cfg.max_tokens || "");
  }

  d.classList.remove("hidden");
}

export function closeNodeDetail() {
  document.getElementById("nodeDetail").classList.add("hidden");
}
