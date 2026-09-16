// The workflow page: header, tabs, the panes and their actions, loading a workflow and its environments
import * as api from "./api.js";
import { escapeHtml as esc, sentence, envChip, envChipNone, humanTokens, inlineEdit, tabMenu } from "./util.js";
import { renderMarkdown } from "./md.js";
import { renderWorkflow, refitWorkflowView } from "./workflow.js";
import { renderChat, say, enterBusy, exitBusy, resetChatView, setChatWorkflow, stageAttachment } from "./chat.js";
import { setTitle } from "./notify.js";
import { renderWorkflowSkills, renderSamples } from "./workflowSkills.js";
import { renderVariables } from "./variables.js";
import { openNodeDetail, closeNodeDetail } from "./nodeDetail.js";
import { clearRunStates, getRunTurnId, verdictLines, offendingHtml, sawHtml, haltNote, haltReasonText, resumeRun, resendStep, beginRun, beginRunWith, didItLandFlow, renderRunWait, openHaltPopup } from "./run.js";
import { openModal, closeModal, modalOpen } from "./modal.js";
import { runModal, resultSummary } from "./runResults.js";
import { S, owns } from "./workspaceState.js";
import { attachToTurn, clearBuildToast, onTurnStarted, repaintNodeStates } from "./liveTurn.js";

// Opens the step drawer with the model and prompt edit hooks
export function selectNode(node) {
  openNodeDetail(node, S.workflow, () => renderWorkflow(S.workflow, selectNode),
                 { onChangeModel, onChangeAnswer, onEditPrompt });
}

const WORKFLOW_TABS = ["workflow", "variables", "results", "issues", "purpose"];

const LEGACY_TABS = { data: "variables", samples: "purpose", skills: "purpose" };

