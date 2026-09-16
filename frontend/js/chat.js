// The chat pane: the transcript drawn one way, the composer, and the one path a reply takes
import { escapeHtml } from "./util.js";
import { renderMarkdown, linkifyEscaped } from "./md.js";
import { uploadSample, putWorkflowSecret, say as apiSay, getWorkflow, setTicketStatus } from "./api.js";
import { openModal, closeModal } from "./modal.js";

let chatWorkflowId = null;
// The chat follows the page's workflow and its view token - one ownership token for both modules
export function setChatWorkflow(id, token = 0) { chatWorkflowId = id; chatEpoch = token || chatEpoch + 1; }

// The one paperclip icon, drawn on currentColor so it themes itself
function clipIcon(size = 14) {
  return `<svg class="ico-clip" width="${size}" height="${size}"
    viewBox="0 0 950 950" fill="currentColor" aria-hidden="true"><path
    d="M857.7,141.3c-30.1-30.1-65.1-53.5-104.3-69.4c-37.8-15.3-77.7-23.2-118.7-23.2c-40.9,0-80.9,7.7-118.7,22.9c-39.1,15.8-74.2,38.9-104.3,68.8L73.1,478.3C49.3,501.9,30.9,529.4,18.3,560.2C6.2,589.9,0,621.3,0,653.6C0,685.7,6.1,717,18.1,746.7c12.4,30.7,30.7,58.2,54.3,81.899c23.6,23.7,51.2,42,81.9,54.5c29.7,12.101,61.1,18.2,93.3,18.2c32.2,0,63.6-6.1,93.3-18.1c30.8-12.5,58.399-30.8,82.1-54.4l269.101-268c17.3-17.2,30.6-37.3,39.699-59.7c8.801-21.6,13.2-44.5,13.2-67.899c0-48.2-18.8-93.2-52.899-127c-34-34.2-79.2-53.1-127.301-53.3c-48.199-0.1-93.5,18.6-127.6,52.7L269.6,473.3c-8.5,8.5-13.1,19.7-13.1,31.601c0,11.899,4.6,23.199,13.1,31.6l0.7,0.7c17.4,17.5,45.8,17.5,63.3,0.1l168-167.5c35.1-34.8,92.1-35,127.199-0.399c16.9,16.8,26.101,39.3,26.101,63.399c0,24.3-9.4,47.101-26.5,64.101l-269,268c-0.5,0.5-0.9,0.899-1.2,1.5c-29.7,28.899-68.9,44.699-110.5,44.5c-41.9-0.2-81.2-16.5-110.6-46c-14.7-15-26.1-32.5-34-52C95.5,694,91.7,674,91.7,653.6c0-41.8,16.1-80.899,45.4-110.3c0.4-0.3,0.7-0.6,1.1-0.899l337.9-337.8c0.3-0.3,0.6-0.7,0.899-1.1c21.4-21,46.3-37.4,74-48.5c27-10.8,55.4-16.2,84.601-16.2c29.199,0,57.699,5.6,84.6,16.4c27.9,11.3,52.9,27.8,74.3,49.1c21.4,21.4,37.9,46.4,49.2,74.3c10.9,26.9,16.4,55.4,16.4,84.6c0,29.3-5.5,57.9-16.5,85c-11.301,28-28,53.2-49.5,74.8l-233.5,232.8c-8.5,8.5-13.2,19.7-13.2,31.7s4.7,23.2,13.1,31.6l0.5,0.5c17.4,17.4,45.8,17.4,63.2,0L857.5,586.9C887.601,556.8,911,521.7,926.9,482.6C942.3,444.8,950,404.9,950,363.9c0-40.9-7.8-80.8-23.1-118.5C911.101,206.3,887.8,171.3,857.7,141.3z"/></svg>`;
}

// A shared file as a download card
function fileCardHtml(name, ref, size) {
  const kb = size ? ` (${size > 1048576 ? (size / 1048576).toFixed(1) + " MB"
                                        : Math.ceil(size / 1024) + " KB"})` : "";
  const href = ref ? `/api/blobs/${escapeHtml(String(ref).replace(/^blob:/, ""))}?wfl=${escapeHtml(String(chatWorkflowId || ""))}` : "";
  return `<div class="file-card">&#128196; <a href="${href}" download>${escapeHtml(name || "file")}</a>${kb}</div>`;
}

// A user upload paints as settled chips, not a spoken message
function uploadChipsHtml(names, cid = "", seq = "") {
  const chips = names.map((n) =>
    `<span class="upload-chip">${clipIcon(14)} <strong>&ldquo;${escapeHtml(String(n))}&rdquo;</strong> uploaded</span>`).join("");
  return `<div class="msg user upload-msg"${cid ? ` data-cid="${escapeHtml(cid)}"` : ""}${
    seq ? ` data-seq="${escapeHtml(String(seq))}"` : ""}>${chips}</div>`;
}

// An assistant message splits into boxes on blank lines, never inside a fence
function paragraphs(text) {
  const chunks = String(text).split(/(```[\s\S]*?(?:```|$))/g);
  const out = [""];
  for (const c of chunks) {
    if (c.startsWith("```")) { out[out.length - 1] += c; continue; }
    const bits = c.split(/\n[ \t]*\n/);
    bits.forEach((b, i) => {
      if (i === 0) out[out.length - 1] += b;
      else out.push(b);
    });
  }
  return out.map((s) => s.trim()).filter(Boolean);
}

