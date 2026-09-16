// The live turn: following a builder turn or a run by polling, painting its events on the canvas, the dock and the chat
import * as api from "./api.js";
import { escapeHtml as esc, sentence } from "./util.js";
import { renderWorkflow } from "./workflow.js";
import { renderChat, renderStream, appendShownItem, startBuildBubble, dropThinkingBubble, failStream, say, resetChatView, scrollChat, ensureTimeline, isStopping, clearStopping, setOpenAsk } from "./chat.js";
import { notify, requestNotifyPermission } from "./notify.js";
import { renderVariables } from "./variables.js";
import { applyRunStates, clearRunStates, setRunning, markFromEvent, handleRunOutcome, userRequestPopup } from "./run.js";
import { S, owns } from "./workspaceState.js";
import { fixMessage, goToChatTab, goToWorkflowTab, loadWorkflow, markSeen, openUnseenRun, renderAll, runBusyModal, selectNode, setChatBusy, setRunBusy } from "./workspace.js";

const NOTIFY_EXCERPT_CHARS = 140;

// One agent turn on an issue; while a turn is already running it degrades to a plain chat message
export async function onInvestigate(ticketId) {
  if (S.runBusy) { runBusyModal(); return; }
  requestNotifyPermission();
  if (S.chatBusy) {

    const t = (S.workflow.tickets || []).find((x) => x.id === ticketId);
    const nodeName = (id) => S.workflow.nodes.find((n) => n.id === id)?.name || id || "?";
    if (t) say(fixMessage(t, nodeName(t.node_id)), "typed", { issue: t.id });
    return;
  }
  goToChatTab();
  const pid = S.workflow.id;
  const tok = S.viewToken;
  try {
    await api.startInvestigate(pid, ticketId);
  } catch (err) {
    if (tok === S.viewToken) failStream("", String(err.message || err));
    return;
  }
  if (tok !== S.viewToken) return;
  if (!(await attachToTurn())) await catchUpFinishedTurn(pid, tok);
}

// Switches the view to a build already running inside the chat turn
function adoptBuildDisplay(s) {
  if (!owns(s) || S.buildStateRef === s) return;
  goToWorkflowTab();
  s.touchedNodes = s.touchedNodes || new Set();
  S.buildStateRef = s;
  buildToast(s, "Building your workflow - this can take a few minutes; "
              + "you can leave this page and come back.");
}

// Refetches and re-renders the graph after tool events, debounced so a burst costs one fetch
function makeGraphRefresher(id) {
  let pending = false;
  return async () => {
    if (pending) return;
    pending = true;
    setTimeout(async () => {
      pending = false;
      try {
        const snap = await api.getWorkflow(id);
        if (!S.workflow || S.workflow.id !== snap.id) return;

        S.workflow = { ...snap, transcript: S.workflow.transcript,
                    transcript_seq: S.workflow.transcript_seq };
        if (S.workflowTab === "workflow") {
          renderWorkflow(S.workflow, selectNode);
          repaintNodeStates();
        }
        renderVariables(S.workflow, S.workflowEnvs, S.workflow._variable_resolution);
      } catch {

      }
    }, 250);
  };
}

// One turn's display state, stamped with its owner token and workflow
function makeTurnState() {
  return { acc: "", builtNodes: new Set(),
           cellStates: new Map(), curText: "", curSince: 0,

           token: S.viewToken, pid: S.workflow.id, pname: S.workflow.name,
           refreshGraph: makeGraphRefresher(S.workflow.id) };
}

// The canvas id of a planned step, by the step's own id (a rename keeps its box)
function plannedBoxId(name) {
  const pn = (S.workflow?.plan?.nodes || []).find((n) => n.name === name || n.id === name);
  return "planned:" + (pn ? (pn.id || pn.name) : name);
}

