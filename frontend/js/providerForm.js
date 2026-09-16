// Shared provider-form pieces: the key field, model rows and the local tick, used by both admin tabs
import { escapeHtml } from "./util.js";

// A fresh provider card id
export const mintId = () => "p_" + crypto.randomUUID().replace(/-/g, "").slice(0, 8);
// The server owns the default-provider list; the fallback covers a mid-upgrade stale server
export const defaultProviderIds = (s) => s.default_provider_ids || ["anthropic", "openai", "gemini"];
const KEY_MASK = "••••••••";

const BUILDER_AUTHS = ["claude-subscription", "codex-subscription",
                              "codex-api-key"];
// Is this the builder's own provider entry
export const isBuilderProvider = (p) =>
  p.use === "builder" || BUILDER_AUTHS.includes(p.auth);

// One key per provider, under a stable generated secret name
export function keyNameFor(prefix, providers) {
  const taken = new Set((providers || []).map((p) => p.key_name).filter(Boolean));
  const base = (prefix || "provider").toUpperCase().replace(/[^A-Z0-9]+/g, "_");
  let name = `${base}_API_KEY`, n = 2;
  while (taken.has(name)) name = `${base}_${n++}_API_KEY`;
  return name;
}

// The one predicate for a provider's credential state
export const providerKeySet = (p, s) =>
  (p.tags || []).includes("local") || !!(((s || {}).secrets) || {})[p.key_name];

// The key-state pill
export function keyStateTag(p, s) {
  if ((p.tags || []).includes("local")) return `<span class="tag">Local</span>`;
  return (((s || {}).secrets) || {})[p.key_name]
    ? `<span class="tag tag-key-set">Key set</span>`
    : `<span class="tag">No key</span>`;
}

// Starter models: the provider's own catalogue entry first, else its adapter's
export function catalogModels(settings, p) {
  const cat = settings.model_catalog || {};
  const rows = cat[p.id] || cat[p.adapter] || [];
  return rows.map((m) => ({ name: m.name, endpoint: "",
                            cost: m.cost || 5, quality: m.quality || 5,
                            ...(m.default ? { default: true } : {}) }));
}

// The key field shows a mask when a key is stored; the real value is never sent back to the page
export function keyField(p, isSet, isSub) {
  const shown = p._pendingKey ? escapeHtml(p._pendingKey) : (isSet ? KEY_MASK : "");
  return `<div class="form-group"><label class="form-label">${isSub ? "Subscription token" : "API key"}
      <span class="${isSet || p._pendingKey ? "ok" : "label-optional"}">${
        p._pendingKey ? "will be saved" : (isSet ? "set" : "not set")}</span></label>
    <input type="password" data-key-input data-key-set="${isSet ? "1" : ""}"
           value="${shown}"
           placeholder="${isSub ? "Paste token" : "Paste key"}" />
    ${p.key_url && !isSub ? `<p class="form-hint">Get your key here: <a href="${escapeHtml(p.key_url)}" target="_blank" rel="noopener">${escapeHtml(String(p.key_url).replace(/^https?:\/\//, ""))}</a></p>` : ""}
  </div>`;
}

// Mask handling for key fields: focus clears for a paste, blur restores it if nothing was typed
export function wireKeyFields(root) {
  root.querySelectorAll("[data-key-input]").forEach((ki) => {
    ki.onfocus = () => { if (ki.value === KEY_MASK) ki.value = ""; };
    ki.onblur = () => { if (!ki.value && ki.dataset.keySet) ki.value = KEY_MASK; };
  });
}

// The typed-but-unsaved key; the mask is never a value
export function readKey(root) {
  const ki = root.querySelector("[data-key-input]");
  return ki && ki.value && ki.value !== KEY_MASK ? ki.value : "";
}

// The local no-key tick, for servers on this computer
export function localTick(p) {
  return `<div class="form-group"><label class="form-check">
      <input type="checkbox" data-f="local" ${(p.tags || []).includes("local") ? "checked" : ""} />
      Local - no API key needed
      <span class="label-optional">for a server on this computer, such as Ollama</span>
    </label></div>`;
}

// One model row, in whichever column shape the variant uses
function modelRow(m, { endpoint, pick, picked }) {
  if (pick) {
    return `<div class="model-row builder-pick">
      <input type="radio" name="builderModel" data-m="pick"
             ${m.name && m.name === picked ? "checked" : ""}
             title="Use this model for the Builder Agent" aria-label="Use this model" />
      <input data-m="name" value="${escapeHtml(m.name || "")}" placeholder="Model ID" />
      <button class="btn btn-secondary btn-sm" data-del-model title="Remove model">&times;</button>
    </div>`;
  }
  return `<div class="model-row${endpoint ? "" : " no-endpoint"}">
    <input data-m="name" value="${escapeHtml(m.name || "")}" placeholder="Model ID" />
    ${endpoint ? `<input data-m="endpoint" value="${escapeHtml(m.endpoint || "")}" placeholder="Endpoint (optional)" />` : ""}
    <input data-m="cost" type="number" min="1" max="10" value="${rankValue(m.cost)}" placeholder="-" title="Cost 1-10, blank = not ranked" />
    <input data-m="quality" type="number" min="1" max="10" value="${rankValue(m.quality)}" placeholder="-" title="Quality 1-10, blank = not ranked" />
    <button class="btn btn-secondary btn-sm" data-del-model title="Remove model">&times;</button>
  </div>`;
}

// The models table in its three shapes
export function modelsBlock(p, { endpoint = true, pick = false, picked = "", addable = true } = {}) {
  const opts = { endpoint, pick, picked };
  const cols = pick
    ? `<div class="model-cols builder-pick"><span>Use</span><span>Model ID</span><span></span></div>`
    : endpoint
      ? `<div class="model-cols"><span>Model ID</span><span>Endpoint</span><span>Cost</span><span>Quality</span><span></span></div>`
      : `<div class="model-cols no-endpoint"><span>Model ID</span><span>Cost</span><span>Quality</span><span></span></div>`;
  return `<div class="io-sub">Models</div>
    ${cols}
    <div class="model-rows">${(p.models || []).map((m) => modelRow(m, opts)).join("")
      || '<p class="muted">No models yet.</p>'}</div>
    <div class="add-model-row">
      <button class="btn btn-secondary" data-add-model ${addable ? "" : "disabled"}
        ${addable ? "" : 'title="Save an API key first"'}>+ Add model</button>
    </div>`;
}

// A rank is 1 to 10 or blank; blank means the person has not ranked the model, and nothing fills it in
const clamp10 = (v) => (v === "" || v === null || v === undefined || Number.isNaN(Number(v)))
  ? null : Math.min(10, Math.max(1, Number(v)));
const rankValue = (v) => (v === null || v === undefined || v === "" ? "" : Number(v));

// Reads the model rows back, whatever columns were rendered
export function readModels(root) {
  return [...root.querySelectorAll(".model-row")].map((r) => {
    const val = (sel) => r.querySelector(sel)?.value;
    const m = { name: (val('[data-m="name"]') || "").trim(),
                endpoint: (val('[data-m="endpoint"]') || "").trim() };
    if (r.querySelector('[data-m="cost"]')) {
      m.cost = clamp10(val('[data-m="cost"]'));
      m.quality = clamp10(val('[data-m="quality"]'));
    }
    return m;
  });
}

// The builder-pick variant's ticked model name
export function readPickedModel(root) {
  const picked = root.querySelector('[data-m="pick"]:checked');
  return picked
    ? (picked.closest(".model-row")?.querySelector('[data-m="name"]')?.value || "").trim()
    : "";
}