const ATTACH_MARK = /\s*\[I attached: [^\]]*\]\s*$/;

// One transcript message as HTML - a shared file as its card, an upload as chips, words as bubbles
function messageHtml(item) {
  const seqAttr = item.seq ? ` data-seq="${escapeHtml(String(item.seq))}"` : "";
  const files = item.files || [];
  const text = String(item.text || "");
  if (files.length && typeof files[0] === "object")
    return `<div class="msg assistant"${seqAttr}><div class="bubble">${
      fileCardHtml(files[0].name, files[0].ref, files[0].size)}</div></div>`;
  if (item.from === "user") {
    const words = text.replace(ATTACH_MARK, "").trim();
    return (files.length ? uploadChipsHtml(files, "", item.seq) : "")
      + (words ? `<div class="msg user"${seqAttr}><div class="bubble"><span class="plain">${
          linkifyEscaped(escapeHtml(words))}</span></div></div>` : "");
  }
  if (!text.trim()) return "";
  return paragraphs(text).map((x, i) =>
    `<div class="msg assistant"${i === 0 ? seqAttr : ""}><div class="bubble">${renderMarkdown(x)}</div></div>`).join("");
}

const requestsById = new Map();

let chatTickets = [];

// Whether the built card's Continue still applies: the issue is ready and its run not continued
function continueOffered(ticketId) {
  const t = chatTickets.find((x) => x.id === ticketId);
  return !t || (t.status === "ready" && !t.continued);
}

// The whole transcript as HTML, in seq order - the reload path and the one truth
function timelineHtml(workflow) {
  chatTickets = workflow.tickets || [];
  const items = workflow.transcript || [];
  const byReq = new Map();
  items.forEach((i) => { if (i.kind === "answer" && i.to) byReq.set(i.to, i); });
  return items.map((i) => {
    if (i.kind === "message") return messageHtml(i);
    if (i.kind === "request") {
      requestsById.set(i.iid, i);
      return requestHtml(i, byReq.get(i.iid));
    }
    return "";
  }).join("");
}

// Whether a card is the opening card, the one that waits for the user
function pausingPlanCard(item) {
  return item?.request === "blueprint" && (item.payload || {}).head !== "built";
}

// Whether a request item waits for an answer in the composer (a question, or the opening card)
function answerable(item) {
  return item?.request === "ask" || pausingPlanCard(item);
}

// One request item as its card - open (no answer yet) or settled, from the frozen payload
function requestHtml(item, answer) {
  if (item.request === "ask") return askCardHtml(item, answer);
  if (item.request === "approval") return approvalCardHtml(item, answer);
  if (item.request === "blueprint") return blueprintCardHtml(item, answer);
  return "";
}

// The card wrapper every request kind shares
function cardHtml(kind, item, answer, inner) {
  const state = answer ? "settled" : "open";
  return `<div class="msg assistant chat-card" data-kind="${kind}" data-state="${state}"
      data-iid="${escapeHtml(String(item.iid || ""))}"${
      item.seq ? ` data-seq="${escapeHtml(String(item.seq))}"` : ""}><div class="bubble">${inner}</div></div>`;
}

// What the user did about a card, read from its answer item
function chosenOf(answer) {
  const shown = answer ? answer.shown : undefined;
  const viaMessage = !!(shown && typeof shown === "object" && shown.via_message);
  return { viaMessage,
           label: typeof shown === "string" ? shown : null,
           answers: shown && typeof shown === "object" && !viaMessage ? shown : null };
}

const OPT_BUTTON_MAX = 5;

// The option row - open buttons/dropdown, or the settled row with the pick marked
function optionButtons(opts, { chosen = null, viaMessage = false, open = false } = {}) {
  const norm = opts.map((o) => (typeof o === "object" ? o
    : { value: String(o), label: String(o), kind: "" }));
  if (open) {
    if (norm.length > OPT_BUTTON_MAX) {
      const choices = norm.map((o) =>
        `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`).join("");
      return `<div class="ask-select-row">
          <select class="rest-select" aria-label="Choose an option">
            <option value="" disabled selected>Choose an option&hellip;</option>${choices}
          </select>
          <button type="button" class="btn btn-primary btn-sm rest-select-go">Select</button>
        </div>`;
    }
    return norm.map((o) =>
      `<button type="button" class="btn btn-secondary btn-sm rest-opt"
         data-v="${escapeHtml(o.value)}">${escapeHtml(o.label)}</button>`).join("");
  }
  if (norm.length > OPT_BUTTON_MAX) {

    const pick = !viaMessage && norm.find((o) => o.value === chosen);
    return pick ? `<button type="button" disabled class="btn btn-sm choice-neutral">${escapeHtml(pick.label)}</button>`
                : `<button type="button" disabled class="btn btn-sm btn-secondary choice-off">Answered in the chat</button>`;
  }
  return norm.map((o) => {
    const sel = !viaMessage && chosen !== null && o.value === chosen;
    const cls = sel ? (o.kind === "no" ? "choice-no" : "choice-neutral") : "btn-secondary choice-off";
    return `<button type="button" disabled class="btn btn-sm ${cls}">${escapeHtml(o.label)}</button>`;
  }).join("");
}

