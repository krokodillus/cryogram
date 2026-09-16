// App bootstrap and routing: hash-routed views, one shared shell
import * as api from "./api.js";
import { escapeHtml as esc, NARROW_MQ } from "./util.js";
import { renderWorkflow, refitWorkflowView } from "./workflow.js";
import { initNotify, setNotifyEnabled, setTitle } from "./notify.js";
import { closeNodeDetail } from "./nodeDetail.js";
import { initRun } from "./run.js";
import { openModal, closeModal } from "./modal.js";
import { renderDashboard, newWorkflowModal } from "./dashboard.js";
import { renderEnvironments, renderEnvDetail } from "./environments.js";
import { renderKnowledge } from "./knowledge.js";
import { renderAdminPage } from "./admin.js";
import { renderProviderDetail } from "./adminProviders.js";
import { openOnboarding, maybeAutoOpenOnboarding } from "./onboarding.js";
import { S } from "./workspaceState.js";
import { fixIssue, loadWorkflow, refreshAttention, renderAll, selectNode, setRunBusy, setWorkflowTab, showRunById, sendToBuilder as workspaceSend } from "./workspace.js";
import { attachToTurn, repaintNodeStates } from "./liveTurn.js";

// Sends a message into a workflow's chat from anywhere in the app
export function sendToBuilder(id, text) { return workspaceSend(id, text); }

// Adds the sidebar update link when a newer version exists: it opens the release on GitHub
async function checkForUpdate() {
  let u = null;
  try { u = await api.getUpdateCheck(); } catch { return; }
  if (!u || !u.available) return;
  const el = document.getElementById("appVersion");
  if (!el || document.getElementById("updateBadge")) return;

  const a = document.createElement("a");
  a.id = "updateBadge";
  a.className = "btn btn-sm btn-secondary update-badge";
  a.textContent = `Update available: v${u.latest || ""}`;
  a.href = u.page || "https://github.com/krokodillus/cryogram/releases/latest";
  a.target = "_blank";
  a.rel = "noopener";
  el.after(a);
}

const VIEWS = ["dashboard", "environments", "envDetail", "workflow", "knowledge", "admin", "adminProvider"];

const extViews = new Set();

const extRoutes = new Map();

// Shows one view and marks its nav item
function showView(name) {
  VIEWS.forEach((v) =>
    document.getElementById(`view-${v}`)?.classList.toggle("hidden", v !== name));
  const navKey = name === "environments" || name === "envDetail" ? "environments"
    : name === "knowledge" ? "knowledge"
    : name === "admin" || name === "adminProvider" ? "admin"
    : extViews.has(name) ? name : "workflows";
  document.querySelectorAll(".sidenav-items a[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === navKey));
}

// Gives one installed integration its page container, its sidebar entry and its hash route
function mountExtension(item, mod) {
  if (typeof mod.render !== "function" || extViews.has(item.id)) return;

  const host = document.createElement("section");
  host.id = `view-${item.id}`;
  host.className = "view hidden";
  document.querySelector(".main-col")?.appendChild(host);
  VIEWS.push(item.id);
  extViews.add(item.id);

  if (item.style && !document.querySelector(`link[href="${item.style}"]`)) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = item.style;
    document.head.appendChild(link);
  }

  const nav = document.querySelector(".sidenav-items");
  if (nav) {
    const a = document.createElement("a");
    a.href = item.route;
    a.dataset.nav = item.id;
    a.innerHTML = `<span class="nav-ico">${item.icon || "&#9679;"}</span> `
      + `<span class="nav-label"></span>`;
    a.querySelector(".nav-label").textContent = item.title;
    nav.appendChild(a);
  }
  extRoutes.set(item.route, { id: item.id, title: item.title, render: mod.render });
}

// Loads whatever integrations are installed; with none, nothing happens at all
async function loadExtensions() {
  let items = [];
  try { items = (await api.getExtensions()).items || []; } catch { return; }
  for (const item of items) {
    try {
      mountExtension(item, await import(item.module));
    } catch (e) {
      console.warn("could not load the", item.id, "integration", e);
    }
  }
}

