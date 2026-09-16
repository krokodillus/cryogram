// Run controls and outcomes: run events update the canvas, and a paused or stopped run opens the matching popup
import * as api from "./api.js";
import { escapeHtml } from "./util.js";
import { openModal, closeModal } from "./modal.js";
import { notify, requestNotifyPermission } from "./notify.js";
import { fieldControl, fieldValue, wireFolderFields } from "./valueField.js";

// app.js supplies these callbacks; this module handles the run and related buttons
let ctx = null;
const states = new Map();
// A stop request carries this run's id, so it cannot apply to a newer run that started just after
let runTurnId = "";
export const getRunTurnId = () => runTurnId;
// Full details of where a run was paused are stored in memory so the banner can reopen the same popup without a server request
let lastHalt = null;
let running = false;

// Wires the Run and Stop buttons
export function initRun(appCtx) {
  ctx = appCtx;
  const btn = document.getElementById("runWorkflow");
  if (btn) btn.onclick = beginRun;
  const stopBtn = document.getElementById("stopWorkflow");
  if (stopBtn) stopBtn.onclick = async () => {
    stopBtn.disabled = true;
    stopBtn.textContent = "Stopping…";
    await api.stopWorkflow(ctx.getWorkflow().id, runTurnId).catch(() => {});

  };
}

// Node boxes are rebuilt on every render; the stored states repaint them
export function applyRunStates() {
  document.querySelectorAll("#workflow .node").forEach((box) => {
    box.classList.remove("run-running", "run-done", "run-failed", "run-awaiting",
                         "run-skip");
    const s = states.get(box.dataset.id);
    if (s) box.classList.add(s);
  });
}

// Clears the canvas marks and the stored pause when the workflow or run changes
export function clearRunStates() {
  states.clear();
  applyRunStates();

  lastHalt = null;
}

// Marks one step's run state and repaints
function mark(id, state) {
  states.set(id, state);
  applyRunStates();
}

// Builds the run form from the user-input steps and per-run settings, prefilled from stored values, and a secret is never prefilled
function entryFields(workflow, envs) {
  envs = Array.isArray(envs) ? envs : (envs ? [envs] : []);
  const pins = workflow.env_bindings || {};
  const merged = [];
  envs.forEach((e) => (e.variables || []).forEach((v) => {
    const pin = pins[v.name];
    if (pin && pin !== `env:${e.id}:${v.name}`) return;
    merged.push(v);
  }));
  const vars = Object.fromEntries([
    ...merged,
    ...(workflow.variables || []),
  ].map((v) => [v.name, v]));
  const fields = [];
  const seen = new Set();
  // A choice that depends on an earlier step's output is collected mid-run, not up front
  const dependent = new Set((workflow.edges || []).map((e) => e.dst));
  workflow.nodes.filter((n) => n.type === "user-input" && !dependent.has(n.id)).forEach((n) => {
    (n.outputs || []).forEach((p) => {
      const secret = p.type === "secret";
      fields.push({
        name: p.name,
        label: p.label || p.name,
        type: p.type || "text",
        options: p.options || [],
        secret,
        node: n.name,

        value: secret ? "" : (vars[p.name]?.value ?? ""),
      });
      seen.add(p.name);
    });
  });

  (workflow.variables || []).forEach((v) => {
    if (v.persistent === false && !seen.has(v.name)) {
      fields.push({
        name: v.name, label: v.name, type: "text", options: [],
        secret: !!v.secret, value: v.secret ? "" : (v.value ?? ""),
      });
      seen.add(v.name);
    }
  });
  return fields;
}

// Secrets typed into a run form are held in this variable only, re-sent on each resume, and never saved to disk
let secretBag = {};

