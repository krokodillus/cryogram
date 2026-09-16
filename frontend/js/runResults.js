// The run modal: what a finished run produced, or where a run stopped and why, with the ways forward
import { escapeHtml as esc, humanTokens, fmtSize } from "./util.js";
import { openModal, closeModal } from "./modal.js";
import { displayText } from "./valueField.js";
import { verdictLines, offendingHtml, sawHtml, haltReasonText, haltDecision } from "./run.js";

const LIST_ITEMS = 12;
const TEXT_CLAMP = 400;
const RECORD_KEYS = 8;

// When it ran, in the reader's own locale - reads as a sentence, not a log line
function fmtWhen(ts) {
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString([],
    { dateStyle: "medium", timeStyle: "short" });
}

function fmtSecs(s) {
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${Math.round(s - m * 60)}s`;
}

// Every scalar leaf goes through displayText, so a boolean can never read true here and yes elsewhere
const leaf = (v) => displayText(v);

// A record's fields joined for one bullet, capped with a count
function recordLine(o) {
  const keys = Object.keys(o);
  const shown = keys.slice(0, RECORD_KEYS)
    .map((k) => `${esc(k)}: ${esc(leaf(o[k]))}`).join(" &middot; ");
  return keys.length > RECORD_KEYS
    ? `${shown} &middot; <span class="muted">and ${keys.length - RECORD_KEYS} more fields</span>`
    : shown;
}

// Long text clamps behind Show more; the full text stays in the DOM
function longText(s) {
  if (s.length <= TEXT_CLAMP) return `<span class="rv-text">${esc(s)}</span>`;
  return `<span class="rv-text rv-clamped" data-clamped="1">
      <span class="rv-short">${esc(s.slice(0, TEXT_CLAMP))}&hellip;</span>
      <span class="rv-full">${esc(s)}</span>
      <button type="button" class="rv-more">Show more</button></span>`;
}

// A result renders as itself: files by name, lists as bullets, long text behind Show more
function valueHtml(d) {
  if (d.skipped)
    return `<span class="muted">not produced on this run</span>`;

  if (d.ref)
    return `<a class="rv-file" href="/api/blobs/${esc(String(d.ref).replace(/^blob:/, ""))}"
        download>&#128196; ${esc(d.name || "file")}</a>${d.size ? ` <span class="muted">(${fmtSize(d.size)})</span>` : ""}`;
  const v = d.value;
  if (Array.isArray(v)) {
    if (!v.length) return `<span class="muted">empty list</span>`;
    const items = v.slice(0, LIST_ITEMS).map((x) =>
      `<li>${x && typeof x === "object" && !Array.isArray(x)
        ? recordLine(x) : esc(leaf(x))}</li>`).join("");
    const more = v.length > LIST_ITEMS
      ? `<li class="muted">&hellip;and ${v.length - LIST_ITEMS} more</li>` : "";
    return `<ul class="rv-list">${items}${more}</ul>`;
  }
  if (v && typeof v === "object") {
    const keys = Object.keys(v);
    if (!keys.length) return `<span class="muted">empty</span>`;

    const rows = keys.slice(0, RECORD_KEYS).map((k) =>
      `<div class="rv-row"><span class="rv-key">${esc(k)}:</span> ${esc(leaf(v[k]))}</div>`).join("");
    const more = keys.length > RECORD_KEYS
      ? `<div class="rv-row muted">&hellip;and ${keys.length - RECORD_KEYS} more fields</div>` : "";
    return `<div class="rv-record">${rows}${more}</div>`;
  }
  if (typeof v === "string") return longText(v);
  return `<span class="rv-text">${esc(displayText(v))}</span>`;
}

const SUMMARY_ITEMS = 3;
const SUMMARY_CHARS = 40;

