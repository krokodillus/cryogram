// Admin tab for the AI providers workflows run on - cards, keys, models
import * as api from "./api.js";
import { escapeHtml, humanTokens, slugName, inlineEdit } from "./util.js";
import { openModal, closeModal } from "./modal.js";
import { keyField, wireKeyFields, readKey, keyNameFor,
         modelsBlock, readModels, localTick, keyStateTag,
         providerKeySet, isBuilderProvider, mintId, defaultProviderIds } from "./providerForm.js";

const isWorkflow = (p) => !isBuilderProvider(p);
// Default cards come from the server's list; the fallback only covers a mid-upgrade stale server
const isDefault = (p, s) =>
  defaultProviderIds(s).includes(p.id);

// One provider card answers: is this set up, and what uses it - token spend lives on the detail page
function card(p, s, usage) {
  const u = usage[p.id] || { workflows: 0, nodes: 0 };

  const keyOk = providerKeySet(p, s);
  const on = keyOk && p.enabled !== false;
  const keyTag = keyStateTag(p, s);
  return `<article class="card provider-card" data-id="${escapeHtml(p.id)}"
      tabindex="0" role="link" aria-label="Open ${escapeHtml(p.name || p.id)}">
    <div class="card-head"><h3>${escapeHtml(p.name || p.id)}</h3>${keyTag}</div>
    <div class="card-stats">
      <span class="card-stat"><b>${u.workflows}</b> ${u.workflows === 1 ? "workflow" : "workflows"}</span>
      <span class="card-stat"><b>${u.nodes}</b> ${u.nodes === 1 ? "AI step" : "AI steps"}</span>
    </div>
    <label class="switch${keyOk ? "" : " switch-disabled"}" title="${keyOk
      ? `Turned off, this provider's models can't be used by any workflow.
         The key stays saved. Applies from the next run.`
      : "Add an API key first - a provider without one can't serve any workflow."}">
      <input type="checkbox" data-enabled ${on ? "checked" : ""}
        ${keyOk ? "" : "disabled"} />
      <span class="switch-track" aria-hidden="true"></span>
      <span data-switch-label>${on ? "Active" : "Inactive"}</span>
    </label>
    <label class="default-tick${on ? "" : " default-tick-off"}" title="${on
      ? "The Builder Agent picks models for AI steps from this provider first."
      : "Only an active provider can be the default."}">
      <input type="checkbox" data-default ${s.default_provider === p.id ? "checked" : ""}
        ${on ? "" : "disabled"} />
      <span>Default</span>
    </label>
  </article>`;
}

// The Workflow AI providers tab: cards plus usage counts
export async function renderProvidersTab(root) {
  root.innerHTML = `<p class="muted">Loading&#8230;</p>`;
  const [s, usageRes] = await Promise.all([
    api.getSettings(),
    api.getProviderUsage().catch(() => ({ usage: {} })),
  ]);
  const usage = usageRes.usage || {};
  const list = (s.providers || []).filter(isWorkflow);
  root.innerHTML = `
    <div class="providers-bar">
      <p class="hint">Providers whose models run the AI steps inside your
        workflows - separate from the Builder Agent, so you can track and control
        that spend on its own. Click a card to add its key or edit its models.</p>
      <button id="addProvider" class="btn btn-primary">+ Add provider</button>
    </div>
    ${list.length ? `<div class="card-grid">${list.map((p) =>
        card(p, s, usage)).join("")}</div>`
      : `<div class="empty-state"><p>No providers yet.</p></div>`}`;

  root.querySelectorAll(".provider-card").forEach((el) => {
    const open = () => { location.hash = `#/admin/provider/${el.dataset.id}`; };
    el.onclick = (e) => { if (!e.target.closest(".switch, .default-tick")) open(); };
    el.onkeydown = (e) => { if (e.key === "Enter" && !e.target.closest(".switch, .default-tick")) open(); };

    const tick = el.querySelector("[data-default]");
    tick.onchange = async () => {
      try {
        await api.putSettings({ default_provider: tick.checked ? el.dataset.id : "" });
      } catch { }
      renderProvidersTab(root);
    };
    const box = el.querySelector("[data-enabled]");
    const label = el.querySelector("[data-switch-label]");
    box.onchange = async () => {
      try {
        const fresh = await api.getSettings();
        const providers = fresh.providers || [];
        const p = providers.find((x) => x.id === el.dataset.id);
        if (!p) throw new Error("provider not found");
        p.enabled = box.checked;
        await api.putSettings({ providers });
      } catch {
        box.checked = !box.checked;
      }
      label.textContent = box.checked ? "Active" : "Inactive";
      renderProvidersTab(root);
    };
  });
  root.querySelector("#addProvider").onclick = () => addProviderModal();
}