// Opens the run form and starts the run with its values
export async function beginRun(prefill = null) {
  const workflow = ctx.getWorkflow();
  if (!workflow || !workflow.nodes.length) return;

  if (ctx.isChatBusy?.()) return;
  requestNotifyPermission();

  const fields = entryFields(workflow, ctx.getEnvironments?.());
  // After a fix, the form can be prefilled with the failed run's own entry values so one click confirms them
  if (prefill)
    fields.forEach((f) => {
      if (f.name in prefill && !f.secret) f.value = prefill[f.name];
    });
  // Every value the run would pause for midway is asked for up front, in this one form
  try {
    const pf = await api.getRunPreflight(workflow.id);
    const have = new Set(fields.map((f) => f.name));
    (pf.missing || []).forEach((m) => {
      if (!have.has(m.name))
        fields.push({ ...m, options: m.options || [], value: "" });
    });
  } catch { }
  if (!fields.length) return startRun({});
  const body = fields.map((f, i) => `
    <div class="form-group"><label class="form-label" for="runIn_${i}">${escapeHtml(f.label)}
      <span class="label-optional">${f.secret ? "secret" : escapeHtml(f.type)}${f.node ? ` &middot; ${escapeHtml(f.node)}` : ""}</span></label>
      ${fieldControl(f, `runIn_${i}`)}
      ${f.description ? `<p class="form-hint">${escapeHtml(f.description)}</p>` : ""}
    </div>`).join("");
  const modalRoot = openModal({
    title: "Run workflow",
    wide: true,
    body,

    actions: [
      { label: "Start run", kind: "btn-primary", onClick: async (root) => {
          const btn = root.querySelector(".btn-primary");
          if (btn) { btn.disabled = true; btn.textContent = "Uploading…"; }
          const entry = {}, secrets = {};
          try {
            for (let i = 0; i < fields.length; i++) {
              const val = await fieldValue(fields[i], `runIn_${i}`);
              // An empty field means "not supplied": the stored value is used, or the run pauses and asks
              if (val === "") continue;
              if (fields[i].secret) secrets[fields[i].name] = val;
              else entry[fields[i].name] = val;
            }
          } catch (err) {
            if (btn) { btn.disabled = false; btn.textContent = "Start run"; }
            toast(`Upload failed: ${err.message || err}`, "bad");
            return;
          }
          closeModal();
          startRun(entry, undefined, secrets);
        } },
    ],
  });
  wireFolderFields(modalRoot);
}

// Switches the Run and Stop buttons and tells app.js whether a run is in flight
export function setRunning(on) {
  running = on;
  const btn = document.getElementById("runWorkflow");
  if (btn) btn.innerHTML = on ? "Running&#8230;" : "&#9654; Run";
  const stopBtn = document.getElementById("stopWorkflow");
  if (stopBtn) { stopBtn.disabled = !on; stopBtn.textContent = "Stop"; }

  if (on) renderRunWait(ctx?.getWorkflow?.());
  if (on) ctx.onRunStart?.(); else ctx.onRunEnd?.();
}

// A browser step needs the person to do something in the window it opened
export function userRequestPopup(req) {
  const workflow = ctx.getWorkflow();
  if (!req?.iid || document.querySelector(`[data-window-req="${req.iid}"]`)) return;
  notify(`${workflow?.name || "Cryogram"} - needs you in the browser window`,
         req.message || "A step is waiting for you.",
         { workflowId: workflow?.id, tag: "run-input" });
  openModal({
    title: "Something for you to do",
    body: `<p data-window-req="${escapeHtml(req.iid)}">${escapeHtml(
      req.message || "The step is waiting for you in the browser window.")}</p>
      <p class="muted">The window stays open while you do it. The step carries
        on the moment you confirm.</p>`,
    actions: [{ label: "I've done it", kind: "btn-primary", onClick: async () => {
      closeModal();
      await api.answerInteraction(req.iid, "done").catch(() => {});
    } }],
  });
}

// One event-to-canvas mapping, shared by the live stream and the catch-up after a reload
export function markFromEvent(ev) {
  if (ev.type === "turn") { runTurnId = ev.turn_id || ""; return; }
  if (ev.type === "user-request") { userRequestPopup(ev); return; }
  if (ev.type === "node-start") mark(ev.node, "run-running");
  else if (ev.type === "node-done") mark(ev.node, "run-done");
  else if (ev.type === "node-skip") mark(ev.node, "run-skip");
}