// The setup card's form: each field gets the control its type deserves
function askFieldsForm(fields) {
  const rows = (fields || []).map((f) => {
    const name = escapeHtml(String(f.name || ""));
    const label = escapeHtml(String(f.label || f.name || ""));
    const opt = f.optional ? `<span class="label-optional">optional</span>` : "";
    const attrs = `data-field="${name}" data-label="${label}"`;
    const ctl = f.type === "choice"
      ? `<select ${attrs}>
           <option value="">Choose&hellip;</option>${(f.options || []).map((o) =>
        `<option value="${escapeHtml(String(o))}">${escapeHtml(String(o))}</option>`).join("")}</select>`
      : f.type === "number" ? `<input type="number" step="any" ${attrs}>`
      : f.type === "date" ? `<input type="date" ${attrs}>`
      : f.type === "boolean"
        ? `<select ${attrs}><option value="">Choose&hellip;</option>
             <option value="yes">yes</option><option value="no">no</option></select>`

      : `<textarea rows="1" ${attrs}></textarea>`;
    return `<div class="form-group"><label class="form-label">${label}${opt}</label>${ctl}</div>`;
  }).join("");
  return `<form class="rest-form ask-fields">${rows}
      <button type="submit" class="btn btn-primary btn-sm">${(fields || []).length === 1 ? "Send" : "Send answers"}</button>
    </form>
    <p class="hint ask-chat-hint">Missing one, or want to ask about it? Use the
    chat box below - anything you've already filled in comes along.</p>`;
}

// The settled setup card: one label-value row per field
function askFieldsSettled(fields, answers, viaMessage = false) {
  if (viaMessage) {
    return `<p class="hint">You answered in the chat instead - the details are in your message below.</p>`;
  }
  const rows = (fields || []).map((f) => {
    const v = (answers || {})[f.name];
    return `<div class="ask-field-done"><span class="ask-field-k">${
      escapeHtml(String(f.label || f.name))}:</span> ${
      v ? `<span class="ask-field-v">${escapeHtml(String(v))}</span>`
        : `<span class="muted">(not provided)</span>`}</div>`;
  }).join("");
  return `<div class="ask-fields-done">${rows}</div>`;
}

// A question card - secret, setup fields, or options - open or settled
function askCardHtml(item, answer) {
  const p = item.payload || {};
  const { viaMessage, label, answers } = chosenOf(answer);
  const question = renderMarkdown(p.question || "");
  if (p.secret) {
    const nameStrong = `<strong>${escapeHtml(p.secret_name || "a secret")}</strong>`;
    let body, hint;
    if (!answer) {
      body = `<form class="rest-form" data-secret-name="${escapeHtml(p.secret_name || "")}">
         <input type="password" placeholder="Value (kept masked)" autocomplete="new-password">
         <button type="submit" class="btn btn-primary btn-sm">Send</button></form>`;
      hint = `Stored securely on this computer as ${nameStrong} - never in this chat.`;
    } else if (label === "(provided)") {
      body = `<div class="ask-actions"><button type="button" disabled class="btn btn-sm choice-provided">(provided)</button></div>`;
      hint = `Stored securely on this computer as ${nameStrong} - never in this chat.`;
    } else {
      body = `<div class="ask-actions"><button type="button" disabled class="btn btn-secondary btn-sm choice-off">no value given</button></div>`;
      hint = `You replied in chat instead, so nothing was stored for ${nameStrong}.`;
    }
    return cardHtml("secret", item, answer, `
       <div class="chat-card-head"><span class="secret-lock" aria-hidden="true">&#128274;</span> Secret</div>
       ${question}
       ${body}
       <p class="hint">${hint}</p>`);
  }
  if (p.fields && p.fields.length) {
    return cardHtml("fields", item, answer, `${question}
       ${answer ? askFieldsSettled(p.fields, answers, viaMessage) : askFieldsForm(p.fields)}`);
  }
  const opts = (p.options || []).map(String);
  if (answer) {
    const row = optionButtons(opts, { chosen: label, viaMessage });
    return cardHtml("ask", item, answer, `${question}
       ${row ? `<div class="ask-actions">${row}</div>` : ""}`);
  }
  const upload = p.upload
    ? `<button type="button" class="btn btn-secondary btn-sm rest-upload">${clipIcon(14)} Upload files</button>
       <button type="button" class="btn btn-secondary btn-sm rest-paste-text">&#128203; Paste text</button>
       <input type="file" class="rest-file" multiple hidden>` : "";
  const choices = optionButtons(opts, { open: true });
  const actions = opts.length > OPT_BUTTON_MAX
    ? `${choices}${upload ? `<div class="ask-actions">${upload}</div>` : ""}`
    : `<div class="ask-actions">${choices}${upload}</div>`;
  return cardHtml("ask", item, answer, `${question}
       ${actions}
       ${p.folder ? `<div class="fs-host" data-rest-fs></div>` : ""}
       <p class="hint ask-chat-hint">Something else, or want to say more? Answer in the chat box below.</p>`);
}

