// Admin tab for the one provider that powers the builder chat
import * as api from "./api.js";
import { escapeHtml } from "./util.js";
import { keyField, wireKeyFields, readKey, keyNameFor, catalogModels,
         modelsBlock, readModels, readPickedModel,
         isBuilderProvider } from "./providerForm.js";

const AUTH_TYPES = [
  ["api-key", "Claude API key"],
  ["claude-subscription", "Claude subscription"],
  ["codex-subscription", "ChatGPT subscription (Codex)"],
  ["codex-api-key", "OpenAI API key (Codex)"],
];
const CODEX_AUTHS = ["codex-subscription", "codex-api-key"];

const isBuilder = isBuilderProvider;

// The auth choice picks the family - Claude or Codex - and with it the adapter and starter models
const familyOf = (auth) => (CODEX_AUTHS.includes(auth) ? "codex" : "anthropic");

// The Builder AI tab: one provider entry, drawn from settings
export async function renderBuilderTab(root) {
  const s = await api.getSettings();
  draw(root, s);
}

const CLAUDE_PAGE = "https://docs.claude.com/en/docs/claude-code/setup";
const CODEX_PAGE = "https://developers.openai.com/codex/cli";
const pageLink = (url, text) => `<a href="${url}" target="_blank" rel="noopener">${text}</a>`;

// The per-auth explainer shown under the Authentication select
function authHint(auth) {
  if (auth === "claude-subscription") {
    return `<p class="form-hint">Your own Claude plan (Pro/Max) powers the Builder Agent chat
      through the Claude Code sign-in on this computer. Nothing is stored here:
      install Claude Code from ${pageLink(CLAUDE_PAGE, "its setup page")},
      run <code>claude</code> and sign in, and Cryogram uses that sign-in. AI steps
      inside workflows still need a workflow provider (next tab).</p>`;
  }
  if (auth === "codex-subscription") {
    return `<p class="form-hint">Your own ChatGPT plan powers the Builder Agent chat
      through the Codex program. Nothing is stored here: install it from
      ${pageLink(CODEX_PAGE, "its setup page")}, sign in with
      <code>codex login</code>, and Cryogram uses that sign-in. AI steps
      inside workflows still need a workflow provider (next tab).</p>`;
  }
  if (auth === "codex-api-key") {
    return `<p class="form-hint">An OpenAI API key, billed to that key, running
      the Builder Agent through the Codex program - so that has to be installed
      too, from ${pageLink(CODEX_PAGE, "its setup page")}. AI steps inside workflows
      use the workflow providers (next tab).</p>`;
  }
  return `<p class="form-hint">A standard Anthropic API key, billed to that key. The
     Builder Agent still runs through Claude Code, so that program has to be installed
     too, from ${pageLink(CLAUDE_PAGE, "its setup page")}. AI steps inside workflows
     use the workflow providers (next tab).</p>`;
}

// Which program the engine runs, its version and the floor, as one plain line
function programLine(prog, name) {
  if (!prog) return "";
  if (!prog.path) return `<p class="form-hint">${name} was not found on this computer.</p>`;
  const v = prog.version ? `${name} ${escapeHtml(prog.version)}` : `${name} (version unknown)`;
  const old = prog.too_old ? ` - Cryogram needs ${escapeHtml(prog.floor || "")} or newer` : "";
  return `<p class="form-hint ${prog.too_old ? "" : "ok"}">${v} at <code>${escapeHtml(prog.path)}</code>${old}</p>`;
}