// Paints working and failed marks on the same-named canvas steps
function applyCellStates(states) {
  if (!states && !S.focusStepRef) return;
  const byName = new Map((S.workflow?.nodes || []).map((n) => [n.name, n.id]));
  const focusId = S.focusStepRef
    ? (byName.get(S.focusStepRef) || plannedBoxId(S.focusStepRef)) : null;
  document.querySelectorAll("#workflow .node").forEach((box) => {
    box.classList.remove("cell-working", "cell-failed");
    for (const [name, st] of states || []) {
      const id = byName.get(name) || plannedBoxId(name);
      if (box.dataset.id === id) {

        box.classList.add(st === "working" ? "cell-working"
          : st === "ok" ? "cell-coded" : "cell-failed");
        if (st !== "ok") box.classList.remove("cell-coded");
      }
    }
    if (focusId && box.dataset.id === focusId)
      box.classList.add("cell-working");
  });
}

let toastTimer = null;

const THINKING_LINE = "Thinking…";

const THINKING_ROW_MIN_MS = 1000;

function fmtDur(secs) {
  return secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s`;
}

// Closes the trail's open entry when a wait begins, so thinking time never counts as work
function closeTrailTail(s, atSec) {
  const tail = s?.trail?.[s.trail.length - 1];
  if (tail && !tail.end) tail.end = atSec ? atSec * 1000 : Date.now();
}

// One activity stream feeds the canvas pill and the dock's scrolling trail
function buildToast(s, text, sinceSec) {

  const setDock = (rowsHtml, time) => {
    const dock = document.getElementById("chatStatus");
    const dt = document.getElementById("chatStatusText");
    if (dock) {
      dock.hidden = rowsHtml === null;
      dock.classList.toggle("status-waiting", rowsHtml !== null && !!s?.waiting);
    }
    if (dt && rowsHtml !== null) {

      const shape = rowsHtml.replace(/<span class="status-clock">[^<]*<\/span>/, "");
      if (dt._shape === shape) {
        const clock = dt.querySelector(".status-line:last-child .status-clock");
        if (clock && time !== undefined) clock.textContent = time;
        return;
      }
      dt._shape = shape;

      const rows = (rowsHtml.match(/<div/g) || []).length;
      const grew = rows > Number(dt.dataset.rows || 0);
      const stick = dt.scrollHeight - dt.scrollTop - dt.clientHeight < 12;
      dt.innerHTML = rowsHtml;
      dt.dataset.rows = String(rows);
      if (grew || stick) dt.scrollTop = dt.scrollHeight;
    }
  };
  if (text === null) {
    clearInterval(toastTimer);
    toastTimer = null;
    const p = document.getElementById("wfWorkingText");
    if (p) p.textContent = "Working on your workflow…";
    setDock(null);
    return;
  }
  if (text !== s.curText) {
    s.curText = text;

    s.curSince = sinceSec ? sinceSec * 1000 : Date.now();
    s.trail = s.trail || [];
    const tail = s.trail[s.trail.length - 1];
    if (tail && !tail.end) tail.end = s.curSince;
    if (tail && tail.text === THINKING_LINE && tail.end - tail.start < THINKING_ROW_MIN_MS) s.trail.pop();
    s.trail.push({ text, start: s.curSince });
    if (s.trail.length > 50) s.trail.shift();
  }

  const settledRows = () => (s.trail || []).slice(0, -1).reduce((rows, t) => {
    const ms = Math.max(0, (t.end ?? t.start) - t.start);
    const last = rows[rows.length - 1];
    if (last && last.text === t.text) last.ms += ms;
    else rows.push({ text: t.text, ms });
    return rows;
  }, []).map((r) => `<div class="status-line done" title="${esc(sentence(r.text))}"><span class="status-text">${
    esc(sentence(r.text))}</span><span class="status-time">${
    esc(fmtDur(Math.floor(r.ms / 1000)))}</span></div>`).join("");
  const paint = () => {
    const pill = (w) => {
      const p = document.getElementById("wfWorkingText");
      if (p) p.textContent = w;
    };

    if (isStopping()) {
      pill("Stopping - wrapping up");
      setDock(settledRows()
        + `<div class="status-line">Stopping - wrapping up</div>`);
      return;
    }

    if (s.waiting) {
      pill("Waiting for you…");
      setDock(settledRows()
        + `<div class="status-line">Waiting for you…</div>`);
      return;
    }
    const secs = Math.max(0, Math.floor((Date.now() - s.curSince) / 1000));
    const suffix = secs >= 5 ? ` - ${fmtDur(secs)}` : "";
    pill(sentence(s.curText) + suffix);

    const clock = fmtDur(secs);
    setDock(settledRows() + `<div class="status-line" title="${esc(sentence(s.curText))}"><span class="status-text">${
      esc(sentence(s.curText))}</span><span class="status-clock">${esc(clock)}</span></div>`, clock);
  };
  paint();
  if (!toastTimer) toastTimer = setInterval(paint, 1000);
}

// Silences the dock and pill and stops the repaint timer
export function clearBuildToast() { buildToast(null, null); }

// One helper repaints every canvas layer - run, build and cell marks - so a re-render cannot drop one
export function repaintNodeStates() {
  applyRunStates();
  applyBuildStates(S.buildStateRef);
  applyCellStates(S.cellStatesRef);
}

// Paints built marks and the pulsing current step, planned or real
function applyBuildStates(s) {
  if (!s) return;
  const byName = new Map((S.workflow?.nodes || []).map((n) => [n.name, n.id]));
  const built = new Set([...s.builtNodes].map((n) => byName.get(n)).filter(Boolean));

  const current = s.currentStep
    ? (byName.get(s.currentStep) || plannedBoxId(s.currentStep)) : null;
  const touched = new Set([...(s.touchedNodes || [])]
    .map((n) => byName.get(n)).filter(Boolean));
  document.querySelectorAll("#workflow .node").forEach((box) => {
    box.classList.remove("node-building", "node-built", "node-touched");
    const id = box.dataset.id;
    if (built.has(id)) box.classList.add("node-built");
    else if (id === current) box.classList.add("node-building");
    else if (touched.has(id)) box.classList.add("node-touched");
  });
}

// A short plain-text excerpt for a desktop notification body
function notifyExcerpt(text) {
  if (!text) return "";
  const line = String(text).replace(/[#*`>_[\]]/g, "").split("\n")
    .map((l) => l.trim()).find(Boolean) || "";
  return line.length > NOTIFY_EXCERPT_CHARS ? line.slice(0, NOTIFY_EXCERPT_CHARS - 1) + "…" : line;
}