// The same outcome handling runs live and after a page reload, and per-run secrets are collected again by the masked form
export function handleRunOutcome(done) {
  const workflow = ctx.getWorkflow();
  const result = done.result || {};
  if (result.status === "halted" && result.halted_at) {
    mark(result.halted_at,
         result.waiting_on_you || result.reason === "stopped-by-user"
           ? "run-awaiting" : "run-failed");
    applyRunStates();
  }
  if (done.workflow) ctx.setWorkflow(done.workflow);

  if (result.status !== "halted") secretBag = {};

  const name = workflow?.name || "Cryogram";
  const pid = workflow?.id;
  const note = (title, body, tag) => notify(`${name} - ${title}`, body, { workflowId: pid, tag });

  lastHalt = (result.status === "halted" && result.waiting_on_you) ? result : null;
  if (result.status === "completed") {
    const asideN = Object.values(result.set_aside || {})
      .flatMap((e) => e.ports || []).reduce((n, o) => n + (o.total || 0), 0);
    toast(asideN ? `Run completed - ${asideN} item${asideN === 1 ? "" : "s"} set aside`
                 : "Run completed", "ok");
    note("finished", asideN ? `The workflow ran to the end; ${asideN} items were set aside.`
                            : "The workflow ran to the end.", "run-done");
    // The results window opens as soon as a run completes
    ctx.openRun?.(result.run_id);
  } else if (result.reason === "stopped-by-user") toast("Run stopped", "ok");
  else if (result.status === "halted") {
    openHaltPopup(result);
    if (result.reason === "awaiting-approval")
      note("your approval is needed", "A step is waiting to write to an external system.", "run-input");
    else if (result.reason === "missing-value")
      note("your input is needed", "A value is needed to continue the run.", "run-input");
    else if (result.reason === "input-needed")
      note("your input is needed", "The run is paused for your choice.", "run-input");
    else if (result.reason === "browser-closed")
      note("the browser window closed", "Open it again to carry on from that step.", "run-input");
    else if (result.reason === "not-confirmed")
      note("needs you in the browser window", "A step is waiting for you to do something.", "run-input");
    else if (result.waiting_on_you)
      note("needs you to check something", "A step sent data but got no confirmation.", "run-input");
    else
      note("hit a problem", "A step couldn't finish - it needs a look.", "run-error");
  } else if (result.status === "error") {
    toast(`Run error: ${result.reason}`, "bad");
    note("hit a problem", "The run ended with an error.", "run-error");
  }
  renderRunWait(ctx.getWorkflow());
}

// One function picks what a paused run shows: a form when it is asking you, the run modal when it stopped
export function openHaltPopup(result) {
  ctx.markRunSeen?.(result.run_id);
  if (result.reason === "awaiting-approval") approvalPopup(result);
  else if (result.reason === "missing-value") missingValuePopup(result);
  else if (result.reason === "input-needed") inputNeededPopup(result);
  else if (["send-unverified", "fired-unverified"].includes(result.reason)) {

    const workflow = ctx.getWorkflow();
    const t = (workflow?.tickets || []).find((x) => x.id === result.ticket_id)
      || (workflow?.tickets || []).find((x) => x.run_id === result.run_id
                                              && x.reason === result.reason
                                              && (!result.halted_at
                                                  || x.node_id === result.halted_at));
    if (t) didItLandFlow(t);
  } else if (result.status === "halted") openStoppedRun(result);
}

// A stopped run opens the one run modal, the same view the Run history tab shows, with the share offer on top
function openStoppedRun(result) {
  ctx.openRun?.(result.run_id, { live: true, result });
}

// A stop reason that carries a decision of its own gets one extra button on the run modal
export function haltDecision(r, workflow, ticket) {
  const tid = ticket?.id || r.ticket_id;
  if (r.reason === "empty-output") {
    const node = (workflow?.nodes || []).find((n) => n.id === r.halted_at);
    const ports = r.empty_ports || ticket?.empty_ports || [];
    if (!node || !ports.length) return null;
    return { label: "Empty can be normal here - don't stop for it again",
             onClick: async () => {
               await api.setMayBeEmpty(workflow.id, node.id, ports).catch(() => {});
               if (tid) await api.setTicketStatus(workflow.id, tid,
                                                  { status: "dismissed" }).catch(() => {});
               toast("Noted - an empty result won't stop this step again", "ok");
             } };
  }
  if (r.reason === "nothing-to-do") {
    return { label: "That's expected - nothing to send today",
             onClick: async () => {
               if (tid) await api.setTicketStatus(workflow.id, tid,
                                                  { status: "dismissed" }).catch(() => {});
               toast("Noted - the run just had nothing to act on", "ok");
             } };
  }
  return null;
}

// A run that is asking you shows a banner naming the question, and clicking the banner reopens its form
function askText(reason, name) {
  const step = name ? `"${name}"` : "a step";
  return {
    "missing-value": `${step} needs a value from you`,
    "input-needed": `${step} needs your choice`,
    "browser-closed": `the browser window closed before ${step} was finished`,
  "not-confirmed": `${step} is waiting for you to do something in the browser window`,
  "awaiting-approval": `${step} needs your approval before it sends`,
    "send-unverified": `${step} sent something - check whether it arrived`,
    "fired-unverified": `${step} acted, but what came back needs your check`,
  }[reason] || null;
}