// The Check connection block: is the program's sign-in present on this machine (Claude Code or Codex)
function signInBlock(s, auth) {
  const isClaude = auth === "claude-subscription";
  const connected = !!(isClaude ? s.claude_connected : s.codex_connected);
  const program = programLine(isClaude ? s.claude_program : s.codex_program,
                              isClaude ? "Claude Code" : "Codex");
  const found = isClaude ? "Claude Code sign-in found on this machine."
    : "Codex sign-in found on this machine.";
  const missing = isClaude
    ? "No Claude Code sign-in found yet - run <code>claude</code> in a terminal and sign in."
    : "No Codex sign-in found yet - run <code>codex login</code> in a terminal.";
  return `<div class="form-group">
    ${program}
    <p class="form-hint ${connected ? "ok" : ""}" id="signInState">${connected ? found : missing}</p>
    <button type="button" class="btn btn-secondary" id="signInCheck" data-auth="${auth}">Check connection</button>
    <span class="save-status" id="signInCheckOut"></span>
  </div>`;
}

// The builder form: auth select, credential field and model rows
function builderForm(p, s) {
  const isSet = !!(s.secrets || {})[p.key_name];
  const auth = p.auth || "api-key";
  const isSub = auth === "claude-subscription";
  const noSecret = auth === "codex-subscription" || auth === "claude-subscription";
  return `<div class="provider-form">
    <div class="form-group"><label class="form-label">Authentication</label>
      <select data-f="auth">${AUTH_TYPES.map(([v, label]) =>
        `<option value="${v}" ${v === auth ? "selected" : ""}>${label}</option>`).join("")}
      </select>
      ${authHint(auth)}
    </div>
    ${noSecret ? signInBlock(s, auth) : keyField(p, isSet, isSub)}
    ${modelsBlock(p, { pick: true, picked: s.master_ai?.model || "" })}
    <p class="hint">Tick the model that runs the Builder Agent chat. With none ticked it
      runs ${familyOf(auth) === "codex" ? "Codex" : "Claude Code"}'s own default model.
      Models for individual workflow steps are chosen by the AI as it builds.</p>
    ${effortField(s, familyOf(auth))}
  </div>`;
}

// The thinking-effort dropdown for the chosen engine family, the default level labelled as such
function effortField(s, family) {
  const levels = (s.effort_levels || {})[family] || [];
  if (!levels.length) return "";
  const dflt = s.effort_default || "medium";
  const current = levels.includes(s.master_ai?.effort) ? s.master_ai.effort : dflt;
  const label = (l) => l.charAt(0).toUpperCase() + l.slice(1) + (l === dflt ? " (default)" : "");
  return `<div class="form-group"><label class="form-label">Thinking effort</label>
    <select data-f="effort">${levels.map((l) =>
      `<option value="${l}" ${l === current ? "selected" : ""}>${label(l)}</option>`).join("")}
    </select>
    <p class="form-hint">How long the AI thinks before each step. Lower is faster and
      spends fewer tokens; higher thinks longer before each decision. Applies from
      your next message.</p>
  </div>`;
}

// The model the shipped list marks as its default, else the first
function shippedDefault(starters) {
  return (starters || []).find((m) => m.default)?.name || starters?.[0]?.name || "";
}

// Reads the whole form back before any re-render, so a structural change never resets a typed field
function sync(root, p) {
  const form = root.querySelector(".provider-form");
  if (!form) return;
  p.auth = form.querySelector('[data-f="auth"]')?.value || "api-key";
  p._effort = form.querySelector('[data-f="effort"]')?.value || "";
  const typed = readKey(form);
  if (typed) p._pendingKey = typed;

  p.models = readModels(form).map((m) => ({ name: m.name }));
  p._picked = readPickedModel(form);
}