// One line naming what a run kept
export function resultSummary(r) {
  const kept = (r.results || []).filter((d) => !d.skipped);
  if (!kept.length) return "No results kept";
  const part = (d) => {
    if (d.ref) return `${d.label}: ${d.name || "file"}`;
    const v = d.value;
    if (Array.isArray(v))
      return `${d.label}: ${v.length} item${v.length === 1 ? "" : "s"}`;
    if (v && typeof v === "object")
      return `${d.label}: ${Object.keys(v).length} fields`;
    const t = displayText(v).replace(/\s+/g, " ").trim();
    return `${d.label}: ${t.length > SUMMARY_CHARS
      ? t.slice(0, SUMMARY_CHARS) + "…" : t}`;
  };
  const shown = kept.slice(0, SUMMARY_ITEMS).map(part).join(" · ");
  return kept.length > SUMMARY_ITEMS
    ? `${shown} · and ${kept.length - SUMMARY_ITEMS} more`
    : shown;
}

const STEP_SUMMARY_PORTS = 3;

// A capped preview list's true length - the server's trailing "...and N more items" marker counts too
function listCount(v) {
  const last = v[v.length - 1];
  const m = typeof last === "string" && last.match(/^\.\.\.and (\d+) more items/);
  return m ? v.length - 1 + parseInt(m[1], 10) : v.length;
}

// One line saying what a step produced, value-first like the history rows
function portSummary(vals) {
  const keys = Object.keys(vals || {});
  const one = (k) => {
    const v = vals[k];
    if (Array.isArray(v)) {
      const n = listCount(v);
      return `${k}: ${n ? `${n} item${n === 1 ? "" : "s"}` : "empty list"}`;
    }
    if (v && typeof v === "object") return `${k}: a record`;
    const t = displayText(v).replace(/\s+/g, " ").trim();
    return `${k}: ${t.length > SUMMARY_CHARS
      ? t.slice(0, SUMMARY_CHARS) + "…" : t}`;
  };
  const shown = keys.slice(0, STEP_SUMMARY_PORTS).map(one).join(" · ");
  return keys.length > STEP_SUMMARY_PORTS ? `${shown} · …` : shown;
}

// A step's expanded body: what it was given, what it produced, what it said while working
function stepBody(e) {
  const block = (title, vals) => {
    const ks = Object.keys(vals || {});
    if (!ks.length) return "";
    return `<div class="rv-step-part"><div class="io-sub">${esc(title)}</div>`
      + ks.map((k) => field(k, valueHtml({ value: vals[k] }))).join("")
      + `</div>`;
  };
  const notes = (e.notes || []).length
    ? `<div class="rv-step-part"><div class="io-sub">While working</div>`
      + e.notes.map((n) => `<p class="hint">${esc(String(n))}</p>`).join("")
      + `</div>`
    : "";
  return block("Given", e.inputs) + block("Produced", e.output) + notes
    + (e.entered_by_user
       ? `<p class="hint">These values were entered by you.</p>` : "")
    + (e.note ? `<p class="hint">${esc(e.note)}</p>` : "");
}

// Every step of the run, in order: completed ones expandable, skipped ones said plainly
function stepsSection(r, nodeName) {
  const entries = r.steps || [];
  if (!entries.length) return "";
  let i = 0;
  return entries.map((e) => {
    if (e.note && !e.node)
      return `<p class="hint">${esc(e.note)}</p>`;
    const name = e.name || nodeName(e.node);
    if (e.status === "skipped") {
      const why = e.reason === "branch-not-taken"
        ? "the workflow took a different path"
        : e.reason === "upstream-skipped"
          ? "a step before it did not run" : "";
      return `<div class="rv-step rv-step-skipped muted">${esc(name)}
        - did not run${why ? ` - ${esc(why)}` : ""}</div>`;
    }
    i += 1;
    const produced = e.output ? portSummary(e.output) : "";
    return `<details class="rv-step"><summary>${i}. ${esc(name)}${
      produced ? ` <span class="muted">- ${esc(produced)}</span>` : ""
      }</summary>${stepBody(e)}</details>`;
  }).join("");
}