// The add form keeps to the essentials - name, key, models - with the API plumbing at the bottom
function addProviderModal() {
  const draft = { id: mintId(), name: "", adapter: "openai", auth: "api-key",
                  use: "workflow", enabled: true, endpoint: "", key_name: "",
                  tags: [], models: [] };

  const modal = openModal({
    title: "Add provider",
    body: `<p class="muted">Loading&#8230;</p>`,
    actions: [
      { label: "Create", kind: "btn-primary", onClick: create },
      { label: "Cancel" },
    ],
  });

  function sync() {
    const body = modal.querySelector(".modal-body");
    draft.name = body.querySelector('[data-f="name"]')?.value.trim() || draft.name;
    const typed = readKey(body);
    if (typed) draft._pendingKey = typed;
    draft.models = readModels(body);
    draft.adapter = body.querySelector('[data-f="adapter"]')?.value || draft.adapter;
    draft.endpoint = (body.querySelector('[data-f="endpoint"]')?.value || "").trim();
  }

  function paint() {
    const body = modal.querySelector(".modal-body");
    body.innerHTML = `<div class="provider-form">
      <div class="form-group"><label class="form-label">Name</label>
        <input data-f="name" type="text" value="${escapeHtml(draft.name)}"
               placeholder="e.g. Mistral, my Ollama server"></div>
      ${keyField(draft, false, false)}
      ${modelsBlock(draft, { endpoint: true })}
      <div class="form-group"><label class="form-label">API type</label>
        <select data-f="adapter">
          <option value="openai" ${!["anthropic", "gemini"].includes(draft.adapter) ? "selected" : ""}>OpenAI-compatible</option>
          <option value="anthropic" ${draft.adapter === "anthropic" ? "selected" : ""}>Anthropic-compatible</option>
          <option value="gemini" ${draft.adapter === "gemini" ? "selected" : ""}>Google Gemini (AI Studio or AI Platform)</option>
        </select>
        <p class="form-hint">Most hosted and local providers speak the
          OpenAI-compatible API. Google Gemini takes PDFs and images; for Google
          AI Platform set the endpoint to https://aiplatform.googleapis.com/v1/publishers/google
          and add the models by name.</p></div>
      <div class="form-group"><label class="form-label">Endpoint</label>
        <input data-f="endpoint" type="url" required value="${escapeHtml(draft.endpoint || "")}"
               placeholder="https://..." />
        <p class="form-hint">The address the provider's API answers at - a hosted service, a proxy, or a local server such as Ollama.</p></div>
      <p class="form-error hidden" id="provAddError"></p>
    </div>`;
    wireKeyFields(body);
    body.querySelector('[data-f="adapter"]').onchange = () => { sync(); paint(); };
    body.querySelector("[data-add-model]").onclick = () => {
      sync();
      draft.models.push({ name: "", endpoint: "", cost: null, quality: null });
      paint();
    };
    body.querySelectorAll("[data-del-model]").forEach((btn) => {
      btn.onclick = () => { btn.closest(".model-row").remove(); sync(); paint(); };
    });
    body.querySelector('[data-f="name"]').focus();
  }

  paint();

  async function create(root) {
    sync();
    const err = root.querySelector("#provAddError");
    err.classList.add("hidden");
    if (!draft.name) {
      err.textContent = "Give the provider a name.";
      err.classList.remove("hidden");
      return;
    }
    if (!draft.endpoint) {
      err.textContent = "Give the provider its endpoint address.";
      err.classList.remove("hidden");
      return;
    }
    try {
      const fresh = await api.getSettings();
      const providers = fresh.providers || [];
      draft.key_name = keyNameFor(slugName(draft.name) || draft.adapter, providers);
      if (draft._pendingKey) await api.putSecret(draft.key_name, draft._pendingKey);
      const { _pendingKey, ...clean } = draft;
      clean.models = (clean.models || []).filter((m) => m.name);
      providers.push(clean);
      await api.putSettings({ providers });
      closeModal();
      location.hash = `#/admin/provider/${clean.id}`;
    } catch (e) {
      err.textContent = "Could not save: " + (e.message || e);
      err.classList.remove("hidden");
    }
  }
}

