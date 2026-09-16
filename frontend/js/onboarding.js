// First-run setup: a four-step guide from nothing to a working configuration
import * as api from "./api.js";
import { escapeHtml, slugName } from "./util.js";
import { openModal, closeModal } from "./modal.js";
import { keyField, wireKeyFields, readKey, keyNameFor, catalogModels,
         modelsBlock, readModels, localTick, keyStateTag,
         providerKeySet, isBuilderProvider, mintId, defaultProviderIds } from "./providerForm.js";

const isBuilder = isBuilderProvider;
const isWorkflow = (p) => !isBuilder(p);

// Configured means a credential and a model pick; a subscription's credential is the program's own sign-in
function builderConfigured(s) {
  const b = (s.providers || []).find(isBuilder);
  if (!b || !s.master_ai?.model) return false;
  if (b.auth === "codex-subscription") return !!s.codex_connected;
  if (b.auth === "claude-subscription") return !!s.claude_connected;
  return !!(s.secrets || {})[b.key_name];
}

let autoOpened = false;

// Auto-opens only while the builder is not set up; the sidebar button reopens it any time
export function maybeAutoOpenOnboarding(s) {
  if (autoOpened || !s || !s.providers) return;
  if (builderConfigured(s)) return;
  autoOpened = true;
  openOnboarding();
}

const TITLES = ["Welcome to Cryogram", "Choose your Builder Agent",
                "Workflow AI providers", "You're set"];

// The four-step guide
export function openOnboarding() {
  const state = { step: 0, choice: "", picked: "", sameKeyChoice: "" };
  const root = openModal({ title: TITLES[0], body: "", actions: [] });
  renderStep(root, state);
}

// Replaces the modal's action buttons
function setActions(root, actions) {
  const host = root.querySelector(".modal-actions");
  host.innerHTML = "";
  actions.forEach((a) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `btn ${a.kind || "btn-secondary"}`;
    b.textContent = a.label;
    if (a.id) b.id = a.id;
    if (a.disabled) b.disabled = true;
    b.onclick = a.onClick || closeModal;
    host.appendChild(b);
  });
}

// Draws the current step of the setup guide
async function renderStep(root, state) {
  if (!document.contains(root)) return;
  root.querySelector("h2").textContent = TITLES[state.step];
  const body = root.querySelector(".modal-body");
  if (state.step === 0) return stepWelcome(root, state, body);
  if (state.step === 1) return stepBuilder(root, state, body);
  if (state.step === 2) return stepWorkflow(root, state, body);
  return stepDone(root, state, body);
}

const stepLine = (n) => `<p class="hint">Step ${n} of 4</p>`;

// Step 1: what Cryogram is
function stepWelcome(root, state, body) {
  body.innerHTML = `${stepLine(1)}
    <p>Describe a task in chat and the Builder Agent does it with you once -
      then turns what worked into a workflow you own. Every step that doesn't
      need judgement becomes plain code that runs on your machine, checked on
      every run; AI stays only where each run genuinely needs it.</p>
    <p>Two quick choices and you're ready: the AI that <b>builds</b> your
      workflows, and the AI your workflows <b>use when they run</b>.</p>`;
  setActions(root, [
    { label: "Not now" },
    { label: "Next", kind: "btn-primary",
      onClick: () => { state.step = 1; renderStep(root, state); } },
  ]);
}

const CLAUDE_PAGE = "https://docs.claude.com/en/docs/claude-code/setup";
const CODEX_PAGE = "https://developers.openai.com/codex/cli";
const pageLink = (url, text) => `<a href="${url}" target="_blank" rel="noopener">${text}</a>`;

// The line under a program check: what the last check said, or what to do first
function programState(state, program, needSignIn) {
  const checked = program === "claude" ? state.claudeChecked : state.codexChecked;
  const ok = program === "claude" ? state.claudeOk : state.codexOk;
  const installed = program === "claude" ? state.claudeInstalled : state.codexInstalled;
  const msg = program === "claude" ? state.claudeMsg : state.codexMsg;
  const name = program === "claude" ? "Claude Code" : "Codex";
  if (!checked) return needSignIn ? "When you've signed in, check the connection below."
    : "Checking that " + name + " is installed...";
  if (needSignIn) return ok ? "Signed in and ready." : escapeHtml(msg || "Not signed in yet.");
  return installed ? name + " is installed." : escapeHtml(msg || name + " isn't installed yet.");
}