// An approval card - the action as its heading, Yes/No, open or settled
function approvalCardHtml(item, answer) {
  const p = item.payload || {};
  const title = p.title || "Do something outside this app";
  const { viaMessage, label } = chosenOf(answer);

  const always = p.holds === "send" ? "Yes, and don't ask again for this step"
    : p.holds === "window" && p.domain ? `Yes, and don't ask again for ${p.domain}` : "";
  const opts = always
    ? [{ value: "allow", label: "Yes", kind: "yes" },
       { value: "always", label: always, kind: "yes" },
       { value: "deny", label: "No", kind: "no" }]
    : [{ value: "allow", label: "Yes", kind: "yes" },
       { value: "deny", label: "No", kind: "no" }];
  const row = answer
    ? optionButtons(opts, { chosen: label, viaMessage })
    : optionButtons(opts, { open: true });
  return cardHtml("approval", item, answer, `
     <div class="chat-card-head">${escapeHtml(title)}</div>
     ${p.detail ? `<p class="appr-detail">${escapeHtml(p.detail)}</p>` : ""}
     ${p.scope ? `<p class="hint">${escapeHtml(p.scope)}</p>` : ""}
     <div class="ask-actions">${row}</div>`);
}

// The bullet colour class; an unmapped type would render an invisible dot
const dotType = (t) => (t === "user-input" ? "input" : (t || "code"));

// The opening card or the built card, drawn from its frozen payload
function blueprintCardHtml(item, answer) {
  const p = item.payload || {};
  const ch = p.changes || {};
  const changed = new Set([...(ch.modified || []), ...(ch.added || [])]);
  const all = p.steps || [];
  const shown = changed.size ? all.filter((n) => changed.has(n.name)) : all;
  const removed = (ch.removed || []);
  const steps = shown.map((n) => `<li class="plan-node">
      <i class="dot plan-node-type ${escapeHtml(dotType(n.type))}"></i>
      <span class="plan-node-body"><strong>${escapeHtml(n.name || "")}</strong>
      ${n.type ? `<span class="muted">(${escapeHtml(n.type)})</span>` : ""}
      ${n.description ? ` - ${escapeHtml(n.description)}` : ""}${
        n.tested ? `<span class="muted"> &middot; ${escapeHtml(n.tested)}</span>` : ""
      }</span></li>`).join("");
  const gone = removed.map((n) => `<li class="plan-node">
      <span class="plan-node-body muted">Removed: ${escapeHtml(String(n))}</span></li>`).join("");
  const warnings = (p.warnings || []).map((w) =>
    `<p class="plan-warn">&#9888; ${escapeHtml(String(w))}</p>`).join("");
  const packages = p.packages || [];
  const domains = p.domains || [];
  const paths = p.paths || [];
  const isChange = !!((ch.modified || []).length || (ch.added || []).length
                      || removed.length);
  const built = p.head === "built";
  const head = built
    ? (isChange ? "Here's what changed" : "Here's what I built")
    : isChange ? "Here's what will change" : "Here's the plan";

  const mustHave = (p.must_have || []).map((m) => `<div class="plan-installs">
       For <strong>${escapeHtml(m.label)}</strong>: a row missing
       <code>${(m.must_have || []).map(escapeHtml).join("</code>, <code>")}</code>
       is flagged; <code>${(m.may_be_missing || []).map(escapeHtml).join("</code>, <code>")}</code>
       may be missing and the row still goes through.</div>`).join("");

  const rest = isChange ? all.length - shown.length : 0;

  const fn = p.fix_note || {};
  const cn = p.change_note || {};
  const [firstLabel, firstText, willChange] = (fn.went_wrong || fn.will_change)
    ? ["What went wrong:", fn.went_wrong, fn.will_change]
    : ["What I found:", cn.found, cn.will_change];
  const fixNote = (firstText || willChange)
    ? `<div class="plan-fixnote">
        ${firstText ? `<p><strong>${firstLabel}</strong> ${escapeHtml(firstText)}</p>` : ""}
        ${willChange ? `<p><strong>What will change:</strong> ${escapeHtml(willChange)}</p>` : ""}
       </div>` : "";

  const done = built && Array.isArray(p.done) && p.done.length
    ? `<div class="plan-done">${p.done.map((l) => `<p>${escapeHtml(String(l))}</p>`).join("")}</div>` : "";
  const cont = built && p.continue && p.continue.run_id && continueOffered(p.continue.ticket_id)
    ? `<div class="ask-actions"><button type="button" class="btn btn-secondary btn-sm built-continue"
         data-ticket="${escapeHtml(String(p.continue.ticket_id || ""))}"
         data-run="${escapeHtml(String(p.continue.run_id))}">Continue the failed run</button></div>` : "";

  const diffList = (label, rows) => (rows || []).length ? `<div class="plan-diff">
       <p class="plan-diff-label">${label}</p>
       <ul class="plan-nodes">${rows.map((n) => `<li class="plan-node">
         <i class="dot plan-node-type ${escapeHtml(dotType(n.type))}"></i>
         <span class="plan-node-body"><strong>${escapeHtml(n.name || "")}</strong>
         ${n.type ? `<span class="muted">(${escapeHtml(n.type)})</span>` : ""}</span></li>`).join("")}</ul>
     </div>` : "";
  const planDiff = !built && p.plan_changed;
  const body = planDiff ? `
     <div class="chat-card-head">Here's what changed in the plan</div>
     ${diffList("Steps I removed from the plan", p.plan_removed)}
     ${diffList("Steps I will add to the plan", p.plan_added)}` : `
     <div class="chat-card-head">${head}</div>
     ${fixNote}
     ${isChange ? "" : `<div class="plan-summary">${renderMarkdown(p.summary || "")}</div>`}
     ${warnings}
     ${mustHave}
     ${steps || gone ? `<ul class="plan-nodes">${steps}${gone}</ul>` : ""}
     ${rest > 0 ? `<p class="hint">${rest} other step${rest === 1 ? "" : "s"} stay${rest === 1 ? "s" : ""} as ${rest === 1 ? "it is" : "they are"}.</p>` : ""}
     ${packages.length ? `<div class="plan-installs">Installs into this workflow's
       own environment: <code>${packages.map(escapeHtml).join("</code>, <code>")}</code></div>` : ""}
     ${domains.length ? `<div class="plan-installs">Connects to:
       <code>${domains.map(escapeHtml).join("</code>, <code>")}</code>
       - allowed for this workflow</div>` : ""}
     ${paths.length ? `<div class="plan-installs">Reads files in:
       <code>${paths.map(escapeHtml).join("</code>, <code>")}</code>
       - allowed for this workflow</div>` : ""}`;
  if (built) return cardHtml("built", item, answer || { shown: "" }, `${body}${done}${cont}`);
  const opts = (p.options || []).map(String);
  const { viaMessage, label } = chosenOf(answer);
  const row = answer ? optionButtons(opts, { chosen: label, viaMessage })
                     : optionButtons(opts, { open: true });
  return cardHtml("plan", item, answer, `${body}
     <div class="ask-actions">${row}</div>
     ${answer ? "" : `<p class="hint">Or tell me what to change - just say it below.</p>`}`);
}