// Draws the whole tab from the settings payload
function draw(root, s) {
  const builder = (s.providers || []).find(isBuilder);

  root.innerHTML = `<div class="admin-card">
    <div class="d-section">
      <p class="hint">The chat that designs and changes your workflows - one
        choice, used everywhere. It runs on Claude: pay with an API key, or
        use your own Claude subscription.</p>
      ${builder ? builderForm(builder, s)
        : `<p class="muted">No builder set up yet.</p>
           <div class="add-model-row"><button class="btn btn-secondary" data-add-builder>+ Set up the Builder Agent</button></div>`}
    </div>
    ${builder ? `<div class="modal-actions">
      <button class="btn btn-primary save-all">Save settings</button>
      <span class="save-status"></span></div>` : ""}
  </div>`;

  root.querySelector("[data-add-builder]")?.addEventListener("click", () => {
    const starters = catalogModels(s, { id: "builder", adapter: "anthropic" });
    const models = starters.map((m) => ({ name: m.name }));
    s.providers = s.providers || [];
    s.providers.push({ id: "builder", name: "Builder", adapter: "anthropic",
                       auth: "api-key", use: "builder", enabled: true,
                       endpoint: "", tags: [],
                       key_name: keyNameFor("anthropic", s.providers), models });
    if (!s.master_ai?.model && models[0]) {
      s.master_ai = { ...s.master_ai, model: shippedDefault(starters) };
    }
    draw(root, s);
  });
  if (!builder) return;

  wireKeyFields(root);
  root.querySelector('[data-f="auth"]').onchange = () => {
    const prevFamily = familyOf(builder.auth);
    sync(root, builder);
    if (familyOf(builder.auth) !== prevFamily) {

      const adapter = familyOf(builder.auth) === "codex" ? "codex" : "anthropic";
      builder.adapter = adapter;
      const starters = catalogModels(s, { id: "", adapter });
      builder.models = starters.map((m) => ({ name: m.name }));
      if (builder.models[0]) {
        s.master_ai = { ...s.master_ai, model: shippedDefault(starters) };
      }

      s.master_ai = { ...s.master_ai, effort: s.effort_default || "medium" };
    }
    draw(root, s);
  };
  const check = root.querySelector("#signInCheck");
  if (check) check.onclick = async () => {
    const out = root.querySelector("#signInCheckOut");
    out.textContent = "checking...";
    const ask = check.dataset.auth === "claude-subscription" ? api.checkClaude : api.checkCodex;
    const r = await ask()
      .catch((e) => ({ ok: false, message: String(e.message || e) }));
    const state = root.querySelector("#signInState");
    state.textContent = r.message || (r.ok ? "Signed in and ready." : "Not connected.");
    state.classList.toggle("ok", !!r.ok);
    out.textContent = r.ok ? "connected" : "";
  };
  root.querySelector("[data-add-model]").onclick = () => {
    sync(root, builder);
    builder.models.push({ name: "" });
    draw(root, s);
  };
  root.querySelectorAll("[data-del-model]").forEach((btn) => {
    btn.onclick = () => {
      btn.closest(".model-row").remove();
      sync(root, builder);

      if (!builder._picked && builder.models[0]) {
        s.master_ai = { ...s.master_ai, model: builder.models[0].name };
      } else if (builder._picked) {
        s.master_ai = { ...s.master_ai, model: builder._picked };
      }
      draw(root, s);
    };
  });
  root.querySelectorAll('[data-m="pick"]').forEach((r) => {
    r.onchange = () => {
      sync(root, builder);
      if (builder._picked) s.master_ai = { ...s.master_ai, model: builder._picked };
    };
  });

  root.querySelector(".save-all").onclick = async () => {
    const status = root.querySelector(".save-status");
    status.textContent = "saving...";
    try {
      sync(root, builder);
      const picked = builder._picked || builder.models[0]?.name || "";
      if (builder._pendingKey && builder.key_name) {
        await api.putSecret(builder.key_name, builder._pendingKey);
      }
      const effort = builder._effort || s.master_ai?.effort || s.effort_default || "medium";
      const { _pendingKey, _picked, _effort, ...clean } = builder;
      clean.models = (clean.models || []).filter((m) => m.name);
      const fresh = await api.getSettings();
      const providers = fresh.providers || [];
      const i = providers.findIndex(isBuilder);
      if (i >= 0) providers[i] = clean; else providers.unshift(clean);

      await api.putSettings({ providers, master_ai: { model: picked, effort } });
      draw(root, await api.getSettings());
      root.querySelector(".save-status").textContent = "saved";
    } catch (e) {
      status.textContent = "failed: " + (e.message || e);
    }
  };
}