// One instruction fragment per builder choice
function builderChoiceForm(choice, state) {
  if (choice === "subscription") {
    return `<div class="form-group">
        <p class="form-hint">Cryogram uses the Claude Code sign-in on this computer. If you
          don't have Claude Code yet:<br>
          1. Install it from ${pageLink(CLAUDE_PAGE, "its setup page")}.<br>
          2. Open the Terminal app, run <code>claude</code> and sign in when it asks.<br>
          Nothing is pasted here.</p>
        <p class="form-hint ${state.claudeOk ? "ok" : ""}" id="obClaudeState">${programState(state, "claude", true)}</p>
        <button type="button" class="btn btn-secondary" id="obClaudeCheck">Check connection</button>
      </div>`;
  }
  if (choice === "api") {
    return `<div class="form-group">
        <p class="form-hint">Create a key at console.anthropic.com &gt; API keys,
          then paste it here. It is stored on this machine and never shown again.
          The Builder Agent still runs through Claude Code, so that program has to be
          installed too - from ${pageLink(CLAUDE_PAGE, "its setup page")} if it isn't yet.</p>
        <p class="form-hint ${state.claudeInstalled ? "ok" : ""}" id="obClaudeState">${programState(state, "claude", false)}</p>
        <button type="button" class="btn btn-secondary" id="obClaudeCheck">Check again</button>
      </div>
      <div class="form-group">
        <label class="form-label">API key</label>
        <input type="password" id="obKey" placeholder="Paste key" autocomplete="off" />
      </div>`;
  }
  if (choice === "codex-sub") {
    return `<div class="form-group">
        <p class="form-hint">1. Install Codex from ${pageLink(CODEX_PAGE, "its setup page")}, if you
          don't have it yet.<br>
          2. Open the Terminal app, run <code>codex login</code> and finish the sign-in in your browser.<br>
          Nothing is pasted here - Cryogram uses that sign-in directly.</p>
        <p class="form-hint ${state.codexOk ? "ok" : ""}" id="obCodexState">${programState(state, "codex", true)}</p>
        <button type="button" class="btn btn-secondary" id="obCodexCheck">Check connection</button>
      </div>`;
  }
  return `<div class="form-group">
      <p class="form-hint">An OpenAI API key from platform.openai.com. Stored on this machine
        and never shown again. The Builder Agent runs through the Codex program, so that
        has to be installed too - from ${pageLink(CODEX_PAGE, "its setup page")} if it isn't yet.</p>
      <p class="form-hint ${state.codexInstalled ? "ok" : ""}" id="obCodexState">${programState(state, "codex", false)}</p>
      <button type="button" class="btn btn-secondary" id="obCodexCheck">Check again</button>
    </div>
    <div class="form-group">
      <label class="form-label">API key</label>
      <input type="password" id="obKey" placeholder="Paste key" autocomplete="off" />
    </div>`;
}