let busy = false;
let openAsk = false;
let stopping = false;

let chatEpoch = 0;
let onTurnStartedImpl = null;
let stopTurnImpl = null;
let staged = [];

// Paints the composer's busy, waiting and stopping states
function reflectComposer() {
  const input = document.getElementById("chatInput");
  const send = document.querySelector('#chatForm button[type="submit"]');
  if (!input) return;
  if (!busy) stopping = false;
  input.placeholder =
    busy && openAsk ? "Answer here - or ask me something back..."
    : busy && stopping ? "Stopping - you can send your next message now"
    : busy ? "Working - a message stops this and takes over, ■ just stops"
    : "Describe a change or a new step...";

  if (send) send.disabled = false;
  const stop = document.getElementById("chatStop");
  if (stop) stop.disabled = !busy || openAsk || stopping;
}

// The composer follows a running turn (Send delivers into it, Stop is live)
export function enterBusy() { busy = true; reflectComposer(); }
// The turn is over: the composer is plain again and no card waits
export function exitBusy() { busy = false; openAsk = false; reflectComposer(); }
// While a Stop winds down the dock says so instead of painting working
export function isStopping() { return stopping; }
// A new turn began (the follow-up after a takeover): the stopping state belongs to the turn that ended
export function clearStopping() { stopping = false; }

// Marks the turn paused on a question, so the composer delivers into the wait
export function setOpenAsk(v) { openAsk = !!v; reflectComposer(); }

// Clears every trace of the previous workflow's chat; its turn keeps running server-side
export function resetChatView() {
  busy = false;
  stopping = false;
  openAsk = false;
  staged = [];
  requestsById.clear();
  const stage = document.getElementById("chatStage");
  if (stage) { stage.innerHTML = ""; stage.hidden = true; }
  onTurnStartedImpl = null;
  stopTurnImpl = null;
  const list = document.getElementById("chatMessages");
  if (list) list.innerHTML = "";
  reflectComposer();
}