// The model picker after a refresh: the provider's live list with the current rows pre-ticked
function refreshPicker(host, s, p, remote) {
  syncDetail(host, p);
  const listed = new Map((p.models || []).filter((m) => m.name)
    .map((m) => [m.name, m]));
  const gone = [...listed.keys()].filter((n) => !remote.includes(n));
  const row = (id, checked) => `<label class="form-check">
    <input type="checkbox" data-rm="${escapeHtml(id)}" ${checked ? "checked" : ""}>
    ${escapeHtml(id)}</label>`;
  const modal = openModal({
    title: `Models from ${p.name || p.id}`,
    body: `<div class="form-group">
        <input id="rmFilter" type="text" placeholder="Filter models..."></div>
      <div class="rm-list">${remote.map((id) => row(id, listed.has(id))).join("")
        || '<p class="muted">The provider returned no models.</p>'}</div>
      ${gone.length ? `<div class="io-sub">On your list, but no longer offered</div>
        <div class="rm-list rm-gone">${gone.map((id) => row(id, true)).join("")}</div>` : ""}
      <p class="form-hint">Tick what this provider should offer, then Apply.
        The page's Save settings makes it permanent.</p>`,
    actions: [
      { label: "Apply", kind: "btn-primary", onClick: () => {
          const ticked = [...modal.querySelectorAll("[data-rm]:checked")]
            .map((b) => b.dataset.rm);
          p.models = ticked.map((id) => listed.get(id)
            || { name: id, endpoint: "", cost: null, quality: null });
          closeModal();
          drawDetail(host, s, p);
          host.querySelector(".key-note").textContent =
            "Model list updated - review and press Save settings.";
        } },
      { label: "Cancel" },
    ],
  });
  const filter = modal.querySelector("#rmFilter");
  filter.oninput = () => {
    const q = filter.value.trim().toLowerCase();
    modal.querySelectorAll(".rm-list .form-check").forEach((l) => {
      l.classList.toggle("hidden",
        !!q && !l.textContent.toLowerCase().includes(q));
    });
  };
  filter.focus();
}

// What a card without models says: the vendor's list is one click away once a key is saved
function refreshNote(p) {
  return `Click "Refresh models" once you have saved an API key to load the latest model list from ${p.name || p.id}. You can refresh again to update the list if needed.`;
}

// The provider detail page
export async function renderProviderDetail(id) {
  const host = document.getElementById("adminProviderDetail");
  host.innerHTML = `<p class="muted">Loading&#8230;</p>`;
  const s = await api.getSettings();
  const p = (s.providers || []).find((x) => x.id === id);
  if (!p || !isWorkflow(p)) {
    host.innerHTML = `<a class="back-link" href="#/admin/workflow">&larr; Workflow AI providers</a>
      <p class="muted">This provider no longer exists.</p>`;
    return;
  }
  drawDetail(host, s, p);
}

// The wire-protocol select and endpoint sit at the bottom of custom forms; name, key and models are the real content
function apiPlumbing(p) {
  return `<div class="form-group"><label class="form-label">API type</label>
      <select data-f="adapter">
        <option value="openai" ${!["anthropic", "gemini"].includes(p.adapter) ? "selected" : ""}>OpenAI-compatible</option>
        <option value="anthropic" ${p.adapter === "anthropic" ? "selected" : ""}>Anthropic-compatible</option>
        <option value="gemini" ${p.adapter === "gemini" ? "selected" : ""}>Google Gemini (AI Studio or AI Platform)</option>
      </select>
      <p class="form-hint">Most hosted and local providers speak the
        OpenAI-compatible API. Google Gemini takes PDFs and images; for Google AI
        Platform set the endpoint to https://aiplatform.googleapis.com/v1/publishers/google
        and add the models by name.</p></div>
    <div class="form-group"><label class="form-label">Endpoint</label>
      <input data-f="endpoint" type="url" required value="${escapeHtml(p.endpoint || "")}"
             placeholder="https://..." />
      <p class="form-hint">The address the provider's API answers at - a hosted service, a proxy, or a local server such as Ollama.</p></div>`;
}

// The detail form: key, models, endpoint and the local tick for custom providers
function detailForm(p, s) {
  const custom = !isDefault(p, s);
  const local = (p.tags || []).includes("local");
  const isSet = !!(s.secrets || {})[p.key_name];
  return `<div class="provider-form">
    ${custom ? localTick(p) : ""}
    <div class="key-wrap${local ? " hidden" : ""}">${keyField(p, isSet, false)}</div>
    ${modelsBlock(p, { endpoint: custom, addable: custom || isSet })}
    <p class="hint">Optional: You can set a Cost and Quality value per model where 1 = lowest and 10 = highest. This helps the Builder Agent automatically select a model when building AI steps in your workflows.</p>
    ${custom ? apiPlumbing(p) : ""}
  </div>`;
}