// Switches the workflow page's tab and re-renders the active pane
export function setWorkflowTab(tab) {
  tab = LEGACY_TABS[tab] || tab;
  S.workflowTab = WORKFLOW_TABS.includes(tab) ? tab : "workflow";
  document.querySelectorAll("#workflowTabs .tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.ptab === S.workflowTab));
  WORKFLOW_TABS.forEach((t) =>
    document.getElementById(`ptab-${t}`).classList.toggle("hidden", t !== S.workflowTab));

  const panelToggle = document.getElementById("wfPanelToggle");
  if (panelToggle) panelToggle.hidden = S.workflowTab !== "workflow";
  if (!S.workflow) return;

  if (S.workflowTab === "workflow") {
    renderWorkflow(S.workflow, selectNode);
    repaintNodeStates();
    renderSetupNotice(S.workflow);
    renderRunWait(S.workflow);
    renderChat(S.workflow, onTurnStarted, S.appSettings, checkRunNotBusy,
      () => api.stopWorkflow(S.workflow.id, S.currentTurnId).catch(() => {}),
      S.chatBusy);
  } else if (S.workflowTab === "variables") {

    renderVariables(S.workflow, S.workflowEnvs, S.workflow._variable_resolution);
  }
  else if (S.workflowTab === "results") renderResults();
  else if (S.workflowTab === "issues") renderIssues();
  else if (S.workflowTab === "purpose") {

    renderPurpose();
    renderInstructions();
    renderVersions();
    const paintSamples = () => renderSamples(S.workflow, {
      onDelete: async (name) => {
        try {
          const r = await api.deleteSample(S.workflow.id, name);
          S.workflow.samples = r.samples || [];
        } catch { }
        paintSamples();
      },
    });
    paintSamples();
    renderWorkflowSkills(S.workflow);
  }
}

// Sends a message into a workflow's chat from anywhere in the app
export function sendToBuilder(id, text) {
  S.pendingFirstMessage = { id, text };
  location.hash = `#/workflow/${id}`;
}

// One workflow does one thing at a time: Run is disabled while chat works, and the other way round
function refreshBusyUI() {
  const runBtn = document.getElementById("runWorkflow");
  if (runBtn) runBtn.disabled = S.chatBusy || S.runBusy;

  const pill = document.getElementById("wfWorking");
  if (pill) pill.hidden = !S.chatBusy;

  const empty = document.querySelector("#workflow .wf-empty");
  if (empty) empty.classList.toggle("hidden", S.chatBusy);

  const stopBtn = document.getElementById("stopWorkflow");
  if (stopBtn && !S.runBusy) {
    stopBtn.disabled = true;
    stopBtn.textContent = "Stop";
  }
}

// ONE busy flag: the page's S.chatBusy and the composer's state move together, always through here
export function setChatBusy(v) { S.chatBusy = v; if (v) enterBusy(); else exitBusy(); refreshBusyUI(); }

// A run starting or ending; an offer waiting for the slot gets its chance when one ends
export function setRunBusy(v) { S.runBusy = v; refreshBusyUI(); if (!v) flushPendingShare(); }

// A run is in progress: offer to stop it or wait; the caller's action is simply not taken and re-issuing it is up to the user
export function runBusyModal() {
  openModal({
    title: "A workflow run is in progress",
    body: `<p>This workflow is currently running. Stop it now and try again in
        a moment, or wait until it finishes.</p>`,
    actions: [
      { label: "Stop the run", kind: "btn-danger", onClick: async () => {
          closeModal();

          await api.stopWorkflow(S.workflow.id, getRunTurnId()).catch(() => {});
        } },
      { label: "Wait" },
    ],
  });
}

// True means OK to proceed; false means the stop-or-wait modal is already showing
export function checkRunNotBusy() {
  if (!S.runBusy) return true;
  runBusyModal();
  return false;
}

// The Fix click as a chat message, the issue id riding along and the user's steer first
export function fixMessage(ticket, nodeName, steer = "") {
  const why = ticket?.reason ? `: ${haltReasonText(ticket.reason)}` : "";

  const ask = `Please fix the problem with "${nodeName}"${why}.`;

  return steer.trim() ? `${steer.trim()}\n\n${ask}` : ask;
}

// Switches to the Workflow tab and reopens the chat panel if it was collapsed
export function goToChatTab() {
  if (S.workflowTab !== "workflow" && S.workflow) {
    history.replaceState(null, "", `#/workflow/${S.workflow.id}/workflow`);
    setWorkflowTab("workflow");
  }
  document.getElementById("ptab-workflow")?.classList.remove("wf-collapsed");
}

// Switches to the Workflow tab without touching the collapsed panel
export function goToWorkflowTab() {
  if (S.workflowTab !== "workflow" && S.workflow) {
    history.replaceState(null, "", `#/workflow/${S.workflow.id}/workflow`);
    setWorkflowTab("workflow");
  }
}

window.addEventListener("cryogram:continue-run", (e) => {
  const { ticketId, runId } = e.detail || {};
  const t = (S.workflow?.tickets || []).find((x) => x.id === ticketId);
  if (t) { t.status = "closed"; t.continued = true; }
  goToWorkflowTab();
  resumeRun(runId);
});

// Tab clicks route through the hash; a click during a workflow switch is ignored
function wireWorkflowTabs() {
  document.querySelectorAll("#workflowTabs .tab").forEach((b) => {

    b.onclick = () => {
      if (S.workflow) location.hash = `#/workflow/${S.workflow.id}/${b.dataset.ptab}`;
    };
  });

  tabMenu(document.getElementById("workflowTabs"), document.getElementById("workflowActions"));
  wirePanelToggle();
}

// The chat panel collapses as one unit; the graph re-fits when the transition actually finishes
function wirePanelToggle() {
  const layout = document.getElementById("ptab-workflow");
  const toggle = document.getElementById("wfPanelToggle");
  if (!layout || !toggle) return;
  const setCollapsed = (collapsed) => {
    layout.classList.toggle("wf-collapsed", collapsed);

    toggle.classList.toggle("btn-secondary", !collapsed);
    toggle.classList.toggle("btn-primary", collapsed);
    toggle.textContent = collapsed ? "Show chat" : "Hide chat";
    let done = false;
    const refit = () => {
      if (done) return;
      done = true;
      if (S.workflow && S.workflowTab === "workflow") {
        refitWorkflowView();
        renderWorkflow(S.workflow, selectNode);
        repaintNodeStates();
      }
    };
    layout.addEventListener("transitionend", refit, { once: true });
    setTimeout(refit, 400);
  };
  toggle.onclick = () => setCollapsed(!layout.classList.contains("wf-collapsed"));
}

// The title, inline-editable through the shared pencil
function renderWorkflowName() {
  if (!S.workflow) return;
  setTitle(S.workflow.name);
  inlineEdit(document.getElementById("workflowName"), {
    value: S.workflow.name || "", label: "the name", maxlength: 120, required: true,
    type: "title",
    save: async (v) => {
      await api.setWorkflowMeta(S.workflow.id, v, S.workflow.description || "");
      S.workflow.name = v;
    },
    repaint: renderWorkflowName,
  });
}

// The one-line description, inline-editable; auto-fills from the intent when blank and is the user's text thereafter
function renderWorkflowDesc() {
  if (!S.workflow) return;
  inlineEdit(document.getElementById("workflowDesc"), {
    value: S.workflow.description || "", label: "the description", maxlength: 240,
    type: "desc",
    emptyText: "No description yet",
    placeholder: "One line on what this workflow does",
    save: async (v) => {
      await api.setWorkflowMeta(S.workflow.id, S.workflow.name, v);
      S.workflow.description = v;
    },
    repaint: renderWorkflowDesc,
  });
}

// The header's Copy and Delete pair - Copy lands on the duplicate, Delete confirms once
function wireWorkflowActions() {
  const copy = document.getElementById("projCopy");
  const del = document.getElementById("projDelete");
  if (!copy || copy.dataset.wired) return;
  copy.dataset.wired = "1";
  copy.onclick = () => {
    if (!S.workflow) return;
    openModal({
      title: `Copy "${S.workflow.name}"`,
      body: `<p>Make a copy of this workflow? Steps, plan, settings,
          conversation and test evidence are all copied - the copy runs on
          its own from here, with a fresh run history.</p>`,
      actions: [
        { label: "Copy", kind: "btn-primary", onClick: async () => {
            const pid = S.workflow.id;
            closeModal();
            const r = await api.duplicateWorkflow(pid).catch(() => null);
            if (r?.workflow?.id) location.hash = `#/workflow/${r.workflow.id}`;
          } },
        { label: "Cancel" },
      ],
    });
  };
  del.onclick = () => {
    if (!S.workflow) return;
    openModal({
      title: `Delete "${S.workflow.name}"`,
      body: `<p>Delete this workflow? Its steps, settings, conversation and
          run history are removed from this computer. This cannot be
          undone.</p>`,
      actions: [
        { label: "Delete", kind: "btn-danger", onClick: async () => {
            const pid = S.workflow.id;
            closeModal();
            await api.deleteWorkflow(pid).catch(() => {});
            location.hash = "#/";
          } },
        { label: "Cancel" },
      ],
    });
  };
}

// Re-renders the whole workflow page for the current tab
export function renderAll() {
  renderWorkflowName();
  renderWorkflowDesc();
  wireWorkflowActions();
  wireWorkflowTabs();
  setWorkflowTab(S.workflowTab);

  const attach = document.getElementById("chatAttach");
  const fileInput = document.getElementById("chatFile");
  if (attach && fileInput) {
    attach.onclick = () => fileInput.click();
    fileInput.onchange = () => {
      [...(fileInput.files || [])].forEach((f) => stageAttachment(f));
      fileInput.value = "";
    };
  }
}

// The Purpose section: the recorded intent, read-only - corrections go through chat
function renderPurpose() {
  const host = document.getElementById("purposePane");
  const it = S.workflow.intent;
  if (!it || !it.summary) {
    host.innerHTML = `<p class="muted purpose-empty">No purpose recorded yet.
      The Builder Agent writes it from your description when you start building - it is what
      the post-build audit checks the workflow against. If it stays empty, just
      describe in chat what this workflow should do.</p>`;
    appendAssistantUsage(host);
    return;
  }
  const facts = (it.facts || []).map((f) => `<li>${esc(f)}</li>`).join("");
  const when = it.updated ? new Date(it.updated * 1000).toLocaleString() : "";
  host.innerHTML = `
    <div class="purpose-summary">${esc(it.summary)}</div>
    ${facts ? `<div class="vars-section-head">Facts</div><ul class="purpose-list">${facts}</ul>` : ""}
    <p class="hint">Kept up to date as you chat${when ? ` - updated ${esc(when)}` : ""}.
      Spotted something wrong? Say so in the chat and it will be corrected.</p>`;
  appendAssistantUsage(host);
}

// The assistant's token spend on this workflow - counts only, fetched lazily
function appendAssistantUsage(host) {
  const pid = S.workflow?.id;
  if (!pid) return;
  const line = document.createElement("p");
  line.className = "hint";
  host.appendChild(line);
  api.getWorkflowAiUsage(pid).then((r) => {
    if (S.workflow?.id !== pid) return;
    const u = r.usage || {};
    const b = u.builder || { in: 0, out: 0 };
    const t = u.build || { in: 0, out: 0 };
    if (!(b.in + b.out + t.in + t.out)) { line.remove(); return; }
    line.textContent = `Builder Agent usage so far (all time): `
      + `${humanTokens(b.in + t.in)} tokens in, `
      + `${humanTokens(b.out + t.out)} out - building, changes and step testing.`;
  }).catch(() => line.remove());
}

// The Instructions section: how to run this S.workflow, maintained by the assistant, read-only here
function renderInstructions() {
  const host = document.getElementById("instructionsPane");
  if (!host) return;
  const text = (S.workflow.intent || {}).instructions || "";
  if (!text.trim()) {
    host.innerHTML = `<p class="muted">No instructions written yet. They appear
      here when the workflow is built - how to run it, what you'll be asked
      for, and where to find each value. You can also just ask in the chat.</p>`;
    return;
  }
  host.innerHTML = `<div class="purpose-instructions">${renderMarkdown(text)}</div>`;
}

const VERSION_REASON = {
  build: "Built", "pre-restore": "Before a restore",
  restore: "Restored", "prompt-edit": "Prompt edited",
  "prompt-reset": "Prompt reset", "model-change": "Model changed",
};

// A version reason as its label, ending in the version's number; a drawer edit names its step, a restore names the version it went back to
function versionLabel(reason, number) {
  const [key, detail] = String(reason || "").split("::", 2);

  const base = VERSION_REASON[key] || (key ? key[0].toUpperCase() + key.slice(1) : "Saved");

  const named = detail && key !== "restore" ? `${base} - ${detail}` : base;
  return number ? `${named} - v${number}` : named;
}

// The Version history pane: the newest row is current, older rows offer Restore
function renderVersions() {
  const host = document.getElementById("versionsPane");
  if (!host) return;
  const pid = S.workflow?.id;
  host.innerHTML = `<p class="muted">Loading...</p>`;
  api.getWorkflowVersions(pid).then((r) => {
    if (S.workflow?.id !== pid) return;
    const versions = r.versions || [];
    if (!versions.length) {
      host.innerHTML = `<p class="muted">No earlier versions yet. One is kept
        each time the workflow is built, so you can always come
        back to how it was.</p>`;
      return;
    }
    const rows = versions.map((v, i) => {
      const when = new Date(v.ts * 1000).toLocaleString();
      const what = versionLabel(v.reason, v.number);

      const action = i === 0
        ? `<span class="tag">Current version</span>`
        : `<button type="button" class="btn btn-secondary"
             data-restore="${v.id}" data-when="${esc(when)}">Restore</button>`;
      return `<div class="dt-row">
        <div>${esc(when)}</div><div>${esc(what)}</div>
        <div class="dt-actions">${action}</div></div>`;
    }).join("");
    host.innerHTML = `<div class="dt dt-versions">
      <div class="dt-head"><div>When</div><div>Description</div><div></div></div>
      ${rows}</div>
      <p class="hint">Restoring puts the steps back to how they were then.
      Values you've set since are kept, and the chat and run history stay.</p>`;
    host.querySelectorAll("[data-restore]").forEach((btn) => {
      btn.onclick = () => confirmRestore(pid, btn.dataset.restore, btn.dataset.when);
    });
  }).catch(() => {
    if (S.workflow?.id !== pid) return;
    host.innerHTML = `<p class="muted">Couldn't load the version list - try again in a moment.</p>`;
  });
}

// The restore confirm - the current setup is saved first, so it can be undone
function confirmRestore(pid, versionId, when) {
  openModal({
    title: "Restore this version?",
    subtitle: `The workflow goes back to how it was on ${when}.`,
    body: `<p>Your current setup is saved first, so you can undo this.
      Values you've set are kept, and the chat and run history stay as they
      are.</p>`,
    actions: [
      { label: "Restore", kind: "btn-primary", onClick: async (root) => {
          const b = root.querySelector(".btn-primary");
          b.disabled = true;
          try {
            await api.restoreWorkflowVersion(pid, versionId);
            closeModal();
            if (S.workflow?.id === pid) loadWorkflow(pid, "purpose");
          } catch (e) {
            b.disabled = false;
            let msg = root.querySelector(".form-error");
            if (!msg) {
              msg = document.createElement("p");
              msg.className = "form-error";
              root.querySelector(".modal-body").appendChild(msg);
            }
            msg.textContent = e?.status === 409
              ? "The Builder Agent is working on this workflow - wait for it to finish, or stop it first."
              : "That didn't work - try again in a moment.";
          }
        } },
      { label: "Cancel" },
    ],
  });
}

let setupDismissedTs = 0;

// The pre-run setup banner, dismissable per notice
function renderSetupNotice(wf) {
  const el = document.getElementById("setupNotice");
  if (!el) return;
  const block = wf.setup_block;
  const problems = (block && block.problems) || [];
  if (!problems.length || setupDismissedTs === block.ts) {
    el.hidden = true;
    el.innerHTML = "";
    return;
  }
  el.hidden = false;
  el.innerHTML = `<div class="setup-notice-body">
      <div class="setup-notice-title">This workflow can't run yet</div>
      ${problems.map((p) => `<div class="setup-notice-line">${esc(p)}</div>`).join("")}
    </div>
    <div class="setup-notice-actions">
      <button class="btn btn-secondary btn-sm" id="setupRetry">Retry</button>
      <button class="setup-notice-x" id="setupDismiss" title="Dismiss" aria-label="Dismiss">&times;</button>
    </div>`;

  el.querySelector("#setupRetry").onclick = () => document.getElementById("runWorkflow")?.click();
  el.querySelector("#setupDismiss").onclick = () => { setupDismissedTs = block.ts; el.hidden = true; };
}

let resultsPage = 1;

// The Run history pane, one page at a time
function renderResults() {
  const el = document.getElementById("resultsPane");
  if (!el) return;
  const pid = S.workflow.id;
  api.getWorkflowRuns(pid, resultsPage).then((data) => {
    if (S.workflow.id !== pid || S.workflowTab !== "results") return;
    const runs = data.runs || [];
    resultsPage = data.page || 1;
    const rows = runs.map(runRow).join("");
    const pager = (data.pages || 1) > 1 ? `<div class="dt-pager">
        <button class="btn btn-secondary btn-sm" id="runsNewer"
          ${resultsPage <= 1 ? "disabled" : ""}>Newer</button>
        <span class="muted">Page ${resultsPage} of ${data.pages} - ${data.total} runs</span>
        <button class="btn btn-secondary btn-sm" id="runsOlder"
          ${resultsPage >= data.pages ? "disabled" : ""}>Older</button>
      </div>` : "";
    el.innerHTML = `<div class="dt dt-results">
        <div class="dt-head"><div>When</div><div>Status</div><div>Summary</div></div>
        ${rows || `<div class="dt-empty muted">No runs yet. Each time the
          workflow runs, what it produced shows up here.</div>`}
      </div>${pager}`;
    el.querySelector("#runsNewer")?.addEventListener("click", () => {
      resultsPage = Math.max(1, resultsPage - 1); renderResults();
    });
    el.querySelector("#runsOlder")?.addEventListener("click", () => {
      resultsPage = resultsPage + 1; renderResults();
    });
    el.querySelectorAll(".dt-row[data-run]").forEach((row) => {
      const r = runs.find((x) => x.run_id === row.dataset.run);

      const open = () => showRun(r);
      row.onclick = open;
      row.onkeydown = (e) => { if (e.key === "Enter") open(); };
    });
  }).catch(() => {
    el.innerHTML = `<p class="muted vars-empty">Could not load the run history.</p>`;
  });
  el.innerHTML = `<p class="muted vars-empty">Loading...</p>`;
}

// One run row: when, the outcome and what was kept or why it stopped
function runRow(r) {

  const half = r.partial && typeof r.partial === "object" ? r.partial : null;
  const ts = half && r.started ? r.started : r.ts;
  const when = ts ? new Date(ts * 1000).toLocaleString() : "";
  const ok = r.status === "completed";
  const asideN = (r.set_aside || []).flatMap((sa) => sa.ports || [])
    .reduce((n, o) => n + (o.total || 0), 0);
  const halfNote = half
    ? ` <span class="muted">- ${esc(String(half.count))} of ${esc(String(half.of))} items${
        half.half === "done" ? "" : " still to do"}</span>` : "";
  const summary = ok
    ? esc(resultSummary(r)) + halfNote + (asideN ? ` <span class="muted">- ${asideN} item${asideN === 1 ? "" : "s"} set aside</span>` : "")
    : `Stopped - ${esc(haltNote(r, S.workflow.tickets))}${halfNote}`;
  const tag = ok ? (half ? "Partially complete" : "Completed")
                 : (half ? "Partially incomplete" : "Did not finish");
  return `<div class="dt-row" data-run="${esc(r.run_id)}" tabindex="0">
    <div class="var-name">${esc(when)}</div>
    <div><span class="tag ${ok ? "run-ok" : "run-halted"}">${tag}</span></div>
    <div class="var-val">${summary}</div>
  </div>`;
}

// A run whose outcome nobody has seen opens its modal the moment the workflow is on screen
const unseenOpened = new Set();

export function openUnseenRun() {
  const u = S.workflow?._run_unseen;
  if (!u?.run_id || S.runBusy || unseenOpened.has(u.run_id)) return;
  unseenOpened.add(u.run_id);
  if (u.status === "halted" && u.waiting_on_you) {
    openHaltPopup({ run_id: u.run_id, status: "halted", halted_at: u.halted_at,
                    reason: u.reason, verdict: u.verdict || {}, waiting_on_you: true });
  } else {
    showRunById(u.run_id, { live: true, result: { ...u, status: u.status } });
  }
}

// Opens a run's modal from its just-written record, merging the live result's own detail over it
export function showRunById(runId, { live = false, result = null } = {}) {
  const pid = S.workflow?.id;
  if (!pid || !runId) return;
  api.getWorkflowRuns(pid).then((data) => {
    if (S.workflow?.id !== pid) return;
    const r = (data.runs || []).find((x) => x.run_id === runId);
    if (r || result) showRun({ ...(result || {}), ...(r || {}) }, { live });
  }).catch(() => { if (result) showRun(result, { live }); });
}

// One way to open a run's modal, from history or at the moment it stops; the live halt adds the share offer
function showRun(r, { live = false } = {}) {
  if (r?.run_id && S.workflow?.id) api.markRunSeen(S.workflow.id, r.run_id).catch(() => {});
  runModal(r, S.workflow, {
    onResume: (run) => { goToWorkflowTab(); resumeRun(run.run_id); },

    onResendAll: (run) => { goToWorkflowTab(); resendStep(run.run_id, run.halted_at); },
    onRestart: () => { goToWorkflowTab(); beginRun(); },
    onFix: (tid, opts) => fixIssue(tid, opts),
    onCheckSend: (t) => didItLandFlow(t),

    onProceed: async (run) => {
      try {
        const res = await api.proceedRun(S.workflow.id, run.run_id);
        goToWorkflowTab();
        resumeRun(res.run_id || run.run_id);
      } catch (err) {
        openModal({ title: "Could not proceed",
                    body: `<p>${esc(err.message || String(err))}</p>`,
                    actions: [{ label: "Close" }] });
      }
    },
    afterExit: live
      ? (tid) => { if (tid) setTimeout(() => offerShareIssue(S.workflow?.id, tid, { auto: true }), 350); }
      : null,
  });
}

const sharePrompted = new Set();
let pendingShare = null;

// Whether the one modal slot is free: no modal on screen and no run still going
function shareSlotFree() {
  return !modalOpen() && !S.runBusy;
}

// Opens the offer that was waiting, once the slot is genuinely free
function flushPendingShare() {
  if (!pendingShare) return;
  setTimeout(() => {
    if (!pendingShare || !shareSlotFree()) return;
    const { workflowId, ticketId } = pendingShare;
    pendingShare = null;
    offerShareIssue(workflowId, ticketId, { auto: true });
  }, 0);
}
document.addEventListener("cryogram:modal-closed", flushPendingShare);

// The share-what-happened ask; the preview is the consent, and auto mode asks once per issue
async function offerShareIssue(workflowId, ticketId, { auto = false } = {}) {
  if (!ticketId || (auto && sharePrompted.has(ticketId))) return;

  if (!shareSlotFree()) { pendingShare = { workflowId, ticketId }; return; }
  let r = null;
  try { r = await api.getSharePreview(workflowId, ticketId); } catch { return; }
  if (!r?.payload) return;
  if (auto && r.mode !== "ask") return;

  if (!shareSlotFree()) { pendingShare = { workflowId, ticketId }; return; }
  pendingShare = null;
  sharePrompted.add(ticketId);
  openModal({
    title: "Share what happened with Cryogram?",
    body: `<p>This helps us make sure it doesn't happen again. Here is
        everything that would be sent - no more, no less. Your values,
        results and conversation are never included.</p>
      <pre class="share-preview">${esc(JSON.stringify(r.payload, null, 2))}</pre>`,
    actions: [
      { label: "Yes, share it", kind: "btn-primary", onClick: async () => {
          closeModal();
          try {
            const res = await api.shareIssue(workflowId, ticketId);
            if (!res.ok) openModal({ title: "Not sent",
              body: `<p>${esc(res.error || "Try again later.")}</p>`,
              actions: [{ label: "Close", kind: "btn-primary" }] });
          } catch { }
        } },
      { label: "No", kind: "btn-secondary" },
    ],
  });
}

// The Issues tab: user actions only - open, in progress, ready, closed
function renderIssues() {
  const el = document.getElementById("issuesList");
  if (!el) return;
  const nodeName = (id) => S.workflow.nodes.find((n) => n.id === id)?.name || id || "?";

  const STATUS = { open: "Open", "in-progress": "In progress",
                   ready: "Ready", closed: "Closed", dismissed: "Dismissed" };
  const tickets = (S.workflow.tickets || []).slice().reverse();
  if (!tickets.length) {
    el.innerHTML = `<p class="muted vars-empty">Nothing needs you. If a run
      stops on a problem, it shows up here.</p>`;
    return;
  }
  const rows = tickets.map((t) => {
    const st = STATUS[t.status] ? t.status : "open";
    const when = t.ts ? new Date(t.ts * 1000).toLocaleString() : "";
    return `<div class="dt-row" data-tid="${t.id}">
      <div class="var-name">${esc(nodeName(t.node_id))}
        ${when ? `<div class="dt-note muted">${esc(when)}</div>` : ""}</div>
      <div><span class="tag ticket-${esc(st)}">${esc(STATUS[st])}</span></div>
      <div class="var-val">${esc(sentence(verdictLines(t)[0]
        || haltReasonText(t.reason)))}${t.resolution_note
          ? `<div class="dt-note muted">Closed - ${esc(t.resolution_note)}</div>` : ""}</div>
      <div class="dt-actions">
        ${st === "open"
          ? `<button class="btn btn-primary t-look">Investigate</button>` : ""}
        ${st === "ready"
          ? `${t.continued ? "" :
               `<button class="btn btn-primary t-cont">Continue</button>`}
             <button class="btn ${t.continued ? "btn-primary" : "btn-secondary"}
               t-restart">Start from beginning</button>` : ""}
        ${st === "open" || st === "in-progress" || st === "ready"
          ? `<button class="btn btn-secondary t-dis">Dismiss</button>` : ""}
        ${st === "dismissed"
          ? `<button class="btn btn-secondary t-reopen">Reopen</button>` : ""}
      </div>
    </div>`;
  }).join("");
  el.innerHTML = `<div class="dt dt-issues">
      <div class="dt-head"><div>Step</div><div>Status</div><div>What happened</div><div>Action</div></div>
      ${rows}
    </div>`;
  el.querySelectorAll(".dt-row[data-tid]").forEach((row) => {
    const tid = row.dataset.tid;
    const t = (S.workflow.tickets || []).find((x) => x.id === tid);
    const setStatus = async (patch) => {
      await api.setTicketStatus(S.workflow.id, tid, patch).catch(() => {});
      if (t && typeof patch === "string") t.status = patch;
      else if (t && patch.status) t.status = patch.status;
      renderIssues();
    };
    const look = row.querySelector(".t-look");
    if (look) look.onclick = () => investigateIssue(t);
    const dis = row.querySelector(".t-dis");
    if (dis) dis.onclick = () => setStatus("dismissed");
    const reopen = row.querySelector(".t-reopen");
    if (reopen) reopen.onclick = () => setStatus("open");

    const cont = row.querySelector(".t-cont");
    if (cont) cont.onclick = async () => {
      await setStatus({ status: "closed", continued: true });
      goToWorkflowTab();
      resumeRun(t.run_id);
    };
    const restart = row.querySelector(".t-restart");
    if (restart) restart.onclick = async () => {
      await setStatus("closed");
      goToWorkflowTab();
      beginRunWith(t.entry_inputs || {});
    };
  });
}

// Investigate forks on what happened: an unconfirmed send opens the did-it-land flow, everything else gets the explanation and one fix button
function investigateIssue(t) {
  if (!t) return;

  if ((t.reason === "send-unverified" || t.fired)
      && !t.user_outputs && !t.continued && t.landed !== false) {
    didItLandFlow(t);
    return;
  }
  const nodeName = S.workflow.nodes.find((n) => n.id === t.node_id)?.name
    || t.node_id || "this step";
  const why = verdictLines(t);
  const uo = t.user_outputs;
  openModal({
    title: `The problem at "${nodeName}"`,
    body: why.map((w) => `<p class="setup-line">${esc(w)}</p>`).join("")
      + offendingHtml(t)
      + sawHtml(t)
      + (uo ? `<p class="hint">You checked the outside system for this one:
          ${uo.given?.length ? `recorded ${esc(uo.given.join(", "))}` : ""}
          ${uo.unsure?.length ? `; couldn't see ${esc(uo.unsure.join(", "))}` : ""}.</p>` : "")
      + (t.reason === "items-set-aside"
          ? `<p class="run-note">These rows were set aside and the rest went through.
              I can look at them: they may be bad data to ignore, or a shape this
              step should accept from now on.</p>`
          : `<p class="run-note">I can look into this and build a fix so it
              doesn't happen again.</p>`),
    actions: [
      { label: "Ask the Builder Agent to fix", kind: "btn-primary",
        onClick: () => { closeModal(); fixIssue(t.id); } },
      { label: "Close" },
    ],
  });
}

// One fix path: flip the issue to in progress, then say it in chat with the id attached
export async function fixIssue(ticketId, opts = {}) {

  const then = typeof opts.then === "function" ? opts.then : () => {};
  const t = (S.workflow?.tickets || []).find((x) => x.id === ticketId);
  if (!t) { then(); return; }
  const nodeName = S.workflow.nodes.find((n) => n.id === t.node_id)?.name
    || t.node_id || "this step";

  const first = verdictLines(t)[0] || haltReasonText(t.reason) || "";
  openModal({
    title: `Ask the Builder Agent to fix "${nodeName}"`,
    body: `<p class="setup-line">${esc(sentence(first))}</p>
      <div class="form-group">
        <label class="form-label" for="fixSteer">Anything I should know?
          <span class="label-optional">optional</span></label>
        <textarea id="fixSteer" class="var-input" rows="3"
          placeholder="A preference, what you already tried, what not to do - or leave empty"></textarea>
      </div>`,
    actions: [
      { label: "Ask the Builder Agent to fix", kind: "btn-primary", onClick: async () => {
          const steer = document.getElementById("fixSteer")?.value || "";
          closeModal();
          await api.setTicketStatus(S.workflow.id, ticketId, "in-progress").catch(() => {});
          t.status = "in-progress";
          goToChatTab();
          say(fixMessage(t, nodeName, steer), "typed", { issue: t.id });
          then();
        } },
      { label: "Cancel", onClick: () => { closeModal(); then(); } },
    ],
  });
  setTimeout(() => document.getElementById("fixSteer")?.focus(), 30);
}

// Saves an edited prompt (or resets it to the agent's version) and repaints the drawer
async function onEditPrompt(nodeId, body) {
  if (S.runBusy) { runBusyModal(); return; }
  try {
    await api.setNodePrompt(S.workflow.id, nodeId, body);
  } catch (err) {
    openModal({
      title: "Couldn't save the prompt",
      body: `<p>${esc(String(err.message || err))}</p>`,
      actions: [{ label: "Close", kind: "btn-primary" }],
    });
    return;
  }
  S.workflow = await api.getWorkflow(S.workflow.id);
  renderAll();
  const node = (S.workflow.nodes || []).find((n) => n.id === nodeId);
  if (node) selectNode(node);
}

// Applies the model the user picked for an AI step and repaints the drawer
async function onChangeModel(nodeId, model, providerId = "") {
  return applyModelSettings(nodeId, { model, provider_id: providerId }, "Couldn't change the model");
}

// Temperature or token limit from the step drawer: the same route as the model, one key at a time
async function onChangeAnswer(nodeId, patch) {
  return applyModelSettings(nodeId, patch, "Couldn't change the setting");
}

// Sends one change to an AI step's model settings and redraws the step from the stored workflow
async function applyModelSettings(nodeId, patch, failTitle) {
  if (S.runBusy) { runBusyModal(); return; }
  try {
    await api.setNodeModelSettings(S.workflow.id, nodeId, patch);
  } catch (err) {
    openModal({
      title: failTitle,
      body: `<p>${esc(String(err.message || err))}</p>`,
      actions: [{ label: "Close", kind: "btn-primary" }],
    });
    return;
  }
  S.workflow = await api.getWorkflow(S.workflow.id);
  renderAll();
  const node = (S.workflow.nodes || []).find((n) => n.id === nodeId);
  if (node) selectNode(node);
}

// The Workflows nav dot: lights when any workflow other than the open one has something new
export async function refreshAttention() {
  try {
    const cards = await api.getWorkflows();

    const here = S.workflow?.id;
    document.getElementById("navWorkflowsAlert")?.classList
      .toggle("hidden", !cards.some((c) => c.needs_attention && c.id !== here));
  } catch { }
}

// Stamps the workflow seen so the alert dot clears
export function markSeen(pid) {
  api.markWorkflowSeen(pid).then(refreshAttention).catch(() => {});
}

// Loads and shows one S.workflow; everything the previous one owned is cleared before the first await
export async function loadWorkflow(id, tab = S.workflowTab) {
  markSeen(id);

  S.viewToken++;
  S.workflow = null;
  S.workflowEnvs = [];
  resultsPage = 1;
  S.turnPollFor = null;
  setChatBusy(false);
  setRunBusy(false);
  S.buildStateRef = null;
  S.cellStatesRef = null;
  S.focusStepRef = null;
  clearBuildToast();
  clearRunStates();
  closeNodeDetail();
  resetChatView();
  setChatWorkflow(id, S.viewToken);
  setWorkflowTab(tab);
  try {

    const [wf, appSettings] = await Promise.all([
      api.getWorkflow(id), api.getSettings().catch(() => S.appSettings)]);
    S.workflow = wf;
    if (appSettings) S.appSettings = appSettings;
  } catch {
    location.hash = "#/";
    return;
  }
  S.workflowEnvs = (await Promise.all(envIdsOf(S.workflow).map(
    (eid) => api.getEnvironment(eid).catch(() => null)))).filter(Boolean);
  renderAll();
  renderEnvControl();

  const tokHere = S.viewToken;
  if (!(await attachToTurn()) && tokHere === S.viewToken) openUnseenRun();

  if (S.pendingFirstMessage) {
    const pend = S.pendingFirstMessage;
    S.pendingFirstMessage = null;
    if (pend.id === id && pend.text) {

      const input = document.getElementById("chatInput");
      const form = document.getElementById("chatForm");
      if (input && form) { input.value = pend.text; form.requestSubmit(); }
    }
  }
}

function envIdsOf(p) {
  return Array.isArray(p?.environment_ids)
    ? p.environment_ids.filter(Boolean) : [];
}

let envCatalog = [];

let envTicked = new Set();

// The environment picker's grouped list, filtered by the search
function envGroupSections(envs, query) {
  const q = query.trim().toLowerCase();
  const matches = envs.filter((e) => !q || (e.name || "").toLowerCase().includes(q));
  if (!matches.length) return `<div class="env-menu-row muted">No match</div>`;
  const groups = new Map();
  matches.forEach((e) => {
    const g = (e.group || "").trim();
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(e);
  });
  const named = [...groups.keys()].filter(Boolean).sort((a, b) => a.localeCompare(b));
  const order = groups.has("") ? [...named, ""] : named;
  return order.map((g) => `
    <details class="env-menu-group" open>
      <summary>${g ? esc(g) : "Ungrouped"}</summary>
      ${groups.get(g).map((e) => `<label class="env-menu-row">
        <input type="checkbox" value="${esc(e.id)}" ${envTicked.has(e.id) ? "checked" : ""}>
        <span>${esc(e.name)}</span></label>`).join("")}
    </details>`).join("");
}

function paintEnvMenuList() {
  const list = document.getElementById("envMenuList");
  if (!list) return;
  const search = document.getElementById("envMenuSearch");
  list.innerHTML = envGroupSections(envCatalog, search?.value || "");
}

let envControlWired = false;

// Wires the environment menu once, by delegation
function wireEnvControl(host) {
  if (envControlWired) return;
  envControlWired = true;
  const menu = () => document.getElementById("envMenu");
  const hide = () => menu()?.classList.add("hidden");
  const openMenu = () => {
    envTicked = new Set(envIdsOf(S.workflow));
    const search = document.getElementById("envMenuSearch");
    if (search) search.value = "";
    paintEnvMenuList();
    menu()?.classList.remove("hidden");
    search?.focus();
  };
  host.addEventListener("click", (e) => {

    const cb = e.target.closest('input[type="checkbox"]');
    if (cb) {
      if (cb.checked) envTicked.add(cb.value); else envTicked.delete(cb.value);
      return;
    }
    const id = e.target.closest("button")?.id;
    if (!id) return;
    e.preventDefault();
    if (id === "envMenuBtn") {
      const m = menu();
      if (!m) return;
      if (m.classList.contains("hidden")) openMenu();
      else hide();
    } else if (id === "envMenuCancel") {
      hide();
    } else if (id === "envMenuConfirm") {
      const chosen = [...envTicked];
      hide();
      applyEnvSelection(chosen, envCatalog);
    }
  });
  host.addEventListener("input", (e) => {
    if (e.target.id === "envMenuSearch") paintEnvMenuList();
  });
  document.addEventListener("click", (e) => {
    const m = menu();
    if (m && !m.classList.contains("hidden") && !host.contains(e.target)) hide();
  });
}

// The header's environment chips and the Change menu
export async function renderEnvControl() {
  const host = document.getElementById("workflowEnv");
  if (!host) return;
  wireEnvControl(host);
  const envs = await api.getEnvironments().catch(() => []);
  envCatalog = envs;
  const current = envIdsOf(S.workflow);
  const chips = current.map((eid) =>
    envChip(envs.find((e) => e.id === eid)?.name, eid)).join("");

  host.innerHTML = `
    <span class="env-pick"><span>Environments</span>
      ${chips || envChipNone()}
      ${envs.length ? `<button id="envMenuBtn" class="btn-text" type="button">Change</button>` : ""}
    </span>
    <div id="envMenu" class="env-menu hidden">
      ${envs.length ? `<input id="envMenuSearch" type="text" class="env-menu-search"
          placeholder="Search environments..." autocomplete="off" spellcheck="false">` : ""}
      <div id="envMenuList" class="env-menu-list"></div>
      <div class="env-menu-foot">
        <button id="envMenuCancel" class="btn btn-secondary btn-sm" type="button">Cancel</button>
        <button id="envMenuConfirm" class="btn btn-primary btn-sm" type="button">Confirm</button>
      </div>
    </div>`;
}

// Saves the selection and refetches the workflow for the derived in-use table
async function saveEnvIds(ids) {
  await api.setWorkflowEnvironments(S.workflow.id, ids);
  S.workflow.environment_ids = ids;
  S.workflowEnvs = (await Promise.all(ids.map(
    (eid) => api.getEnvironment(eid).catch(() => null)))).filter(Boolean);

  const fresh = await api.getWorkflow(S.workflow.id).catch(() => null);
  if (fresh && S.workflow?.id === fresh.id) S.workflow = fresh;
  renderAll();
  renderEnvControl();
}

// One confirm for the whole selection; attaching to a workflow with steps also fires the alignment turn
async function applyEnvSelection(chosen, envs) {
  const cur = envIdsOf(S.workflow);
  const next = [...cur.filter((id) => chosen.includes(id)),
                ...chosen.filter((id) => !cur.includes(id))];
  const added = next.filter((id) => !cur.includes(id));
  const removed = cur.filter((id) => !chosen.includes(id));
  if (!added.length && !removed.length) return;
  const name = (id) => envs.find((e) => e.id === id)?.name || "environment";
  const addedEnvs = added.map((id) => envs.find((e) => e.id === id)).filter(Boolean);
  const list = (ids) => ids.map((id) => `<strong>${esc(name(id))}</strong>`).join(", ");
  openModal({
    title: "Update environments?",
    body: `${added.length ? `<p>Attach ${list(added)} - the variables become
        available here (read-only, edited on the environment page) and their
        values are used at run time.</p>` : ""}
      ${removed.length ? `<p>Detach ${list(removed)} - those shared variables will
        no longer be available to this workflow. The workflow's own variables
        are untouched.</p>` : ""}
      ${added.length && S.workflow.nodes.length ? `<p>On confirm, the Builder Agent
        checks that the workflow actually uses these variables - where names
        correspond it aligns them, and it asks you about anything
        ambiguous.</p>` : ""}`,
    actions: [
      { label: "Confirm", kind: "btn-primary", onClick: async () => {
          closeModal();
          await saveEnvIds(next);
          if (addedEnvs.length && S.workflow.nodes.length) askAiToAlign(addedEnvs);
        } },
      { label: "Cancel", onClick: () => closeModal() },
    ],
  });
}

// Sends the alignment ask through the normal chat pipeline, so the user watches it live
function askAiToAlign(envs) {
  const input = document.getElementById("chatInput");
  const form = document.getElementById("chatForm");
  if (!input || !form) return;
  const list = Array.isArray(envs) ? envs : [envs];
  const parts = list.map((env) => {
    const vars = (env?.variables || [])
      .map((v) => `${v.name} (${v.kind}${v.secret ? ", secret" : ""})`).join(", ");
    return `"${env.name}" (variables: ${vars || "none yet"})`;
  });
  const plural = list.length > 1;
  input.value =
    `I attached the environment${plural ? "s" : ""} ${parts.join(" and ")} to ` +
    `this workflow. Check the workflow's variables and user-input ports and, ` +
    `where one clearly corresponds to an environment variable, rename it to ` +
    `match exactly so the environment's value is used (update declared I/O and ` +
    `any code that reads it). Ask me about ambiguous matches, and leave ` +
    `genuinely workflow-specific values alone.`;
  form.requestSubmit();
}