// A client id for one send: the echo carries it, the server stamps it on the item
function newCid() {
  return "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}

// The optimistic echo of a typed send, under its client id
function appendEcho(text, files, cid) {
  const list = document.getElementById("chatMessages");
  if (!list) return;
  const words = String(text || "").replace(ATTACH_MARK, "").trim();
  list.insertAdjacentHTML("beforeend",
    (files?.length ? uploadChipsHtml(files, cid) : "")
    + (words ? `<div class="msg user" data-cid="${escapeHtml(cid)}"><div class="bubble"><span class="plain">${
        linkifyEscaped(escapeHtml(words))}</span></div></div>` : ""));
  list.scrollTop = list.scrollHeight;
}

// Sends typed words or a card click; the server decides how it lands
export async function say(text, kind = "typed", { iid = "", files = null, answers = null, issue = "" } = {}) {
  if (!chatWorkflowId) return;
  const epoch = chatEpoch;
  const cid = newCid();
  if (kind === "typed") appendEcho(text, files, cid);
  const notSent = (why) => {

    if (kind === "typed") {
      const echo = document.querySelector(`#chatMessages [data-cid="${CSS.escape(cid)}"]`);
      if (echo) echo.querySelector(".bubble")?.insertAdjacentHTML("beforeend",
        `<div class="chat-err">Not sent - ${escapeHtml(why)}</div>`);
      const input = document.getElementById("chatInput");
      if (input && !input.value) input.value = text.replace(ATTACH_MARK, "");
    } else {
      unlatchCard(iid, `Not sent - ${why}`);
    }
  };
  let r;
  try {
    r = await apiSay(chatWorkflowId, { cid, kind, text,
                                      ...(iid ? { iid } : {}),
                                      ...(files?.length ? { files } : {}),
                                      ...(answers ? { answers } : {}),
                                      ...(issue ? { issue } : {}) });
  } catch (err) {
    if (epoch !== chatEpoch) return;
    notSent(err.message || String(err));
    return;
  }
  if (epoch !== chatEpoch) return;
  if (r.landed === "busy") {

    notSent(r.kind === "run"
      ? "a run is going on this workflow. Stop it, or wait for it to finish, then send again."
      : "the workflow is busy for a moment - send again in a few seconds.");
    return;
  }
  if (r.landed === "noop") {

    await rebuildTimeline();
    return;
  }
  (r.items || []).forEach((it) => appendShownItem(it));
  if (r.landed === "started") {
    enterBusy();
    startBuildBubble();
    if (onTurnStartedImpl) onTurnStartedImpl();
  } else if (r.landed === "resolved") {

    setOpenAsk(false);
  }
}

// Answers a card: latches it synchronously, then one send
function answerCard(card, { text = "", files = null, answers = null } = {}) {
  if (!card || card.dataset.settling) return;
  card.dataset.settling = "1";
  card.querySelectorAll("button, input, select, textarea").forEach((b) => { b.disabled = true; });
  const iid = card.dataset.iid;

  const req = requestsById.get(iid);
  if (req && text && !(files && files.length)) {
    card.outerHTML = requestHtml(req, { shown: answers || String(text), provisional: true });
  }
  say(text, "click", { iid, files, answers });
}

// Reopens a card whose send never reached the server, saying why under its buttons
function unlatchCard(iid, why = "") {
  const card = iid && document.querySelector(`#chatMessages .chat-card[data-iid="${CSS.escape(iid)}"]`);
  if (!card) return;
  const req = requestsById.get(iid);
  if (req) {
    card.outerHTML = requestHtml(req, undefined);
    const fresh = document.querySelector(`#chatMessages .chat-card[data-iid="${CSS.escape(iid)}"]`);
    if (fresh && why) fresh.querySelector(".bubble")?.insertAdjacentHTML("beforeend",
      `<div class="chat-err">${escapeHtml(why)}</div>`);
    return;
  }
  delete card.dataset.settling;
  card.querySelectorAll("button, input, select, textarea").forEach((b) => { b.disabled = false; });
}

// Repaints the whole conversation from the record - the one recovery when the page and the record disagree
async function rebuildTimeline() {
  if (!chatWorkflowId) return;
  const epoch = chatEpoch;
  let p;
  try { p = await getWorkflow(chatWorkflowId); } catch { return; }
  if (epoch !== chatEpoch) return;
  const list = document.getElementById("chatMessages");
  if (!list) return;
  const live = document.getElementById("thinkingMsg");
  list.innerHTML = timelineHtml(p);
  wireFolderPickers(list);
  if (live) list.appendChild(live);
  scrollChat(true);
}

// The built card's Continue: closes the issue as continued and hands the run to the workflow page to resume
async function continueFailedRun(btn) {
  const ticketId = btn.dataset.ticket;
  const runId = btn.dataset.run;
  btn.disabled = true;

  const wf = await getWorkflow(chatWorkflowId).catch(() => null);
  const t = (wf?.tickets || []).find((x) => x.id === ticketId);
  if (!t || t.status !== "ready" || t.continued) {
    btn.closest(".ask-actions")?.remove();
    return;
  }
  await setTicketStatus(chatWorkflowId, ticketId, { status: "closed", continued: true }).catch(() => {});
  chatTickets = (wf.tickets || []).map((x) => x.id === ticketId ? { ...x, status: "closed", continued: true } : x);
  btn.closest(".ask-actions")?.remove();
  window.dispatchEvent(new CustomEvent("cryogram:continue-run", { detail: { ticketId, runId } }));
}

document.addEventListener("click", (e) => {
  const contBtn = e.target.closest?.(".built-continue");
  if (contBtn) { continueFailedRun(contBtn); return; }
  const opt = e.target.closest?.(".rest-opt");
  if (opt) { answerCard(opt.closest(".chat-card"), { text: opt.dataset.v }); return; }
  const go = e.target.closest?.(".rest-select-go");
  if (go) {
    const v = go.parentElement.querySelector(".rest-select")?.value || "";
    if (v) answerCard(go.closest(".chat-card"), { text: v });
    return;
  }
  const pt = e.target.closest?.(".rest-paste-text");
  if (pt) {

    const card = pt.closest(".chat-card");
    openModal({
      title: "Paste text",
      body: `<div class="form-group">
               <label class="form-label">Text
                 <span class="label-optional">sent as a text file</span></label>
               <textarea class="paste-text-body" rows="12"></textarea>
               <p class="form-hint">The whole text is attached as a file, so
                 nothing is shortened.</p>
             </div>`,
      actions: [
        { label: "Attach", kind: "btn-primary", onClick: async (root) => {
            const text = root.querySelector(".paste-text-body")?.value || "";
            if (!text.trim() || !chatWorkflowId) return;
            const stamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-");
            const name = `pasted-text-${stamp}.txt`;
            try {
              await uploadSample(chatWorkflowId, new File([text], name, { type: "text/plain" }));
            } catch { return; }
            closeModal();
            answerCard(card, { text: `I uploaded a file: ${name}`, files: [name] });
          } },
        { label: "Cancel" },
      ],
    });
    return;
  }
  const up = e.target.closest?.(".rest-upload");
  if (up) {
    const file = up.parentElement.querySelector(".rest-file");
    if (!file) return;
    file.onchange = async () => {
      const files = [...(file.files || [])];
      if (!files.length || !chatWorkflowId) return;
      up.disabled = true;
      const uploaded = [];
      try {
        for (const f of files) {
          up.textContent = files.length > 1
            ? `Uploading ${uploaded.length + 1} of ${files.length}…` : "Uploading…";
          await uploadSample(chatWorkflowId, f);
          uploaded.push(f.name);
        }
        answerCard(up.closest(".chat-card"), {
          text: `I uploaded ${uploaded.length > 1 ? `${uploaded.length} files` : "a file"}: ${uploaded.join(", ")}`,
          files: uploaded });
      } catch { up.disabled = false; up.innerHTML = `${clipIcon(14)} Upload files`; }
    };
    file.click();
  }
});

document.addEventListener("submit", async (e) => {
  const form = e.target.closest?.(".rest-form");
  if (!form) return;
  e.preventDefault();
  const card = form.closest(".chat-card");
  const secretName = form.dataset.secretName;
  if (secretName) {
    const v = form.querySelector("input")?.value || "";
    if (!v.trim() || form.dataset.busy) return;

    form.dataset.busy = "1";
    try {
      await putWorkflowSecret(chatWorkflowId, secretName, v);
    } catch (err) {

      form.dataset.busy = "";
      let line = form.querySelector(".form-error");
      if (!line) { line = document.createElement("p"); line.className = "form-error"; form.appendChild(line); }
      line.textContent = `Couldn't store the value (${err?.message || "the app didn't answer"}) - try again, or tell me in the chat.`;
      return;
    }
    answerCard(card, { text: "(provided)" });
    return;
  }
  if (form.classList.contains("ask-fields")) {

    const filled = {};
    form.querySelectorAll("[data-field]").forEach((c) => {
      const v = (c.value || "").trim();
      if (v) filled[c.dataset.field] = v;
    });
    if (!Object.keys(filled).length) return;
    answerCard(card, { answers: filled });
  }
});

document.addEventListener("input", (e) => {
  const t = e.target;
  if (t?.tagName === "TEXTAREA" && t.closest(".ask-fields")) {
    t.style.height = "auto";
    t.style.height = `${t.scrollHeight + 2}px`;
  }
});

// Wires the folder picker on any open card that asks for a path
function wireFolderPickers(scope) {
  scope.querySelectorAll(".chat-card[data-state=\"open\"] [data-rest-fs]").forEach((h) => {
    if (h.dataset.wired) return;
    h.dataset.wired = "1";
    import("./fsPicker.js").then(({ renderFsPicker }) =>
      renderFsPicker(h, (p) => answerCard(h.closest(".chat-card"), { text: p })));
  });
}

// Paints the transcript into an empty chat DOM - the reattach path needs it
export function ensureTimeline(workflow) {
  const list = document.getElementById("chatMessages");
  if (!list || list.childElementCount) return;
  list.innerHTML = timelineHtml(workflow);
  wireFolderPickers(list);
  list.scrollTop = list.scrollHeight;
}

// The chat pane render; a busy DOM holding live content is never rebuilt
export function renderChat(workflow, onTurnStarted, settings, guard, stopTurn, isBusy = false) {
  renderBuilderNote(workflow, settings);
  const list = document.getElementById("chatMessages");

  if (!isBusy) {
    list.innerHTML = timelineHtml(workflow);
    wireFolderPickers(list);
  }
  list.scrollTop = list.scrollHeight;

  const form = document.getElementById("chatForm");
  const input = document.getElementById("chatInput");

  const autosize = () => {
    input.style.setProperty("--h", "0px");
    input.style.height = "auto";
    const content = input.scrollHeight;
    input.style.height = "";

    input.style.setProperty("--h", (content + 2) + "px");
  };
  input.oninput = autosize;
  onTurnStartedImpl = onTurnStarted;
  stopTurnImpl = stopTurn || null;
  reflectComposer();

  const stopBtn = document.getElementById("chatStop");
  if (stopBtn) stopBtn.onclick = () => {
    if (busy && !stopping && stopTurnImpl) {
      stopping = true;
      reflectComposer();
      stopTurnImpl();
    }
  };

  input.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  };

  form.onsubmit = async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text && !staged.length) return;
    if (!busy && guard && !guard()) return;
    const names = await deliverStaged();
    const msg = [text, names.length ? `[I attached: ${names.join(", ")}]` : ""]
      .filter(Boolean).join(" ");
    if (!msg) return;
    input.value = "";
    autosize();

    if (busy && !openAsk && !stopping && stopTurnImpl) {
      stopping = true;
      reflectComposer();
      stopTurnImpl();
    }
    say(msg, "typed", { files: names });
  };

  input.onpaste = (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) {
      e.preventDefault();
      files.forEach((f) => stageAttachment(f));
    }
  };
}