// Draws or hides the banner for a paused run
export function renderRunWait(workflow) {
  const el = document.getElementById("runWaitBanner");
  if (!el) return;

  const w = lastHalt
    ? { run_id: lastHalt.run_id, node_id: lastHalt.halted_at,
        reason: lastHalt.reason,
        node_name: (workflow?.nodes || []).find((n) => n.id === lastHalt.halted_at)?.name || "" }
    : workflow?._run_wait;
  const text = (!running && w) ? askText(w.reason, w.node_name) : null;
  if (!text) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  el.innerHTML = `<span class="run-wait-body"><span class="run-wait-title">The run is waiting -
      </span> ${escapeHtml(text)}</span>
    <span class="run-wait-open">Open</span>`;
  el.onclick = () => {
    if (lastHalt && lastHalt.run_id === w.run_id) return openHaltPopup(lastHalt);

    openHaltPopup({ run_id: w.run_id, status: "halted", halted_at: w.node_id,
                    reason: w.reason, verdict: w.verdict || {} });
  };
  el.onkeydown = (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.onclick(); }
  };

  if (w.node_id && !states.get(w.node_id)) {
    mark(w.node_id, w.reason === "awaiting-approval" ? "run-awaiting" : "run-failed");
  }
}

// Sends the start or resume request, then follows the run's progress by polling
async function startRun(entry, resume, secrets) {
  const workflow = ctx.getWorkflow();

  if (!resume) secretBag = {};
  if (secrets) Object.assign(secretBag, secrets);
  clearRunStates();
  setRunning(true);
  // The request only starts the run; progress is drawn from polling, so a dropped connection does not affect the run
  try {
    await api.startRun(workflow.id, {
      entry_inputs: entry || {},
      run_id: resume?.runId,
      approvals: resume?.approvals || [],

      ...(resume?.stepOutputs ? { step_outputs: resume.stepOutputs } : {}),
      ...(resume?.unfire ? { unfire: resume.unfire } : {}),
      secret_inputs: secretBag,
    });
  } catch (err) {
    setRunning(false);
    toast(`Run failed: ${err.message || err}`, "bad");
    return;
  }
  const attached = await ctx.reattach?.();
  if (!attached) {

    setRunning(false);
    const snap = await api.getTurn(workflow.id).catch(() => null);
    const done = (snap?.events || []).filter((e) => e.type === "done").pop();
    for (const ev of snap?.events || []) markFromEvent(ev);
    if (done?.result) handleRunOutcome({ result: done.result });
    else toast("The run didn't start - try again.", "bad");
  }
}

// The approval popup shows the real resolved values about to be sent, not a description written earlier
function approvalPopup(result) {
  const workflow = ctx.getWorkflow();
  const node = workflow.nodes.find((n) => n.id === result.halted_at);
  const resume = () => startRun({}, { runId: result.run_id, approvals: [result.halted_at] });
  openModal({
    title: "Approval needed",
    wide: true,
    body: `<p><strong>${escapeHtml(node?.name || result.halted_at)}</strong> is about to
        write to an external system. Everything before it has run and is checkpointed;
        nothing fires until you decide.</p>
      <p class="run-impact">${escapeHtml(node?.external_impact || "No declared external impact.")}</p>`

      + ((result.verdict?.sending || []).length
        ? `<div class="send-preview">${(result.verdict.sending).map((f) =>


            `<div class="send-line"><span class="rv-key">${escapeHtml(f.label)}:</span> ${escapeHtml(f.text)}</div>`).join("")}</div>`
        : ""),

    actions: [

      { label: "Decline", kind: "btn-danger", side: "left",
        onClick: async () => {
          closeModal();
          await api.declineRun(workflow.id, result.run_id).catch(() => {});
          clearRunStates();
          const p = ctx.getWorkflow();
          if (p && p._run_wait?.run_id === result.run_id) delete p._run_wait;
          renderRunWait(p);
          toast("Declined - nothing was sent. The run is under Run history "
                + "if you want to pick it up later.", "ok");
        } },
      { label: "Approve, don't ask again",
        onClick: async () => {
          closeModal();
          await api.setNodeApproval(workflow.id, result.halted_at, true).catch(() => {});
          resume();
        } },
      { label: "Approve & continue", kind: "btn-primary",
        onClick: () => { closeModal(); resume(); } },
    ],
  });
}

