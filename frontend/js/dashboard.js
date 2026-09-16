// The home page: workflow cards in user-named groups, with select mode for bulk actions
import * as api from "./api.js";
import { escapeHtml, envChip, fmtTs, statChips, ARCHIVED } from "./util.js";
import { openModal, closeModal } from "./modal.js";
import { pickModal } from "./pickModal.js";

// One workflow card, with a select box while selecting
function workflowCard(p, envNames) {
  const sel = selected.has(p.id);
  const box = selecting
    ? `<input type="checkbox" class="card-select" data-sel="${escapeHtml(p.id)}"
         ${sel ? "checked" : ""} aria-label="Select ${escapeHtml(p.name)}">` : "";
  return `<article class="card${sel ? " selected" : ""}${selecting ? " selecting" : ""}"
      data-id="${escapeHtml(p.id)}" tabindex="0" role="link"
      aria-label="Open ${escapeHtml(p.name)}">
    <div class="card-head">
      ${box}${p.needs_attention
        ? `<span class="alert-dot" title="Something new since you last looked">!</span>` : ""}
      <h3>${escapeHtml(p.name)}</h3>
    </div>
    <p class="card-desc">${escapeHtml(p.description) || "<span class='muted'>No description yet.</span>"}</p>
    <div class="card-stats">${statChips(p.stats)}</div>
    <div class="card-meta">
      <span class="card-meta-item">Updated ${fmtTs(p.updated_ts)}</span>
      <span class="card-meta-item">Last run ${fmtTs(p.last_run_ts)}</span>
    </div>
    ${envNames.length ? `<div class="card-env" title="Attached environments">${
      envNames.map((n) => envChip(n)).join("")}</div>` : ""}
  </article>`;
}

// Named groups alphabetically, Ungrouped below them, Archived always last and closed
export function groupSections(items, renderCard, collapsed = collapsedGroups) {
  const groups = new Map();
  items.forEach((p) => {
    const g = (p.group || "").trim();
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(p);
  });
  const named = [...groups.keys()]
    .filter((g) => g && g !== ARCHIVED).sort((a, b) => a.localeCompare(b));
  const order = [...named];
  if (groups.has("")) order.push("");
  if (groups.has(ARCHIVED)) order.push(ARCHIVED);
  return order.map((g) => `
    <details class="card-group" data-group="${escapeHtml(g)}"${collapsed.has(g) ? "" : " open"}>
      <summary class="group-head">${g ? escapeHtml(g) : "Ungrouped"}
        <span class="group-count">${groups.get(g).length}</span></summary>
      <div class="card-grid">
        ${groups.get(g).map(renderCard).join("")}
      </div>
    </details>`).join("");
}

// The selection toolbar above the groups
function selectionBar() {
  if (!selecting) return "";
  const n = selected.size;
  const dis = n ? "" : "disabled";
  return `<div class="select-bar">
    <span class="select-count">${n} selected</span>
    <button type="button" class="btn btn-secondary btn-sm" id="selMove" ${dis}>Move to group</button>
    <button type="button" class="btn btn-secondary btn-sm" id="selArchive" ${dis}>Archive</button>
    <button type="button" class="btn btn-danger btn-sm" id="selDelete" ${dis}>Delete</button>
    <button type="button" class="btn btn-secondary btn-sm" id="selCancel">Cancel</button>
  </div>`;
}

const SORT_OPTIONS = [["updated_ts", "Last updated"], ["last_run_ts", "Last run"]];

let dashQuery = "";
let dashSortKey = "updated_ts";
let dashSortDir = "desc";

let selecting = false;
let selected = new Set();
let selectAnchor = null;

const collapsedGroups = new Set([ARCHIVED]);