// Routes one turn event: text deltas, tool lines, cards, node marks and notifications
function handleTurnEvent(ev, s, { liveCards = true, quiet = false, replay = false } = {}) {

  if (ev.type === "turn") {

    if (owns(s) && ev.turn_id && ev.turn_id !== S.currentTurnId) clearStopping();
    if (owns(s)) S.currentTurnId = ev.turn_id || "";
    return;
  }

  const reqItem = ev.type === "shown" && (ev.item || {}).kind === "request"
    ? ev.item : null;
  if (reqItem) {

    s.notifiedInput = true;
    if (liveCards && !replay) {
      const p = reqItem.payload || {};
      const what = reqItem.request === "approval" ? "approval" : "input";
      const body = reqItem.request === "approval"
        ? (p.title || "The Builder Agent wants to do something for you.")
        : reqItem.request === "blueprint"
          ? (notifyExcerpt(p.summary) || "Your workflow is ready to build.")
          : (notifyExcerpt(p.question) || "The Builder Agent has a question for you.");
      notify(`${s.pname || "Cryogram"} - your ${what} is needed`, body,
             { workflowId: s.pid, tag: "builder-input" });
    }
  }

  if (liveCards && !replay && ev.type === "done" && !ev.fix
      && !ev.stopped && !ev.asked && !s.notifiedInput) {
    notify(`${s.pname || "Cryogram"} - finished`,
           "The Builder Agent is done - come and see where it landed.",
           { workflowId: s.pid, tag: "builder-done" });
  }

  if (!owns(s)) return;

  const waitItem = reqItem
    && !(reqItem.request === "blueprint"
         && (reqItem.payload || {}).head === "built");
  if (waitItem) {

    s.waiting = true;
    closeTrailTail(s, ev.ts);
    s.curText = s.curText || "";
    buildToast(s, s.curText, ev.ts);
  } else if (ev.type === "shown" && (ev.item || {}).kind === "answer"
             && s.waiting) {
    s.waiting = false;
    s.curText = s.curText || "";
    buildToast(s, s.curText, ev.ts);
  }

  if (ev.type === "build-start") { adoptBuildDisplay(s); return; }
  if (ev.type === "phase") {

    if (s !== S.buildStateRef) return;
    const step = ev.node || (ev.text.match(/^(?:Finishing|Building) "(.+)"$/) || [])[1] || null;
    if (step) {
      s.touchedNodes = s.touchedNodes || new Set();
      s.touchedNodes.add(step);
      s.currentStep = step;
      applyBuildStates(s);
    }
    buildToast(s, ev.text, ev.ts);
    return;
  }
  if (ev.type === "node-built") {
    (ev.nodes || []).forEach((n) => s.builtNodes?.add(n));
    if (s === S.buildStateRef) applyBuildStates(s);
    return;
  }
  if (ev.type === "validation") {

    if (s === S.buildStateRef)
      buildToast(s, ev.status === "pass"
        ? "All checks passed"
        : `Found ${(ev.findings || []).length || "some"} things to fix`, ev.ts);
    return;
  }
  if (ev.type === "cell") {
    s.cellStates?.set(ev.name, ev.status);
    S.cellStatesRef = s.cellStates;
    applyCellStates(S.cellStatesRef);
    return;
  }
  if (ev.type === "done") {
    S.cellStatesRef = null;
    S.focusStepRef = null;
  }

  if (ev.type === "progress") {
    if (owns(s) && ev.text && s.trail?.length) {
      const tail = s.trail[s.trail.length - 1];
      if (!tail.end) {
        tail.text = ev.text;
        s.curText = ev.text;
      }
    }
    return;
  }

  if (ev.type === "tool" && ev.node) {
    S.focusStepRef = ev.node;
    applyCellStates(S.cellStatesRef);
  }

  if (ev.type === "thinking") {
    if (!quiet || s === S.buildStateRef) buildToast(s, THINKING_LINE, ev.ts);
    return;
  }
  if (quiet && (ev.type === "delta" || ev.type === "tool" || ev.type === "regression")) {
    if (ev.type === "delta") s.acc += ev.text;
    if (ev.type === "tool") {

      if (s === S.buildStateRef && ev.text) buildToast(s, ev.text, ev.ts);
      s.refreshGraph();
    }
    return;
  }
  if (ev.type === "delta") {
    s.acc += ev.text;
    renderStream(s.acc);
  } else if (ev.type === "tool") {

    if (ev.text) buildToast(s, ev.text, ev.ts);
    s.refreshGraph();
  } else if (ev.type === "shown" && liveCards) {

    if (ev.item && (ev.item.kind === "request"
        || (ev.item.kind === "message" && ev.item.from === "assistant")))
      s.acc = "";
    appendShownItem(ev.item);
  } else if (ev.type === "approval-done" && liveCards) {

    if (ev.decision !== "parked") startBuildBubble();
  } else if (ev.type === "done") {

    if (!ev.stopped) {
      if (ev.fix) s.fix = ev.fix;
    }

    if (s.pid) markSeen(s.pid);
  }

}