// Stages a file as a chip by the composer
export function stageAttachment(file) {
  if (!file) return;
  staged.push(file);
  renderStage();
}

// The staged-file chips; nothing staged means an empty, hidden bar
function renderStage() {
  const host = document.getElementById("chatStage");
  if (!host) return;
  host.hidden = staged.length === 0;
  host.innerHTML = !staged.length ? "" : staged.map((f, i) => {
    const img = f.type?.startsWith("image/")
      ? `<img class="stage-thumb" src="${URL.createObjectURL(f)}" alt="">` : clipIcon(14);
    return `<span class="stage-chip">${img} ${escapeHtml(f.name || "pasted image")}
      <button type="button" class="stage-x" data-i="${i}" title="Remove">&times;</button></span>`;
  }).join("") + `<span class="hint">Press Send to share</span>`;
  host.querySelectorAll(".stage-x").forEach((b) => (b.onclick = () => {
    staged.splice(+b.dataset.i, 1);
    renderStage();
  }));
}

// Uploads the staged files as samples and returns their names
async function deliverStaged() {
  if (!staged.length || !chatWorkflowId) return [];
  const names = [];
  for (const f of staged.splice(0)) {
    const name = f.name || `pasted-${Date.now()}.png`;
    try {
      await uploadSample(chatWorkflowId, f.name ? f : new File([f], name, { type: f.type }));
      names.push(name);
    } catch { }
  }
  renderStage();
  return names;
}