// A field reads Label: value; block values wrap below the label
const field = (label, value) =>
  `<div class="card-field"><span class="card-field-label">${esc(label)}:</span>
    <span class="card-field-value">${value}</span></div>`;

const section = (title, inner) =>
  inner ? `<div class="d-section"><h3>${esc(title)}</h3>${inner}</div>` : "";

// The plain span from start to finish; a wait for a person is included and said
function timingField(r) {
  if (!r.started || !r.ts) return "";
  return field("Duration", esc(fmtSecs(Math.max(0, r.ts - r.started)))
    + (r.paused ? ` <span class="muted">(incl. waiting for you)</span>` : ""));
}

// The run-results window: what a run produced, its timing and what it was given
export function runModal(r, workflow, { onResume, onFix, onRestart, onCheckSend,
                                        onProceed, onResendAll, afterExit } = {}) {
  if (!r) return;
  const nodeName = (id) =>
    (workflow.nodes || []).find((n) => n.id === id)?.name || id || "?";
  const ok = r.status === "completed";

  const results = (r.results || [])
    .map((d) => field(d.label, valueHtml(d))).join("");

  const au = r.ai_usage;
  const steps = (au && au.steps) || [];
  const counts = (u) => `${esc(humanTokens(u.in))} in / ${esc(humanTokens(u.out))} out`;
  const usage = au && au.total ? steps.map((u) =>
      field(`Step: ${u.name || u.node}`,
            `<div class="rv-record"><div class="rv-row"><span class="rv-key">${
              esc(u.model || "tokens")}:</span> ${counts(u)}</div></div>`)).join("")
    + (steps.length > 1 ? field("Total", counts(au.total)) : "")
    : "";

  const details = (r.path?.length
      ? field("Steps run", String(r.path.length)) : "")
    + timingField(r)
    + (r.run_id ? field("Run ID", esc(r.run_id)) : "");

  const ticket = (workflow.tickets || []).find((t) => t.run_id === r.run_id
                                                      && (!r.halted_at || t.node_id === r.halted_at))
    || (workflow.tickets || []).find((t) => t.run_id === r.run_id);
  const carrier = ticket || r;

  const prog = r.progress && typeof r.progress === "object" ? r.progress : null;
  const doneN = prog ? Number(prog.done || 0) : 0;
  const restN = prog && prog.of ? Math.max(0, Number(prog.of) - doneN) : 0;
  const progressLine = prog && prog.of
    ? `<p class="hint"><strong>Done so far:</strong> ${esc(String(doneN))} of ${esc(String(prog.of))} items${
        doneN ? " - the ones already done are kept and will not be repeated" : ""}.</p>`
    : "";

  const partly = r.status === "halted" && doneN > 0;
  const failure = ok ? "" : `<p class="hint">Stopped at
      "${esc(nodeName(r.halted_at))}" - ${esc(haltReasonText(r.reason))}.</p>`
    + verdictLines(carrier).map((l) => `<p class="hint">${esc(l)}</p>`).join("")
    + progressLine
    + offendingHtml(carrier) + sawHtml(carrier)
    + (partly
        ? `<p class="run-note">Nothing after it ran, and your earlier steps are saved.
            You can try the ${esc(String(restN))} that failed again - the ${esc(String(doneN))} that went
            are kept and will not be sent twice - and try as many times as you like. Or go on
            with the ${esc(String(doneN))} that went: the rest wait in a run of their own under
            Run history. Or, if the ones that went were discarded on the other side, run the
            whole step again from nothing. If it needs a change to work, let me fix it first;
            the same choices are there afterwards.</p>`
        : r.status === "halted"
        ? `<p class="run-note">Nothing after it ran, and your earlier steps are saved.
            If it needs a change to work, let me fix it; otherwise run it again from
            the start, or try again from this step.</p>`
        : "");

  const half = r.partial && typeof r.partial === "object" ? r.partial : null;
  const halfLine = half
    ? `<p class="hint">${half.half === "done"
        ? `This run carries the ${esc(String(half.count))} of ${esc(String(half.of))} items that were done when it stopped partway`
        : `This run carries the ${esc(String(half.count))} of ${esc(String(half.of))} items that were still to do when the run stopped partway`
      }${half.sibling ? " - the other half is the run in the history that started at the same time" : ""}.</p>`
    : "";

  const given = (r.given || []).map((g) => field(
    esc(g.label || g.name),
    g.secret ? `<span class="muted">kept privately</span>`
             : displayText(g.value))).join("");

  const ticketOpen = ticket && ["open", "in-progress"].includes(ticket.status);
  const tid = ticket?.id;
  const exit = (fn) => () => { closeModal(); fn?.(); afterExit?.(tid); };
  const actions = [];
  if (!ok && r.status === "halted") {

    const checkSend = !partly && ticket && (r.reason === "send-unverified" || ticket.fired)
      && !ticket.user_outputs && !ticket.continued && ticket.landed !== false;
    if (checkSend)
      actions.push({ label: "Did it go through?", kind: "btn-primary",
                     onClick: exit(() => onCheckSend?.(ticket)) });
    if (ticketOpen && (partly || r.reason !== "send-unverified"))

      actions.push({ label: "Fix it", kind: checkSend || partly ? undefined : "btn-primary",
                     onClick: () => {
                       closeModal();
                       onFix?.(tid, { then: () => afterExit?.(tid) });
                     } });
    const decision = haltDecision(r, workflow, ticket);
    if (decision) actions.push({ label: decision.label, onClick: exit(decision.onClick) });
    if (partly) {
      actions.push({ label: `Try the ${restN} that failed again`, kind: "btn-primary",
                     onClick: exit(() => onResume?.(r)) });
      actions.push({ label: `Go on with the ${doneN} that went`,
                     onClick: exit(() => onProceed?.(r)) });
      actions.push({ label: "Run the whole step again",
                     onClick: exit(() => onResendAll?.(r)) });
    }
    actions.push({ label: "Run again from the start", onClick: exit(onRestart) });
    if (!checkSend && !partly)
      actions.push({ label: "Try again from this step",
                     kind: actions.length ? undefined : "btn-primary",
                     onClick: exit(() => onResume?.(r)) });
  }

  const aside = (r.set_aside || []).map((sa) => {
    const heads = (sa.ports || []).map((o) =>
      `<p class="hint">${esc(String(o.total))} of ${esc(String(o.of))} items on
        "${esc(o.port)}" did not fit the expected shape and were set aside${
        sa.policy === "proceed" ? " - there is an issue for them" : ""}.</p>`).join("");
    return `<div class="set-aside"><div class="io-sub">${esc(sa.name)}</div>${heads}
      ${offendingHtml({ offending: sa.ports })}</div>`;
  }).join("");
  openModal({
    title: ok ? (half ? "Run partially complete" : "Run complete")
              : (half ? "Run partially incomplete" : "Run stopped"),
    wide: true,
    subtitle: [workflow.name, fmtWhen(half && r.started ? r.started : r.ts)].filter(Boolean).join(" · "),
    kind: ok ? "ok" : "bad",
    body: halfLine
      + (ok
        ? section("Results", results) || `<p class="hint">Finished, but this
            workflow does not keep any results yet - ask in chat if it should.</p>`
        : failure)
      + section("Set aside", aside)
      + section("What it was given", given)
      + section("What each step did", stepsSection(r, nodeName))
      + section("AI token usage", usage)
      + section("Run details", details),
    actions,
    onDismiss: () => afterExit?.(tid),
  });
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest?.(".rv-more");
  if (!btn) return;
  const host = btn.closest(".rv-clamped");
  if (!host) return;
  const open = host.dataset.clamped !== "1";
  host.dataset.clamped = open ? "1" : "0";
  btn.textContent = open ? "Show more" : "Show less";
});