// The composer's answer mode follows the poll's pending list - the cards themselves come from the transcript
function renderPendingCards(pending) {
  setOpenAsk((pending || []).some((p) => p.kind === "ask"));
}

const TURN_POLL_MS = 1500;

const TURN_POLL_RETRY_MS = 3000;

// Follows a running turn by polling - the same path a reloaded page uses to catch up
export async function attachToTurn() {
  const pid = S.workflow.id;
  let snap;
  try {
    snap = await api.getTurn(pid);
  } catch {
    return false;
  }
  if (!snap.active) return false;

  if (snap.kind === "run") {
    setRunBusy(true);
    setRunning(true);
    clearRunStates();
    (snap.events || []).forEach(markFromEvent);
    applyRunStates();

    const rs = makeTurnState();
    const pollTok = {};
    S.turnPollFor = pollTok;
    let after = snap.last_seq || 0;
    let turnId = snap.turn_id || "";
    let lastDone = null;
    const rtick = async () => {
      if (!owns(rs) || S.turnPollFor !== pollTok) return;
      let s2;
      try { s2 = await api.getTurn(pid, after); }
      catch { setTimeout(rtick, TURN_POLL_RETRY_MS); return; }
      if (!owns(rs) || S.turnPollFor !== pollTok) return;
      if (s2.turn_id && turnId && s2.turn_id !== turnId) {

        S.turnPollFor = null;
        attachToTurn();
        return;
      }
      (s2.events || []).forEach((ev) => {
        markFromEvent(ev);
        if (ev.type === "done") lastDone = ev;
      });
      applyRunStates();
      after = s2.last_seq ?? after;
      if (s2.active) { setTimeout(rtick, TURN_POLL_MS); return; }
      S.turnPollFor = null;
      S.workflow = await api.getWorkflow(pid).catch(() => S.workflow);
      if (!owns(rs)) return;
      setRunning(false);
      setRunBusy(false);
      renderAll();
      if (lastDone?.result) handleRunOutcome({ result: lastDone.result });
    };
    rtick();
    return true;
  }

  const quiet = snap.kind === "investigate";

  ensureTimeline(S.workflow);
  if (!document.getElementById("thinkingMsg")) startBuildBubble();
  setChatBusy(true);
  const s = makeTurnState();
  if (quiet) {
    s.touchedNodes = new Set();
    S.buildStateRef = s;
  }
  s.acc = snap.text || "";

  (snap.events || []).forEach((ev) => handleTurnEvent(ev, s, { liveCards: true, replay: true, quiet }));
  renderStream(quiet ? "" : s.acc);
  renderPendingCards(snap.pending);

  (snap.pending || []).filter((p) => p.kind === "run-request")
    .forEach((p) => userRequestPopup({ iid: p.id, message: (p.payload || {}).message }));

  if ((snap.pending || []).some((p) => p.kind === "approval"))
    dropThinkingBubble();
  scrollChat(true);
  const pollTok = {};
  S.turnPollFor = pollTok;
  let after = snap.last_seq || 0;
  let turnId = snap.turn_id || "";
  S.currentTurnId = turnId;

  const tick = async () => {

    if (!owns(s) || S.turnPollFor !== pollTok) return;
    let s2;
    try {
      s2 = await api.getTurn(pid, after);
    } catch {
      setTimeout(tick, TURN_POLL_RETRY_MS);
      return;
    }
    if (!owns(s)) return;
    if (s2.turn_id && turnId && s2.turn_id !== turnId) {
      S.turnPollFor = null;
      attachToTurn();
      return;
    }
    s.acc = s2.text ?? s.acc;

    (s2.events || []).forEach((ev) => handleTurnEvent(ev, s, { liveCards: true, quiet }));
    renderStream(quiet ? "" : s.acc);
    renderPendingCards(s2.pending);
    after = s2.last_seq ?? after;
    if (s2.active) {
      setTimeout(tick, TURN_POLL_MS);
      return;
    }
    S.turnPollFor = null;
    buildToast(s, null);
    if (S.buildStateRef === s) S.buildStateRef = null;
    const fresh = await api.getWorkflow(pid).catch(() => null);
    if (!owns(s)) return;
    if (fresh) S.workflow = fresh;

    setChatBusy(false);
    renderAll();

    if (s.fix) return onInvestigate(s.fix.ticket_id);
  };
  tick();
  return true;
}

// A turn the server just started on the user's words: follow it
export async function onTurnStarted() {
  requestNotifyPermission();
  const pid = S.workflow.id;
  const tok = S.viewToken;
  if (!(await attachToTurn())) {

    await catchUpFinishedTurn(pid, tok);
  }
}

// Replays the outcome of a turn that finished before anyone attached
export async function catchUpFinishedTurn(pid, tok) {
  let snap = null;
  try { snap = await api.getTurn(pid); } catch { }
  const fresh = await api.getWorkflow(pid).catch(() => null);
  if (tok !== S.viewToken) return;
  if (fresh) S.workflow = fresh;
  renderAll();
  const done = (snap?.events || []).filter((e) => e.type === "done").pop();
  if (done?.fix) return onInvestigate(done.fix.ticket_id);
  openUnseenRun();
}