// Step 2: choose and configure the builder AI
async function stepBuilder(root, state, body) {
  const s = await api.getSettings();
  if (!document.contains(root)) return;
  const configured = builderConfigured(s);

  if (!state.choice) {
    const b = (s.providers || []).find(isBuilder);
    state.choice = (b && CHOICE_BY_AUTH[b.auth]) || "";
  }
  const choice = state.choice;
  const option = (value, name, note) => `<button type="button" class="btn ${choice === value ? "btn-primary" : "btn-secondary"}" data-choice="${value}">${name}<span class="ob-choice-note">${note}</span></button>`;
  body.innerHTML = `${stepLine(2)}
    <p>The Builder Agent is the automation expert you chat with - it designs, tests and
      repairs your workflows. It runs on Claude or on Codex (ChatGPT) -
      subscription or API key either way.</p>
    ${configured ? `<p class="form-hint ok">Your builder is already set up -
      continue, or pick an option below to change it.</p>` : ""}
    <div class="ob-choices">
      ${option("subscription", "Claude subscription", "uses your Claude Pro/Max plan - flat cost")}
      ${option("api", "Claude API key", "pay per use, billed to the key")}
      ${option("codex-sub", "ChatGPT subscription (Codex)", "uses your ChatGPT plan - flat cost")}
      ${option("codex-api", "OpenAI API key (Codex)", "pay per use, billed to the key")}
    </div>
    ${choice ? builderChoiceForm(choice, state) : ""}
    <p class="form-error hidden" id="obErr"></p>`;

  body.querySelectorAll(".ob-choices .btn").forEach((b) => {
    b.onclick = () => { state.choice = b.dataset.choice; renderStep(root, state); };
  });
  const runClaudeCheck = async () => {
    const r = await api.checkClaude()
      .catch((e) => ({ ok: false, message: String(e.message || e) }));
    state.claudeChecked = true;
    state.claudeOk = !!r.ok;
    state.claudeInstalled = !!r.installed;
    state.claudeMsg = r.message || "";
    renderStep(root, state);
  };
  const runCodexCheck = async () => {
    const r = await api.checkCodex()
      .catch((e) => ({ ok: false, message: String(e.message || e) }));
    state.codexChecked = true;
    state.codexOk = !!r.ok;
    state.codexInstalled = !!r.installed;
    state.codexMsg = r.message || "";
    renderStep(root, state);
  };
  const claudeCheck = body.querySelector("#obClaudeCheck");
  if (claudeCheck) claudeCheck.onclick = () => {
    claudeCheck.disabled = true;
    claudeCheck.textContent = "Checking...";
    runClaudeCheck();
  };
  const codexCheck = body.querySelector("#obCodexCheck");
  if (codexCheck) codexCheck.onclick = () => {
    codexCheck.disabled = true;
    codexCheck.textContent = "Checking...";
    runCodexCheck();
  };

  if (choice === "api" && !state.claudeChecked) runClaudeCheck();
  if (choice === "codex-api" && !state.codexChecked) runCodexCheck();

  const key = body.querySelector("#obKey");
  const refresh = () => {
    const confirm = root.querySelector("#obConfirm");
    if (confirm) {
      confirm.disabled = choice === "codex-sub" ? !state.codexOk
        : choice === "subscription" ? !state.claudeOk
        : !key?.value.trim();
    }
  };
  if (key) { key.oninput = refresh; setTimeout(() => key.focus(), 0); }

  const actions = [
    { label: "Back", onClick: () => { state.step = 0; renderStep(root, state); } },
  ];
  if (configured && !choice) {
    actions.push({ label: "Continue", kind: "btn-primary",
      onClick: () => { state.step = 2; renderStep(root, state); } });
  } else {
    actions.push({ label: "Confirm and continue", kind: "btn-primary",
      id: "obConfirm", disabled: true,
      onClick: () => saveBuilder(root, state, body) });
  }
  setActions(root, actions);
  refresh();
}

// Prefers a sonnet-tier model as the default builder pick
function defaultMaster(models) {

  const names = (models || []).map((m) => m.name).filter(Boolean);
  return (models || []).find((m) => m.default)?.name || names[0] || "";
}

const AUTH_BY_CHOICE = {
  subscription: "claude-subscription", api: "api-key",
  "codex-sub": "codex-subscription", "codex-api": "codex-api-key",
};
const CHOICE_BY_AUTH = Object.fromEntries(Object.entries(AUTH_BY_CHOICE).map(([c, a]) => [a, c]));

// Saves the builder choice and confirms the credential
async function saveBuilder(root, state, body) {
  const err = body.querySelector("#obErr");
  const codexFamily = state.choice.startsWith("codex");
  const noSecret = state.choice === "codex-sub" || state.choice === "subscription";
  const token = (body.querySelector("#obKey")?.value || "").trim();
  if (!noSecret && !token) return;
  const confirm = root.querySelector("#obConfirm");
  confirm.disabled = true;
  confirm.textContent = "Checking...";
  err.classList.add("hidden");
  try {
    const fresh = await api.getSettings();
    const providers = fresh.providers || [];
    const adapter = codexFamily ? "codex" : "anthropic";
    let b = providers.find(isBuilder);
    if (!b) {
      b = { id: "builder", name: "Builder", adapter,
            use: "builder", enabled: true, endpoint: "", tags: [],
            key_name: keyNameFor(adapter, providers),
            models: catalogModels(fresh, { id: "builder", adapter })
              .map((m) => ({ name: m.name })) };
      providers.unshift(b);
    } else if (b.adapter !== adapter) {

      b.adapter = adapter;
      b.models = catalogModels(fresh, { id: "", adapter })
        .map((m) => ({ name: m.name }));
    }
    b.auth = AUTH_BY_CHOICE[state.choice] || "api-key";
    const existing = fresh.master_ai?.model;
    const master = (existing && (b.models || []).some((m) => m.name === existing))
      ? existing : defaultMaster(b.models);
    if (state.choice === "api") {

      const r = await api.testProviderKey({ adapter: "anthropic", endpoint: "",
        model: master || "", key: token }).catch(() => ({ ok: true }));
      if (!r.ok && r.error_kind === "auth") {
        err.textContent = "That key was refused - check it and paste it again.";
        err.classList.remove("hidden");
        confirm.textContent = "Confirm and continue";
        confirm.disabled = false;
        return;
      }
    }
    if (!noSecret) await api.putSecret(b.key_name, token);
    await api.putSettings({ providers, master_ai: { model: master } });
    state.step = 2;
    renderStep(root, state);
  } catch (e) {
    err.textContent = "Could not save: " + (e.message || e);
    err.classList.remove("hidden");
    confirm.textContent = "Confirm and continue";
    confirm.disabled = false;
  }
}