// A missing value pauses the run and asks in place; a value that fills an empty setting is kept for future runs
function missingValuePopup(result) {

  const humanise = (n) => {
    const w = String(n).replace(/[_-]+/g, " ").trim();
    return w ? w[0].toUpperCase() + w.slice(1) : n;
  };
  const fields = (result.verdict?.missing_fields || [
    ...(result.verdict?.missing_values || []).map((n) => ({ name: n, secret: false })),
    ...(result.verdict?.missing_secrets || []).map((n) => ({ name: n, secret: true })),
  ]).map((f) => ({ ...f, label: f.label || humanise(f.name),
                   type: f.type === "secret" ? "text" : (f.type || "text"),
                   options: f.options || [], value: "" }));
  const body = fields.map((f, i) => `
    <div class="form-group"><label class="form-label" for="mv_${i}">${escapeHtml(f.label)}
      ${f.secret ? `<span class="label-optional">secret</span>` : ""}</label>
      ${fieldControl(f, `mv_${i}`)}
      ${f.description ? `<p class="form-hint">${escapeHtml(f.description)}</p>` : ""}</div>`).join("")
    + `<p class="hint">A value that fills an empty setting is kept for future runs -
        change it any time under Inputs.${fields.some((f) => f.secret)
        ? " Keys and passwords are used for this run only and never stored." : ""}</p>`;
  openModal({
    title: "A value is needed",
    wide: true,
    body,
    actions: [
      { label: "Continue the run", kind: "btn-primary", onClick: async () => {
          const entry = {}, secretVals = {};
          for (let i = 0; i < fields.length; i++) {
            const val = await fieldValue(fields[i], `mv_${i}`);
            if (val === "") continue;
            if (fields[i].secret) secretVals[fields[i].name] = val;
            else entry[fields[i].name] = val;
          }
          closeModal();
          startRun(entry, { runId: result.run_id }, secretVals);
        } },
    ],
  });
  wireFolderFields();
  document.getElementById("mv_0")?.focus();
}

// A mid-run choice can offer options produced by the steps that just ran
function inputNeededPopup(result) {
  const req = result.verdict?.input_request || {};
  const fields = (req.fields || []).map((f) => ({
    name: f.name, label: f.label || f.name,
    type: (f.options && f.options.length) ? "enum" : (f.type || "text"),
    options: f.options || [], secret: !!f.secret, value: "",
  }));
  const body = `<p>${escapeHtml(req.prompt || "Your choice")}</p>` + fields.map((f, i) => `
    <div class="form-group"><label class="form-label" for="in_${i}">${escapeHtml(f.label)}
      ${f.secret ? `<span class="label-optional">secret</span>` : ""}</label>
      ${fieldControl(f, `in_${i}`)}</div>`).join("");
  openModal({
    title: "Your input is needed",
    wide: true,
    body,
    actions: [
      { label: "Continue the run", kind: "btn-primary", onClick: async () => {
          const entry = {}, secretVals = {};
          for (let i = 0; i < fields.length; i++) {
            const val = await fieldValue(fields[i], `in_${i}`);
            if (val === "") continue;
            if (fields[i].secret) secretVals[fields[i].name] = val;
            else entry[fields[i].name] = val;
          }
          closeModal();
          startRun(entry, { runId: result.run_id }, secretVals);
        } },
    ],
  });
  wireFolderFields();
  document.getElementById("in_0")?.focus();
}

// Only the user can check whether an unconfirmed send arrived; this flow asks, then records what actually arrived
export function didItLandFlow(ticket) {
  const workflow = ctx.getWorkflow();
  const node = workflow.nodes.find((n) => n.id === ticket.node_id);
  const name = node?.name || "this step";
  const why = verdictLines(ticket);
  // A send that is known to have happened skips the did-it-arrive question, so it cannot be sent twice
  if (ticket.fired) {
    sendLandedForm(ticket, name);
    return;
  }
  openModal({
    title: "Did it go through?",
    body: `${why.map((w) => `<p class="setup-line">${escapeHtml(w)}</p>`).join("")}
      <p>Check the outside system "${escapeHtml(name)}" sends to - did the
        data arrive there?</p>
      <p class="hint">Everything before this step is saved.</p>`,
    actions: [
      { label: "Yes - it arrived", kind: "btn-primary",
        onClick: () => { closeModal(); sendLandedForm(ticket, name); } },
      // A failed send is fixed first; the re-send then runs the corrected step, not the code that just failed
      { label: "No - it didn't",
        onClick: async () => {
          closeModal();
          await api.setTicketStatus(workflow.id, ticket.id, { landed: false })
            .catch(() => {});
          ticket.landed = false;
          openModal({
            title: "A failed send",
            body: `<p>Then nothing went out, and nothing will be sent twice.
                I can look at why "${escapeHtml(name)}" failed and fix it -
                once the fix is in, this issue offers to continue the run,
                which sends it with the corrected step.</p>`,
            actions: [
              { label: "Ask the Builder Agent to fix", kind: "btn-primary",
                onClick: () => { closeModal(); ctx.fixIssue?.(ticket.id); } },
              { label: "Not now" },
            ],
          });
        } },
    ],
  });
}