// Renders one live transcript item with the same function a reload uses
export function appendShownItem(item) {
  const list = document.getElementById("chatMessages");
  if (!list || !item) return;
  if (item.seq && list.querySelector(`[data-seq="${CSS.escape(String(item.seq))}"]`)) return;
  if (item.kind === "message") {
    const html = messageHtml(item);
    const echo = item.cid && list.querySelector(`[data-cid="${CSS.escape(String(item.cid))}"]`);
    if (echo) {

      list.querySelectorAll(`[data-cid="${CSS.escape(String(item.cid))}"]`).forEach((el, i) => {
        if (i === 0) el.outerHTML = html; else el.remove();
      });
    } else if (item.from === "assistant") {
      dropThinkingBubble();
      list.insertAdjacentHTML("beforeend", html);
      startBuildBubble();
    } else {
      list.insertAdjacentHTML("beforeend", html);
    }
    scrollChat();
    return;
  }
  if (item.kind === "request") {
    requestsById.set(item.iid, item);
    dropThinkingBubble();
    list.insertAdjacentHTML("beforeend", requestHtml(item, undefined));
    wireFolderPickers(list);
    if (answerable(item)) setOpenAsk(true);
    scrollChat(true);
    return;
  }
  if (item.kind === "answer") {
    const req = requestsById.get(item.to);
    const card = list.querySelector(`.chat-card[data-iid="${CSS.escape(String(item.to))}"]`);
    if (req && card) card.outerHTML = requestHtml(req, item);
    else rebuildTimeline();
    if (!req || answerable(req)) setOpenAsk(false);
    startBuildBubble();
    scrollChat();
  }
}

let streamPending = null;
let streamLastTs = 0;

// The live bubble's throttled repaint - a full re-parse per delta killed selection and scaled badly
export function renderStream(accText) {
  streamPending = accText;
  const now = Date.now();
  if (now - streamLastTs < 80) {
    setTimeout(() => {
      if (streamPending !== null) {
        const t = streamPending;
        streamPending = null;
        streamLastTs = Date.now();
        paintStream(t);
      }
    }, 90);
    return;
  }
  streamPending = null;
  streamLastTs = now;
  paintStream(accText);
}

// The actual live-bubble paint; the host appears with its first character, never before
function paintStream(accText) {
  const host = document.getElementById("thinkingMsg");
  if (!host) return;

  const parts = paragraphs(accText || "");
  host.innerHTML = parts.map((x) => `<div class="bubble">${renderMarkdown(x)}</div>`).join("");
  host.classList.toggle("hidden", !parts.length);
  scrollChat();
}

// Keeps what was streamed and appends the error under it - never replaces the bubble
export function failStream(accText, message) {
  streamPending = null;
  const t = document.getElementById("thinkingMsg");
  if (!t) return;
  t.classList.remove("hidden");
  t.innerHTML = paragraphs(accText || "").map((x) => `<div class="bubble">${renderMarkdown(x)}</div>`).join("")
    + `<div class="bubble"><div class="chat-err">${escapeHtml(message)}</div></div>`;
  t.removeAttribute("id");
  scrollChat();
}

// Drops the live bubble - its text has become a card's lead or a message item
export function dropThinkingBubble() {
  streamPending = null;
  document.getElementById("thinkingMsg")?.remove();
}

// Opens the hidden host the reply streams into
export function startBuildBubble() {
  const list = document.getElementById("chatMessages");
  if (!list || document.getElementById("thinkingMsg")) return;
  list.insertAdjacentHTML("beforeend",
    `<div class="msg assistant hidden" id="thinkingMsg"></div>`);
  scrollChat(true);
}

// Sticky scrolling: follows the newest content only when the user is already near the bottom
export function scrollChat(force = false) {
  const list = document.getElementById("chatMessages");
  if (!list) return;
  const nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 120;
  if (force || nearBottom) list.scrollTop = list.scrollHeight;
}

// Points at Admin while no builder AI is connected; once one is, the chat header says nothing about it
function renderBuilderNote(workflow, settings) {
  const host = document.getElementById("chatModel");
  if (!host || !settings) return;

  const b = (settings.providers || []).find((p) => p.use === "builder");
  const connected = !b ? false
    : b.auth === "claude-subscription" ? !!settings.claude_connected
    : b.auth === "codex-subscription" ? !!settings.codex_connected
    : !!(settings.secrets || {})[b.key_name];
  host.innerHTML = connected
    ? ""
    : `<a href="#/admin" class="chat-model-empty" title="No Builder Agent connected yet">set the Builder Agent in Admin</a>`;
}