// Reads the detail form back into the entry before a re-render
function syncDetail(host, p) {
  const form = host.querySelector(".provider-form");
  const val = (sel) => form.querySelector(sel)?.value;
  const name = val('[data-f="name"]');
  if (name !== undefined) p.name = name.trim() || p.name;
  const adapter = val('[data-f="adapter"]');
  if (adapter !== undefined) p.adapter = adapter;
  const localBox = form.querySelector('[data-f="local"]');
  if (localBox) {
    p.tags = (p.tags || []).filter((t) => t !== "local");
    if (localBox.checked) p.tags.push("local");
  }
  const endpoint = val('[data-f="endpoint"]');
  if (endpoint !== undefined) p.endpoint = endpoint.trim();
  const typed = readKey(form);
  if (typed) p._pendingKey = typed;
  p.models = readModels(form);
}

// Draws the provider detail page - header, form and delete
function drawDetail(host, s, p) {
  const custom = !isDefault(p, s);
  host.innerHTML = `
    <div class="page-head detail-head">
      <a class="back-link" href="#/admin/workflow">&larr; Workflow AI providers</a>
      <div class="page-head-row">
        <h1 id="provName">${escapeHtml(p.name || p.id)}</h1>
        <div class="head-actions">
          <button id="provRefresh" class="btn btn-secondary" ${providerKeySet(p, s) ? "" : "disabled"}
            title="${providerKeySet(p, s) ? "Fetch the provider's current model list and pick what to offer" : "Save an API key first"}">Refresh models</button>
          ${custom ? `<button id="provDelete" class="btn btn-danger">Delete provider</button>` : ""}
        </div>
      </div>
      ${custom ? `<p class="page-sub">Your own provider - key, address and models.</p>` : ""}
      ${p.reads_kinds ? `<p class="page-sub">File types supported: ${p.reads_kinds.length
          ? p.reads_kinds.map((k) => `<span class="tag">${k === "pdf" ? "PDF" : "Images"}</span>`).join(" ")
          : "text only"}</p>` : ""}
      <p class="hint" id="provTokens"></p>
    </div>
    <div class="admin-card">
      ${detailForm(p, s)}
      <div class="modal-actions">
        <button class="btn btn-primary save-provider">Save settings</button>
        <button class="btn btn-secondary test-provider" title="Save, then send one real request to check the provider answers">Test</button>
        <span class="save-status"></span>
      </div>
      <p class="form-hint key-note">${providerKeySet(p, s) ? "" : escapeHtml(refreshNote(p))}</p>
    </div>`;

  api.getProviderUsage().then((r) => {
    const line = host.querySelector("#provTokens");
    const t = (((r.usage || {})[p.id]) || {}).tokens;
    if (line && t && (t.in || t.out)) {
      line.textContent = `Last ${r.token_window_days || 30} days: `
        + `${humanTokens(t.in)} tokens in, ${humanTokens(t.out)} out.`;
    }
  }).catch(() => {});

  wireKeyFields(host);

  if (custom) {
    const paintProvName = () => inlineEdit(host.querySelector("#provName"), {
      value: p.name || "", label: "the name", maxlength: 60, required: true,
      type: "title", repaint: paintProvName,
      save: async (v) => {
        const fresh = await api.getSettings();
        const providers = fresh.providers || [];
        const i = providers.findIndex((x) => x.id === p.id);
        if (i >= 0) {
          providers[i] = { ...providers[i], name: v };
          await api.putSettings({ providers });
        }
        p.name = v;
      },
    });
    paintProvName();
  }
  const rerender = () => { syncDetail(host, p); drawDetail(host, s, p); };
  host.querySelector("#provRefresh").onclick = async () => {
    const btn = host.querySelector("#provRefresh");
    btn.disabled = true;
    btn.textContent = "Fetching...";
    const r = await api.refreshProviderModels(p.id).catch(() => null);
    btn.disabled = false;
    btn.textContent = "Refresh models";
    if (!r || !r.ok) {
      const note = host.querySelector(".key-note");
      note.textContent = !r ? "Couldn't reach the app - try again."
        : r.error_kind === "auth"
          ? (r.message || "Save an API key first - the model list comes from the provider.")
          : `Couldn't fetch the model list: ${r.message || "try again"}.`;
      return;
    }
    refreshPicker(host, s, p, r.models || []);
  };
  const localBox = host.querySelector('[data-f="local"]');
  if (localBox) localBox.onchange = rerender;
  const adapterSel = host.querySelector('[data-f="adapter"]');
  if (adapterSel) adapterSel.onchange = () => {
    syncDetail(host, p);

    if (!(p.models || []).some((m) => m.name)) {
      p.models = [];
    }
    drawDetail(host, s, p);
  };
  host.querySelector("[data-add-model]").onclick = () => {
    syncDetail(host, p);
    p.models.push({ name: "", endpoint: "", cost: null, quality: null });
    drawDetail(host, s, p);
  };
  host.querySelectorAll("[data-del-model]").forEach((btn) => {
    btn.onclick = () => { btn.closest(".model-row").remove(); rerender(); };
  });

  // Saves the form; the page is redrawn from the stored settings, so every element is fresh afterwards
  const saveProvider = async () => {
    const status = host.querySelector(".save-status");
    const note = host.querySelector(".key-note");
    status.textContent = "saving...";
    note.textContent = "";
    try {
      syncDetail(host, p);
      if (!isDefault(p, s) && !(p.endpoint || "").trim()) {
        status.textContent = "";
        note.textContent = "Give the provider its endpoint address.";
        return false;
      }
      const pending = p._pendingKey || "";
      if (pending && p.key_name) await api.putSecret(p.key_name, pending);
      const { _pendingKey, ...clean } = p;
      clean.models = (clean.models || []).filter((m) => m.name);
      const fresh = await api.getSettings();
      const providers = fresh.providers || [];
      const i = providers.findIndex((x) => x.id === p.id);
      if (i >= 0) providers[i] = clean; else providers.push(clean);
      await api.putSettings({ providers });
      const latest = await api.getSettings();
      drawDetail(host, latest,
                 (latest.providers || []).find((x) => x.id === p.id) || clean);
      host.querySelector(".save-status").textContent = "saved";

      if (pending && !(clean.tags || []).includes("local")) {
        const note2 = host.querySelector(".key-note");
        note2.textContent = "Checking the key...";
        const model = (clean.models[0] || {}).name || "";
        const r = await api.testProviderKey({
          adapter: clean.adapter, endpoint: clean.endpoint || "",
          model, key_name: clean.key_name }).catch(() => null);
        note2.textContent = !r ? ""
          : r.ok ? "Key check: ok"
          : r.error_kind === "auth" ? "The service refused this key - check it and paste it again."
          : "Couldn't check the key right now - it is saved and will be used as-is.";
      }
    } catch (e) {
      status.textContent = "failed: " + (e.message || e);
      return false;
    }
    return true;
  };
  host.querySelector(".save-provider").onclick = saveProvider;

  host.querySelector(".test-provider").onclick = async () => {
    const busy = (on) => {
      const b = host.querySelector(".test-provider");
      if (b) { b.disabled = on; b.textContent = on ? "Testing\u2026" : "Test"; }
    };
    busy(true);
    if (!(await saveProvider())) { busy(false); return; }
    busy(true);
    const note = host.querySelector(".key-note");
    try {
      const r = await api.testProvider(p.id);
      note.textContent = r.ok
        ? `Works: ${r.model} answered as asked.`
        : `Did not work${r.model ? ` (${r.model})` : ""}: ${r.message}`;
    } catch (e) {
      note.textContent = "Did not work: " + (e.message || e);
    } finally {
      busy(false);
    }
  };

  const del = host.querySelector("#provDelete");
  if (del) del.onclick = async () => {
    const u = ((await api.getProviderUsage().catch(() => ({ usage: {} })))
      .usage || {})[p.id] || { workflows: 0, nodes: 0 };
    openModal({
      title: `Delete ${p.name || "this provider"}?`,
      body: `<p>${u.workflows
          ? `${u.workflows} workflow${u.workflows > 1 ? "s" : ""} (${u.nodes} AI
             step${u.nodes > 1 ? "s" : ""}) currently use this provider's models -
             they will pause at those steps until another provider serves the
             same models.`
          : "No workflows use this provider."}
        Its saved key is deleted with it. This cannot be undone.</p>`,
      actions: [
        { label: "Delete", kind: "btn-danger", onClick: async () => {
            const fresh = await api.getSettings();
            const providers = (fresh.providers || []).filter((x) => x.id !== p.id);
            await api.putSettings({ providers });
            if (p.key_name) await api.deleteSecret(p.key_name).catch(() => {});
            closeModal();
            location.hash = "#/admin/workflow";
          } },
        { label: "Cancel" },
      ],
    });
  };
}