// Step 3: the workflow AI providers
async function stepWorkflow(root, state, body) {
  const s = await api.getSettings();
  if (!document.contains(root)) return;
  const providers = s.providers || [];
  const builder = providers.find(isBuilder);
  const defaults = defaultProviderIds(s).map((id) => providers.find((p) => p.id === id))
    .filter(Boolean);
  const options = defaults.map((p) => `
    <label class="form-check"><input type="radio" name="obProv" value="${escapeHtml(p.id)}"
      ${state.picked === p.id ? "checked" : ""}> ${escapeHtml(p.name || p.id)}
      ${keyStateTag(p, s)}</label>`).join("");

  body.innerHTML = `${stepLine(3)}
    <p>The Builder Agent designs your workflows; <b>these</b> providers run the AI
      steps inside them - a separate choice, so you can track and control that
      spend on its own. A Claude subscription can never run workflow steps.</p>
    <div class="form-group">
      ${options}
      <label class="form-check"><input type="radio" name="obProv" value="custom"
        ${state.picked === "custom" ? "checked" : ""}> Custom
        <span class="tag">Any provider or local server</span></label>
    </div>
    <div id="obProvPanel"></div>
    <p class="form-error hidden" id="obErr3"></p>`;

  body.querySelectorAll('[name="obProv"]').forEach((r) => {
    r.onchange = () => { state.picked = r.value; state.sameKeyChoice = ""; renderStep(root, state); };
  });
  const panel = body.querySelector("#obProvPanel");
  const picked = state.picked === "custom" ? "custom"
    : defaults.find((p) => p.id === state.picked);
  if (picked === "custom") customPanel(root, state, s, panel);
  else if (picked) defaultPanel(root, state, s, picked, panel, builder);

  setActions(root, [
    { label: "Back", onClick: () => { state.step = 1; renderStep(root, state); } },
    { label: "Continue", kind: "btn-primary", onClick: () => {
        state.step = 3; renderStep(root, state);
      } },
  ]);
}

// A default provider's panel: done, reuse the builder's key, or paste one
function defaultPanel(root, state, s, p, panel, builder) {
  if (providerKeySet(p, s)) {
    panel.innerHTML = `<p class="form-hint ok">Key already saved - nothing else
      needed for ${escapeHtml(p.name || p.id)}.</p>`;
    return;
  }

  const canShare = builder && builder.auth === "api-key"
    && (s.secrets || {})[builder.key_name] && builder.adapter === p.adapter;
  if (canShare && !state.sameKeyChoice) {
    panel.innerHTML = `<div class="form-group">
      <p class="form-hint">Your builder already uses a ${escapeHtml(p.name)} API key.</p>
      <label class="form-check"><input type="radio" name="obSame" value="different">
        Use a different key
        <span class="label-optional">recommended - builder and workflow usage stay separately trackable</span></label>
      <label class="form-check"><input type="radio" name="obSame" value="same">
        Use the same key</label>
    </div>`;
    panel.querySelectorAll('[name="obSame"]').forEach((r) => {
      r.onchange = async () => {
        state.sameKeyChoice = r.value;
        if (r.value === "same") {
          try {
            const fresh = await api.getSettings();
            const provs = fresh.providers || [];
            const target = provs.find((x) => x.id === p.id);
            const b = provs.find(isBuilder);
            if (target && b) {
              target.key_name = b.key_name;
              await api.putSettings({ providers: provs });
            }
          } catch { }
        }
        renderStep(root, state);
      };
    });
    return;
  }

  panel.innerHTML = `<div class="provider-form">
    ${keyField(p, false, false)}
    <button type="button" class="btn btn-secondary" id="obProvSave">Save key</button>
  </div>`;
  wireKeyFields(panel);
  panel.querySelector("#obProvSave").onclick = async () => {
    const key = readKey(panel);
    if (!key) return;
    const btn = panel.querySelector("#obProvSave");
    btn.disabled = true;
    btn.textContent = "Saving...";
    try {
      await api.putSecret(p.key_name, key);
      renderStep(root, state);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Save key";
      const err = root.querySelector("#obErr3");
      err.textContent = "Could not save: " + (e.message || e);
      err.classList.remove("hidden");
    }
  };
}