// The form for what an unconfirmed send actually produced; submitting records the step as done and offers to continue the run
async function sendLandedForm(ticket, name) {
  const workflow = ctx.getWorkflow();
  let form;
  try {
    form = await api.getStepOutputsForm(workflow.id, ticket.run_id);
  } catch {
    toast("That run can no longer be continued - it was already picked back "
          + "up, or it has been cleared away.", "bad");
    return;
  }
  const all = (form.fields || []).map((f) => ({ ...f, value: f.value ?? "" }));
  // Values already known from the step's own checks are not shown - there is nothing to ask
  const certain = all.filter((f) => f.locked);
  const fields = all.filter((f) => !f.locked);
  const submit = async () => {
    const outs = {}, unsure = [];
    certain.forEach((f) => { outs[f.name] = f.value; });
    for (let i = 0; i < fields.length; i++) {
      if (document.getElementById(`uo_ns_${i}`)?.checked) {
        unsure.push(fields[i].name);
        continue;
      }
      outs[fields[i].name] = await fieldValue(fields[i], `uo_${i}`);
    }
    let r;
    try {
      r = await api.submitUserOutputs(workflow.id, ticket.id,
                                      { outputs: outs, unsure });
    } catch (e) {
      closeModal();
      toast(String(e.message || e), "bad");
      return;
    }
    ticket.user_outputs = { given: r.given, unsure: r.unsure };
    ticket.continued = true;
    closeModal();
    const complete = !r.unsure.length;
    openModal({
      title: complete ? "All set" : "Recorded",
      body: complete
        ? `<p>Everything the later steps need is in place - the run can carry
            on from the next step.</p>`
        : `<p>Recorded. ${r.unsure.length === 1 ? "One field" : "Some fields"}
            couldn't be seen (${escapeHtml(r.unsure.join(", "))}) - if a later
            step needs one, the run will stop there and ask.</p>`,
      actions: [
        { label: "Continue the run", kind: "btn-primary",
          onClick: () => { closeModal(); startRun({}, { runId: ticket.run_id }); } },
        { label: "Not now" },
      ],
    });
  };
  const lead = ticket.fired
    ? `<p>"${escapeHtml(name)}" DID send its data, but the result wasn't what
        it expected. Check the outside system and fill in what actually
        arrived, so the run can carry on. Tick anything you can't see.</p>`
    : `<p>Good - fill in what "${escapeHtml(name)}" produced, so the
        steps after it have what they need. Tick anything you can't see.</p>`;
  const body = lead
    + fields.map((f, i) => `
      <div class="form-group"><label class="form-label" for="uo_${i}">${
        escapeHtml(f.label)}</label>
        ${fieldControl(f, `uo_${i}`)}
        <label class="uo-unsure"><input type="checkbox" id="uo_ns_${i}">
          Can't see this</label>
        ${f.description
          ? `<p class="form-hint">${escapeHtml(f.description)}</p>` : ""}</div>`).join("")
    + (fields.length ? "" : `<p class="hint">Nothing needs filling in - the
        step's own checks already say what it produced.</p>`);
  openModal({
    title: "What did it produce?",
    body,
    actions: [
      { label: "Submit", kind: "btn-primary", onClick: submit },
      { label: "Back", onClick: () => { closeModal(); didItLandFlow(ticket); } },
    ],
  });
}

// Continuing a paused run starts from the step it paused at; the completed steps are read from the checkpoint
export function resumeRun(runId) {
  startRun({}, { runId });
}

// Run the whole step again: the items that went are treated as discarded on the other side, so the step starts from nothing
export function resendStep(runId, nodeId) {
  startRun({}, { runId, unfire: [nodeId] });
}