// The search and sort controls
function controlsHtml() {
  return `<div class="dash-controls">
    <input id="dashSearch" type="text" class="dash-search" placeholder="Search by name or description..."
      value="${escapeHtml(dashQuery)}" />
    <div class="dash-sort">
      <label class="dash-sort-label">Sort by
        <select id="dashSortKey">${SORT_OPTIONS.map(([v, label]) =>
          `<option value="${v}" ${v === dashSortKey ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <button type="button" id="dashSortDir" class="btn btn-secondary btn-sm" title="Toggle sort direction">
        ${dashSortDir === "desc" ? "&#8595; Newest first" : "&#8593; Oldest first"}</button>
    </div>
  </div>`;
}

// Applies the search text and sort key
function filterAndSort(workflows) {
  const q = dashQuery.trim().toLowerCase();
  const filtered = q
    ? workflows.filter((p) => (p.name || "").toLowerCase().includes(q)
        || (p.description || "").toLowerCase().includes(q))
    : workflows;
  const dir = dashSortDir === "desc" ? -1 : 1;
  return [...filtered].sort((a, b) => dir * ((a[dashSortKey] || 0) - (b[dashSortKey] || 0)));
}

// The home page: cards in collapsible groups, with search, sort and a select mode for bulk actions
export async function renderDashboard({ onOpen, onNew }) {
  const host = document.getElementById("dashboardCards");
  host.innerHTML = `<p class="muted">Loading&#8230;</p>`;
  const [workflows, envs] = await Promise.all(
    [api.getWorkflows(), api.getEnvironments()]);
  const envNames = Object.fromEntries(envs.map((e) => [e.id, e.name]));
  const rerender = () => renderDashboard({ onOpen, onNew });

  host.innerHTML = (workflows.length ? controlsHtml() : "")
    + `<div id="dashGroups"></div>`;

  const groupsHost = document.getElementById("dashGroups");

  function paintGroups() {
    const shown = filterAndSort(workflows);
    groupsHost.innerHTML = selectionBar() + (shown.length
      ? groupSections(shown, (p) => workflowCard(p,
          (p.environment_ids || []).map((id) => envNames[id]).filter(Boolean)))
      : `<div class="empty-state"><p>${workflows.length
           ? "No workflow matches your search."
           : "No workflows yet."}</p>
           ${workflows.length ? "" : `<p class="muted">Create one and describe the
             task - the Builder Agent does the rest.</p>`}</div>`);
    wireCards();
  }

  function syncSelectionUI() {
    groupsHost.querySelectorAll(".card").forEach((card) => {
      const on = selected.has(card.dataset.id);
      card.classList.toggle("selected", on);
      const box = card.querySelector(".card-select");
      if (box) box.checked = on;
    });
    const bar = groupsHost.querySelector(".select-bar");
    if (bar) {
      bar.querySelector(".select-count").textContent = `${selected.size} selected`;
      ["selMove", "selArchive", "selDelete"].forEach((bid) => {
        const b = bar.querySelector(`#${bid}`);
        if (b) b.disabled = selected.size === 0;
      });
    }
  }

  const orderedIds = () =>
    [...groupsHost.querySelectorAll(".card")].map((c) => c.dataset.id);

  function clickSelect(id, shift) {
    if (shift && selectAnchor) {
      const ids = orderedIds();
      const a = ids.indexOf(selectAnchor), b = ids.indexOf(id);
      if (a !== -1 && b !== -1) {
        ids.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((r) => selected.add(r));
        syncSelectionUI();
        return;
      }
    }
    if (selected.has(id)) selected.delete(id); else selected.add(id);
    selectAnchor = id;
    syncSelectionUI();
  }

  function exitSelect() {
    selecting = false;
    selected.clear();
    selectAnchor = null;
    const sm = document.getElementById("selectMode");
    if (sm) sm.textContent = "Select";
    paintGroups();
  }

  function wireCards() {
    groupsHost.querySelectorAll(".card").forEach((card) => {
      const id = card.dataset.id;
      const open = () => onOpen(id);
      card.onclick = (e) => {
        if (selecting) clickSelect(id, e.shiftKey); else open();
      };
      card.onkeydown = (e) => {
        if (e.key !== "Enter") return;
        if (selecting) clickSelect(id, e.shiftKey); else open();
      };
    });

    groupsHost.querySelectorAll("details.card-group").forEach((d) => {
      d.ontoggle = () => {
        const g = d.dataset.group;
        if (d.open) collapsedGroups.delete(g); else collapsedGroups.add(g);
      };
    });

    const cancel = groupsHost.querySelector("#selCancel");
    if (cancel) cancel.onclick = exitSelect;
    const move = groupsHost.querySelector("#selMove");
    if (move) move.onclick = () => moveSelected(workflows, [...selected], () => {
      selected.clear(); rerender();
    });
    const arch = groupsHost.querySelector("#selArchive");
    if (arch) arch.onclick = async () => {
      const ids = [...selected];
      await runBulk(ids.map((id) => api.setWorkflowGroup(id, ARCHIVED)), "archive");
      selected.clear(); rerender();
    };
    const del = groupsHost.querySelector("#selDelete");
    if (del) del.onclick = () => deleteSelected([...selected], workflows, () => {
      selected.clear(); rerender();
    });
  }

  const search = document.getElementById("dashSearch");
  if (search) {
    search.oninput = () => { dashQuery = search.value; paintGroups(); };
    search.onkeydown = (e) => e.stopPropagation();
  }
  const sortKeySelect = document.getElementById("dashSortKey");
  if (sortKeySelect) {
    sortKeySelect.onchange = () => { dashSortKey = sortKeySelect.value; paintGroups(); };
  }
  const sortDirBtn = document.getElementById("dashSortDir");
  if (sortDirBtn) {
    sortDirBtn.onclick = () => {
      dashSortDir = dashSortDir === "desc" ? "asc" : "desc";
      sortDirBtn.innerHTML = dashSortDir === "desc" ? "&#8595; Newest first" : "&#8593; Oldest first";
      paintGroups();
    };
  }
  paintGroups();

  const selBtn = document.getElementById("selectMode");
  if (selBtn) {
    selBtn.textContent = selecting ? "Done" : "Select";
    selBtn.disabled = !workflows.length;
    selBtn.onclick = () => {
      selecting = !selecting;
      if (!selecting) selected.clear();
      selBtn.textContent = selecting ? "Done" : "Select";
      paintGroups();
    };
  }
  document.getElementById("newWorkflow").onclick = () => onNew(workflows, envs);
  const imp = document.getElementById("importWorkflow");
  if (imp) imp.onclick = () => importModal();
}

// Import a workflow from a file; the file is scrubbed again on import
function importModal() {
  openModal({
    title: "Import a workflow",
    body: `<div class="form-group"><label class="form-label">Workflow file
          <span class="label-optional">.cryogram.json</span></label>
        <input id="impFile" type="file" accept=".json,application/json">
        <p class="form-hint">Settings and passwords are never in a shared
          workflow - you enter your own on the first run.</p></div>
      <p class="form-error hidden" id="impError"></p>`,
    actions: [
      { label: "Import", kind: "btn-primary", onClick: async (root) => {
          const err = root.querySelector("#impError");
          const say = (m) => { err.textContent = m; err.classList.remove("hidden"); };
          const file = root.querySelector("#impFile").files?.[0];
          if (!file) return say("Choose a file first.");
          let doc;
          try { doc = JSON.parse(await file.text()); }
          catch { return say("That file isn't a Cryogram workflow."); }
          try {
            const r = await api.importWorkflow({ doc });
            closeModal();
            if (r?.workflow?.id) location.hash = `#/workflow/${r.workflow.id}`;
          } catch (e) {
            say(e.status === 400 && e.message
              ? e.message : "Something went wrong importing that workflow.");
          }
        } },
      { label: "Cancel" },
    ],
  });
}

// Runs bulk requests and reports how many failed - a swallowed failure looks like nothing happened
async function runBulk(promises, verb) {
  const results = await Promise.allSettled(promises);
  const failed = results.filter((r) => r.status === "rejected").length;
  if (failed) {
    openModal({
      title: `Could not ${verb} ${failed} of ${results.length}`,
      body: `<p>${failed} workflow${failed > 1 ? "s" : ""} could not be
          ${verb}d. If you have just updated Cryogram, restart it so the latest
          changes take effect, then try again.</p>`,
      actions: [{ label: "Close" }],
    });
  }
  return failed;
}

// Bulk move to a group
function moveSelected(workflows, ids, done) {
  if (!ids.length) return;
  const groups = [...new Set(workflows.map((x) => x.group).filter(Boolean))];
  openModal({
    title: `Move ${ids.length} workflow${ids.length > 1 ? "s" : ""}`,
    body: `<div class="form-group">
        <label class="form-label">Group <span class="label-optional">leave empty to ungroup</span></label>
        <input id="bulkGroup" type="text" list="bulkGrpList" placeholder="e.g. Sales, Finance">
        <datalist id="bulkGrpList">${groups.map((g) =>
          `<option value="${escapeHtml(g)}">`).join("")}</datalist></div>`,
    actions: [
      { label: "Move", kind: "btn-primary", onClick: async () => {
          const group = document.getElementById("bulkGroup").value.trim();
          closeModal();
          await runBulk(ids.map((id) => api.setWorkflowGroup(id, group)), "move");
          done();
        } },
      { label: "Cancel" },
    ],
  });
  document.getElementById("bulkGroup")?.focus();
}

// Bulk permanent delete, one confirm
function deleteSelected(ids, workflows, done) {
  if (!ids.length) return;
  const names = ids.map((id) => workflows.find((p) => p.id === id)?.name).filter(Boolean);
  openModal({
    title: "Delete for good",
    body: `<p>Delete ${ids.length} workflow${ids.length > 1 ? "s" : ""} permanently?
        Everything they own goes with them - steps, run history and reference
        data. This cannot be undone.</p>
      ${names.length ? `<p class="muted">${names.map(escapeHtml).join(", ")}</p>` : ""}`,
    actions: [
      { label: "Delete permanently", kind: "btn-danger", onClick: async () => {
          closeModal();
          await runBulk(ids.map((id) => api.deleteWorkflow(id)), "delete");
          done();
        } },
      { label: "Cancel" },
    ],
  });
}

let npDraft = null;
// The New workflow modal; a draft survives the environment picker reopening it
export function newWorkflowModal(workflows, envs, onCreated, state = {}) {
  const groups = [...new Set(workflows.map((x) => x.group).filter(Boolean))];
  if (!("name" in state) && npDraft) state = npDraft;
  const picked = [...(state.picked || [])];
  const envName = (id) => envs.find((e) => e.id === id)?.name || "environment";
  const saveDraft = () => {
    npDraft = {
      name: document.getElementById("npName")?.value || "",
      desc: document.getElementById("npDesc")?.value || "",
      group: document.getElementById("npGroup")?.value || "",
      picked: [...picked],
    };
  };
  openModal({
    title: "New workflow",
    body: `<div class="form-group"><label class="form-label">Name</label>
        <input id="npName" type="text" placeholder="e.g. Invoices to ledger" value="${escapeHtml(state.name || "")}"></div>
      <div class="form-group"><label class="form-label">What should it do?</label>
        <textarea id="npDesc" rows="3" placeholder="Be as detailed as you can - don't worry, the Builder Agent will ask if anything is unclear.">${escapeHtml(state.desc || "")}</textarea>
        <p id="npDescError" class="form-error hidden">Say what the workflow should do - that is where the building starts.</p></div>
      <div class="form-group"><label class="form-label">Group <span class="label-optional">optional</span></label>
        <input id="npGroup" type="text" list="npGrpList" placeholder="e.g. Sales" value="${escapeHtml(state.group || "")}">
        <datalist id="npGrpList">${groups.map((g) =>
          `<option value="${escapeHtml(g)}">`).join("")}</datalist></div>
      <div class="form-group"><label class="form-label">Environments <span class="label-optional">optional - reusable variables the workflow can draw on</span></label>
        ${envs.length ? `<div id="npEnvPills" class="np-env-pills">${picked.map((id) =>
            `<span class="env-chip">${escapeHtml(envName(id))}</span>`).join("")}</div>
          <button type="button" class="btn btn-secondary" id="npEnvPick">${picked.length ? "Change environments" : "Choose environments"}</button>`
          : `<p class="hint">No environments yet - create them on the Environments page.</p>`}</div>`,
    actions: [
      { label: "Create", kind: "btn-primary", onClick: async () => {
          const name = document.getElementById("npName").value.trim();
          const desc = document.getElementById("npDesc").value.trim();
          const group = document.getElementById("npGroup").value.trim();
          if (!desc) {
            document.getElementById("npDescError").classList.remove("hidden");
            document.getElementById("npDesc").focus();
            return;
          }
          const envIds = [...picked];
          npDraft = null;
          closeModal();

          let created;
          try {
            created = await api.createWorkflow(name, "", group, envIds);
          } catch (e) {
            alert(`Creating the workflow failed: ${e.message || e}`);
            return;
          }
          onCreated(created, desc);
        } },
      { label: "Cancel" },
    ],
  });
  document.getElementById("npName")?.focus();
  ["npName", "npDesc", "npGroup"].forEach((id) =>
    document.getElementById(id)?.addEventListener("input", saveDraft));
  const pickBtn = document.getElementById("npEnvPick");
  if (pickBtn) pickBtn.onclick = () => {
    saveDraft();
    const keep = npDraft;
    pickModal({
      title: "Choose environments",
      confirmLabel: "Add",
      multi: true,
      preselected: picked,
      emptyText: "No environments yet - create them on the Environments page.",
      items: envs.map((e) => ({ id: e.id, name: e.name, description: e.description || "",
                                group: e.group || "",
                                stats: { variables: e.variable_count ?? 0 } })),
      onConfirm: (ids) => {
        npDraft = { ...keep, picked: ids };
        newWorkflowModal(workflows, envs, onCreated, npDraft);
      },
    });

    const watch = setInterval(() => {
      if (!document.getElementById("appModal")) {
        clearInterval(watch);
        newWorkflowModal(workflows, envs, onCreated, keep);
      } else if (!document.getElementById("pickList")) {
        clearInterval(watch);
      }
    }, 150);
  };
}