// The hash router: reads the URL and paints the matching view
async function route() {
  closeNodeDetail();
  closeModal();
  let h = location.hash || "#/";

  if (h.startsWith("#/project/")) {
    h = "#/workflow/" + h.slice("#/project/".length);
    history.replaceState(null, "", h);
  }
  if (h.startsWith("#/workflow/")) {
    const [id, tab] = h.slice("#/workflow/".length).split("/");
    showView("workflow");

    if (S.workflow && S.workflow.id === id) { setWorkflowTab(tab || "workflow"); return; }
    return loadWorkflow(id, tab || "workflow");
  }
  if (h.startsWith("#/env/")) {
    showView("envDetail");
    setTitle("Environment");
    await renderEnvDetail(h.slice("#/env/".length),
      { onDeleted: () => { location.hash = "#/environments"; } });

    return setTitle(document.getElementById("envName")?.textContent || "Environment");
  }
  if (h === "#/environments") {
    showView("environments");
    setTitle("Environments");
    return renderEnvironments({ onOpen: (id) => { location.hash = `#/env/${id}`; } });
  }
  if (h === "#/knowledge") {
    showView("knowledge");
    setTitle("Knowledge");
    return renderKnowledge();
  }
  if (h.startsWith("#/admin/provider/")) {
    showView("adminProvider");
    setTitle("Admin");
    await renderProviderDetail(
      decodeURIComponent(h.slice("#/admin/provider/".length)));
    return setTitle(document.getElementById("provName")?.textContent || "Admin");
  }
  if (h === "#/admin" || h.startsWith("#/admin/")) {
    showView("admin");
    setTitle("Admin");
    return renderAdminPage(h.split("/")[2] || "builder");
  }
  for (const [prefix, ext] of extRoutes) {
    if (h === prefix || h.startsWith(`${prefix}/`)) {
      showView(ext.id);
      setTitle(ext.title);
      return ext.render({
        host: document.getElementById(`view-${ext.id}`),
        tab: h.slice(prefix.length).replace(/^\//, ""),
      });
    }
  }
  showView("dashboard");
  setTitle("Workflows");
  return renderDashboard({
    onOpen: (id) => { location.hash = `#/workflow/${id}`; },
    onNew: (workflows, envs) => newWorkflowModal(workflows, envs,
      (created, firstMessage) => {

        if (firstMessage) S.pendingFirstMessage = { id: created.id, text: firstMessage };
        location.hash = `#/workflow/${created.id}`;
      }),
  });
}

// Boot: settings, routing and notifications; a failed settings fetch shows a banner instead of killing the app
async function init() {
  initNotify();

  try {
    S.appSettings = await api.getSettings();
  } catch (e) {
    S.appSettings = {};
    document.body.insertAdjacentHTML("afterbegin",
      `<div class="chat-err app-load-err" role="alert">Couldn't load
       settings (${String(e.message || e)}) - some panels may look incomplete;
       reload once the app is reachable.</div>`);
  }
  setNotifyEnabled(S.appSettings.preferences?.notifications !== false);
  fetch("/api/version").then((r) => r.json()).then((v) => {
    const el = document.getElementById("appVersion");
    if (el && v.version) el.textContent = `v${v.version}`;
    if (v.channel && v.channel !== "dev") {
      checkForUpdate();

      document.getElementById("importWorkflow")?.remove();
    }
  }).catch(() => {});
  initRun({
    getWorkflow: () => S.workflow,
    getEnvironments: () => S.workflowEnvs,
    setWorkflow: (p) => { S.workflow = p; renderAll(); },
    fixIssue: (ticketId, opts) => fixIssue(ticketId, opts),
    isChatBusy: () => S.chatBusy,

    reattach: () => attachToTurn(),
    onRunStart: () => setRunBusy(true),
    onRunEnd: () => setRunBusy(false),

    openRun: (runId, opts) => showRunById(runId, opts),
    markRunSeen: (runId) => {
      if (S.workflow?.id && runId) api.markRunSeen(S.workflow.id, runId).catch(() => {});
    },
  });
  document.getElementById("onboardingOpen").onclick = () => openOnboarding();
  document.getElementById("quitApp").onclick = () => quitApp();
  window.addEventListener("hashchange", route);

  const sidenav = document.querySelector(".sidenav");
  sidenav.addEventListener("pointerenter", () => sidenav.classList.add("expanded"));
  sidenav.addEventListener("pointerleave", () => sidenav.classList.remove("expanded"));
  window.matchMedia(NARROW_MQ).addEventListener("change", () => {
    if (S.workflow && S.workflowTab === "workflow") {
      refitWorkflowView();
      renderWorkflow(S.workflow, selectNode);
      repaintNodeStates();
    }
  });
  await loadExtensions();
  await route();

  maybeAutoOpenOnboarding(S.appSettings);

  refreshAttention();
  window.addEventListener("hashchange", refreshAttention);
  setInterval(refreshAttention, 45000);
}

init().catch((e) => {
  document.getElementById("dashboardCards").textContent = "Failed to load: " + e.message;
});

// The sidebar's Quit: one confirm, then the server stops and the page says how to start again
function quitApp() {
  const busy = S.chatBusy || S.runBusy;
  openModal({
    title: "Quit Cryogram?",
    body: busy
      ? "<p>Something is still running. It stops where it is and can be picked up next time.</p>"
      : "<p>Your work is saved. Start it again from its icon, the launcher in the Cryogram folder, or by typing <code>cryogram</code> in a terminal.</p>",
    actions: [
      { label: "Cancel", kind: "btn-secondary" },
      { label: "Quit", kind: "btn-primary", onClick: async () => {
          closeModal();
          try { await api.shutdown(); } catch { }
          document.body.innerHTML = `<div class="empty-state" style="margin:80px auto;max-width:420px">
            <p>Cryogram has stopped.</p>
            <p class="muted">Start it again from its icon, the launcher in the Cryogram folder, or by typing <code>cryogram</code> in a terminal.</p></div>`;
        } },
    ],
  });
}