// Opens the run form prefilled with a failed run's entry values
export function beginRunWith(entry) {
  beginRun(entry && Object.keys(entry).length ? entry : null);
}

// The one-line note a run history row shows for a run that paused or stopped
export function haltNote(r, tickets) {
  if ((tickets || []).some((t) => t.run_id === r.run_id))
    return "see the Issues tab";
  return haltReasonText(r.reason);
}

// Every reason a run can pause or stop has a plain-language sentence, so no internal code reaches the user
const HALT_TEXT = {
  "awaiting-approval": "waiting for your approval",
  "approval-declined": "you declined the send - nothing went out",
  "environment-check-failed": "something it needs was not ready - fix that, then try again from this step",
  "egress-blocked": "an outside connection was not on the allowed list - Fix it "
    + "asks me to allow it",
  "path-blocked": "a folder on this computer was not on the allowed list - Fix it "
    + "asks me to allow it",
  "missing-value": "a value was missing (provide it and retry)",
  "input-needed": "waiting for your input (run it again to continue)",
  "stopped-by-user": "you stopped it",
  "rate-limited": "the service asked it to slow down - try again from this step "
    + "in a few minutes",
  "model-unavailable": "the AI service could not be reached - try again from this step",
  "model-not-chosen": "this step has no AI model that can be picked for it - choose one in its panel, or give a model a Cost on the Admin page",
  "wrong-file-kind": "the file given is not the kind this workflow was built for - give it the right kind, or ask in chat to change the workflow",
  "service-unavailable": "an outside service did not answer in time - try again "
    + "from this step",
  "browser-window-open": "a browser window for this workflow was already open "
    + "- close it, then try again from this step",
  "empty-output": "a step found nothing at all, where earlier runs found "
    + "items",
  "send-unverified": "it sent your data but never got confirmation - check "
    + "whether it arrived",
  "ai-timed-out": "an AI step took too long to answer - it needs a look",
  "no-progress": "a step went too long without any sign of progress and was "
    + "stopped - it needs a look",
  "items-set-aside": "some items did not fit the expected shape and were set "
    + "aside - the rest went through",
  "input-shape": "the file it was given doesn't look like the one it expects",
  "interrupted": "the app was closed while this ran - carry on from where it stopped",
  "branch-condition-error": "a condition between two steps could not be decided, so the "
    + "run stopped rather than skip a step - ask for the condition to be fixed",
  "file-unsupported": "an AI step was given a file its model does not read - choose "
    + "another model on the step's panel, or ask for a step that reads the file first",
  "no-effect": "a step was given items to act on and did nothing with them, "
    + "without saying why",
  "nothing-to-do": "an earlier step found nothing for a sending step to act on "
    + "- expected, or something is off with the data",
  "ai-answer-truncated": "an AI answer was cut off before it finished",
  "ai-answered-blank": "an AI step returned an empty answer",
  "ai-call-failed": "an AI step failed",
  "fired-unverified": "an action was sent but its result could not be confirmed",
  "input-check-failed": "a step received a value in the wrong shape",
  "input-contract-failed": "a step could not read one of its values",
  "input-unconnected": "a step was missing one of its values",
  "output-check-failed": "a step's result failed its checks",
  "output-check-failed (standard)": "a step's result failed its checks",
};
// A stop reason in plain words
export function haltReasonText(reason) {
  if (!reason) return "did not finish";

  if (String(reason).startsWith("node threw"))
    return "a step hit an error while running";
  return HALT_TEXT[reason]
    || HALT_TEXT[String(reason).replace(/ \(.*\)$/, "")]
    || String(reason).replace(/-/g, " ");
}