// The custom panel - the same fields the Admin provider page uses
function customPanel(root, state, s, panel) {
  const draft = state.customDraft
    || (state.customDraft = { id: mintId(), name: "", adapter: "openai",
                              auth: "api-key", use: "workflow", enabled: true,
                              endpoint: "", key_name: "", tags: [],
                              models: [] });
  const local = (draft.tags || []).includes("local");

  panel.innerHTML = `<div class="provider-form">
    <div class="form-group"><label class="form-label">Name</label>
      <input data-f="name" type="text" value="${escapeHtml(draft.name)}"
             placeholder="e.g. Mistral, my Ollama server" /></div>
    ${localTick(draft)}
    <div class="key-wrap${local ? " hidden" : ""}">${keyField(draft, false, false)}</div>
    ${modelsBlock(draft, { endpoint: true })}
    <div class="form-group"><label class="form-label">API type</label>
      <select data-f="adapter">
        <option value="openai" ${!["anthropic", "gemini"].includes(draft.adapter) ? "selected" : ""}>OpenAI-compatible</option>
        <option value="anthropic" ${draft.adapter === "anthropic" ? "selected" : ""}>Anthropic-compatible</option>
        <option value="gemini" ${draft.adapter === "gemini" ? "selected" : ""}>Google Gemini (AI Studio or AI Platform)</option>
      </select></div>
    <div class="form-group"><label class="form-label">Endpoint
        <span class="label-optional">optional</span></label>
      <input data-f="endpoint" type="url" value="${escapeHtml(draft.endpoint)}"
             placeholder="https://... - blank uses the provider's standard address" /></div>
    <button type="button" class="btn btn-secondary" id="obCustomSave">Add this provider</button>
  </div>`;
  wireKeyFields(panel);

  const sync = () => {
    draft.name = panel.querySelector('[data-f="name"]').value.trim();
    draft.adapter = panel.querySelector('[data-f="adapter"]').value;
    draft.endpoint = panel.querySelector('[data-f="endpoint"]').value.trim();
    draft.tags = panel.querySelector('[data-f="local"]').checked ? ["local"] : [];
    const typed = readKey(panel);
    if (typed) draft._pendingKey = typed;
    draft.models = readModels(panel);
  };
  panel.querySelector('[data-f="local"]').onchange = () => { sync(); renderStep(root, state); };
  panel.querySelector('[data-f="adapter"]').onchange = () => {
    sync();
    if (!(draft.models || []).some((m) => m.name)) {
      draft.models = [];
    }
    renderStep(root, state);
  };
  panel.querySelector("[data-add-model]").onclick = () => {
    sync();
    draft.models.push({ name: "", endpoint: "", cost: null, quality: null });
    renderStep(root, state);
  };
  panel.querySelectorAll("[data-del-model]").forEach((btn) => {
    btn.onclick = () => { btn.closest(".model-row").remove(); sync(); renderStep(root, state); };
  });
  panel.querySelector("#obCustomSave").onclick = async () => {
    sync();
    const err = root.querySelector("#obErr3");
    err.classList.add("hidden");
    if (!draft.name) {
      err.textContent = "Give the provider a name.";
      err.classList.remove("hidden");
      return;
    }
    try {
      const fresh = await api.getSettings();
      const provs = fresh.providers || [];
      if (!draft.key_name) {
        draft.key_name = keyNameFor(slugName(draft.name) || draft.adapter, provs);
      }
      if (draft._pendingKey) await api.putSecret(draft.key_name, draft._pendingKey);
      const { _pendingKey, ...clean } = draft;
      clean.models = (clean.models || []).filter((m) => m.name);
      const i = provs.findIndex((x) => x.id === clean.id);
      if (i >= 0) provs[i] = clean; else provs.push(clean);
      await api.putSettings({ providers: provs });
      state.customDraft = null;
      state.picked = "";
      renderStep(root, state);
    } catch (e) {
      err.textContent = "Could not save: " + (e.message || e);
      err.classList.remove("hidden");
    }
  };
}

// Step 4: done
function stepDone(root, state, body) {
  body.innerHTML = `${stepLine(4)}
    <p>You're set. Create your first workflow from the Workflows page -
      describe the task in the chat and the Builder Agent takes it from there.</p>
    <p class="form-hint">While it builds, it asks before it opens a browser window, connects somewhere new or sends anything for real. Opening a page is meant only to read it, but the site can still record that you visited, so a visit can leave a trace even when nothing is sent.</p>
    <p class="form-hint">Everything here can be changed later in Admin, and
      this guide reopens from the sidebar any time.</p>`;
  setActions(root, [
    { label: "Back", onClick: () => { state.step = 2; renderStep(root, state); } },
    { label: "Finish", kind: "btn-primary", onClick: () => {
        closeModal();
        location.hash = "#/";
      } },
  ]);
}