// When values miss the expected shape, the user sees the actual rows and their problems, bounded and counted
const OFFENDING_ROW_CHARS = 600;
export function offendingHtml(carrier) {
  const off = carrier?.offending;
  if (!Array.isArray(off) || !off.length) return "";
  const list = (v) => Array.isArray(v) ? v.map(String).join(", ")
    : (v && typeof v === "object") ? Object.keys(v).join(", ") : String(v);
  const pre = (v) => {

    if (v && typeof v === "object" && !Array.isArray(v)
        && ("found" in v || "expected" in v) && Object.keys(v).length <= 2) {
      return `<div class="offending-row">${
        "expected" in v ? `<div>It needs: <strong>${escapeHtml(list(v.expected))}</strong></div>` : ""}${
        "found" in v ? `<div>What arrived has: <strong>${escapeHtml(list(v.found))}</strong></div>` : ""}</div>`;
    }
    let t;
    try { t = JSON.stringify(v, null, 1); } catch { t = String(v); }
    if (t.length > OFFENDING_ROW_CHARS) t = t.slice(0, OFFENDING_ROW_CHARS) + " ...";
    return `<pre class="offending-row">${escapeHtml(t)}</pre>`;
  };
  return off.map((o) => {
    const rows = (o.rows || []).map((r) => `<li>
        ${r.item ? `<div class="offending-head">Item ${r.item}</div>` : ""}
        ${(r.problems || []).map((q) => `<div class="setup-line">${escapeHtml(q)}</div>`).join("")}
        ${pre(r.value)}</li>`).join("");
    const head = o.of > 1
      ? `${o.total} of ${o.of} items did not fit the expected shape`
        + (o.total > (o.rows || []).length ? ` - showing the first ${o.rows.length}` : "")
      : "What arrived";
    return `<details class="offending" open><summary>${escapeHtml(head)}</summary>
      <ul class="offending-list">${rows}</ul></details>`;
  }).join("");
}

// What the step saw when it failed: the page it was on and the last replies it got, from the record
export function sawHtml(carrier) {
  const saw = carrier?.saw;
  if (!saw || typeof saw !== "object") return "";
  const blobUrl = (ref) => `/api/blobs/${String(ref).replace(/^blob:/, "")}`;
  let out = "";
  if (saw.page && typeof saw.page === "object") {
    const pg = saw.page;
    out += `<details class="offending" open><summary>What the browser was showing</summary>
      ${pg.url ? `<div class="setup-line">Page: ${escapeHtml(pg.url)}${pg.title ? ` - "${escapeHtml(pg.title)}"` : ""}</div>` : ""}
      ${pg.screenshot ? `<img class="saw-shot" alt="the page when the step stopped" src="${blobUrl(pg.screenshot)}">` : ""}
      ${pg.page ? `<div class="hint"><a href="${blobUrl(pg.page)}" target="_blank" rel="noopener">Open the saved page</a></div>` : ""}
    </details>`;
  }

  const written = saw.written && typeof saw.written === "object" ? saw.written : null;
  if (written && Object.keys(written).length) {
    out += `<details class="offending" open><summary>What it had written before it stopped</summary>
      <ul class="offending-list">${Object.entries(written).map(([k, v]) => {
        let txt = typeof v === "string" ? v : JSON.stringify(v, null, 1);
        if (txt.length > OFFENDING_ROW_CHARS) txt = txt.slice(0, OFFENDING_ROW_CHARS) + " ...";
        return `<li><div class="offending-head">${escapeHtml(k)}</div>
          <pre class="offending-row">${escapeHtml(txt)}</pre></li>`;
      }).join("")}</ul></details>`;
  }
  const traffic = Array.isArray(saw.traffic) ? saw.traffic : [];
  if (traffic.length) {
    out += `<details class="offending" open><summary>What the service said</summary>
      <ul class="offending-list">${traffic.map((t) => {
        let head = String(t.response_head || "");
        if (head.length > OFFENDING_ROW_CHARS) head = head.slice(0, OFFENDING_ROW_CHARS) + " ...";
        return `<li><div class="offending-head">${escapeHtml(`${t.method || ""} ${t.host || ""}${t.path || ""}`)}
          &rarr; ${escapeHtml(String(t.status ?? "no answer"))} ${escapeHtml(t.reason || "")}</div>
          ${head ? `<pre class="offending-row">${escapeHtml(head)}</pre>` : ""}</li>`;
      }).join("")}</ul></details>`;
  }
  return out;
}

// Collects a result's reasons into plain sentences for the popups
export function verdictLines(result) {
  const v = result.verdict;
  if (!v || typeof v !== "object") return [];
  const all = Object.values(v)
    .flatMap((x) => (Array.isArray(x) ? x : [x]))
    .filter((x) => typeof x === "string" && x.trim());
  // Past eight reasons the rest are shown as a count, not dropped
  if (all.length > 8) {
    return all.slice(0, 8).concat(`...and ${all.length - 8} more like this`);
  }
  return all;
}

// A four-second toast
function toast(msg, kind) {
  document.querySelector(".toast")?.remove();
  const t = document.createElement("div");
  t.className = `toast ${kind || ""}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}
