// Environment pages: reusable variable sets, edited only here
import * as api from "./api.js";
import { escapeHtml, humanise, slugName, inlineEdit, secretCell } from "./util.js";
import { openModal, closeModal } from "./modal.js";

const collapsedEnvGroups = new Set();

let selectingEnv = false;
let selectedEnvs = new Set();
let envSelectAnchor = null;

// Runs bulk requests and reports failures rather than swallowing them
async function runEnvBulk(promises, verb) {
  const results = await Promise.allSettled(promises);
  const failed = results.filter((r) => r.status === "rejected").length;
  if (failed) {
    openModal({
      title: `Could not ${verb} ${failed} of ${results.length}`,
      body: `<p>${failed} environment${failed > 1 ? "s" : ""} could not be
          ${verb}d. If you have just updated Cryogram, restart it so the latest
          changes take effect, then try again.</p>`,
      actions: [{ label: "Close" }],
    });
  }
  return failed;
}

// One environment card
function envCard(e) {
  const sel = selectedEnvs.has(e.id);
  const box = selectingEnv
    ? `<input type="checkbox" class="card-select" data-sel="${escapeHtml(e.id)}"
         ${sel ? "checked" : ""} aria-label="Select ${escapeHtml(e.name)}">` : "";
  return `<article class="card${sel ? " selected" : ""}${selectingEnv ? " selecting" : ""}"
      data-id="${escapeHtml(e.id)}" tabindex="0" role="link"
      aria-label="Open ${escapeHtml(e.name)}">
    <div class="card-head">${box}<h3>${escapeHtml(e.name)}</h3></div>
    <p class="card-desc">${escapeHtml(e.description) || "<span class='muted'>No description yet.</span>"}</p>
    <div class="card-stats">
      <span class="card-stat"><b>${e.variable_count}</b> variables</span>
      <span class="card-stat"><b>${e.used_by}</b> ${e.used_by === 1 ? "workflow" : "workflows"}</span>
    </div>
  </article>`;
}

// The selection toolbar - environments have no archive, so move or delete
function envSelectionBar() {
  if (!selectingEnv) return "";
  const n = selectedEnvs.size;
  const dis = n ? "" : "disabled";
  return `<div class="select-bar">
    <span class="select-count">${n} selected</span>
    <button type="button" class="btn btn-secondary btn-sm" id="envSelMove" ${dis}>Move to group</button>
    <button type="button" class="btn btn-danger btn-sm" id="envSelDelete" ${dis}>Delete</button>
    <button type="button" class="btn btn-secondary btn-sm" id="envSelCancel">Cancel</button>
  </div>`;
}

// Grouped like the dashboard, each group collapsible
function envGroupSections(envs) {
  const groups = new Map();
  envs.forEach((e) => {
    const g = (e.group || "").trim();
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(e);
  });
  const named = [...groups.keys()].filter(Boolean).sort((a, b) => a.localeCompare(b));
  const order = groups.has("") ? [...named, ""] : named;

  if (order.length === 1 && order[0] === "") {
    return `<div class="card-grid">${envs.map(envCard).join("")}</div>`;
  }
  return order.map((g) => `
    <details class="card-group" data-group="${escapeHtml(g)}"${collapsedEnvGroups.has(g) ? "" : " open"}>
      <summary class="group-head">${g ? escapeHtml(g) : "Ungrouped"}
        <span class="group-count">${groups.get(g).length}</span></summary>
      <div class="card-grid">${groups.get(g).map(envCard).join("")}</div>
    </details>`).join("");
}

// The Environments page: cards, selection and the new-environment modal
export async function renderEnvironments({ onOpen }) {
  const host = document.getElementById("environmentCards");
  host.innerHTML = `<p class="muted">Loading&#8230;</p>`;
  const envs = await api.getEnvironments();
  const rerender = () => renderEnvironments({ onOpen });
  host.innerHTML = envs.length
    ? envSelectionBar() + envGroupSections(envs)
    : `<div class="empty-state">
         <p>No environments yet.</p>
         <p class="muted">An environment is a reusable set of variables (endpoints,
           credentials, shared config) that any workflow can attach.</p>
       </div>`;

  const syncSelectionUI = () => {
    host.querySelectorAll(".card").forEach((card) => {
      const on = selectedEnvs.has(card.dataset.id);
      card.classList.toggle("selected", on);
      const box = card.querySelector(".card-select");
      if (box) box.checked = on;
    });
    const bar = host.querySelector(".select-bar");
    if (bar) {
      bar.querySelector(".select-count").textContent = `${selectedEnvs.size} selected`;
      ["envSelMove", "envSelDelete"].forEach((bid) => {
        const b = bar.querySelector(`#${bid}`);
        if (b) b.disabled = selectedEnvs.size === 0;
      });
    }
  };
  const orderedIds = () => [...host.querySelectorAll(".card")].map((c) => c.dataset.id);
  const clickSelect = (id, shift) => {
    if (shift && envSelectAnchor) {
      const ids = orderedIds();
      const a = ids.indexOf(envSelectAnchor), b = ids.indexOf(id);
      if (a !== -1 && b !== -1) {
        ids.slice(Math.min(a, b), Math.max(a, b) + 1).forEach((r) => selectedEnvs.add(r));
        syncSelectionUI();
        return;
      }
    }
    if (selectedEnvs.has(id)) selectedEnvs.delete(id); else selectedEnvs.add(id);
    envSelectAnchor = id;
    syncSelectionUI();
  };

  host.querySelectorAll(".card").forEach((card) => {
    const id = card.dataset.id;
    card.onclick = (e) => { if (selectingEnv) clickSelect(id, e.shiftKey); else onOpen(id); };
    card.onkeydown = (e) => {
      if (e.key !== "Enter") return;
      if (selectingEnv) clickSelect(id, e.shiftKey); else onOpen(id);
    };
  });
  host.querySelectorAll("details.card-group").forEach((d) => {
    d.ontoggle = () => {
      const g = d.dataset.group;
      if (d.open) collapsedEnvGroups.delete(g); else collapsedEnvGroups.add(g);
    };
  });

  const cancel = host.querySelector("#envSelCancel");
  if (cancel) cancel.onclick = () => {
    selectingEnv = false; selectedEnvs.clear(); envSelectAnchor = null; rerender();
  };
  const move = host.querySelector("#envSelMove");
  if (move) move.onclick = () => moveEnvs(envs, [...selectedEnvs], () => {
    selectedEnvs.clear(); rerender();
  });
  const del = host.querySelector("#envSelDelete");
  if (del) del.onclick = () => deleteEnvs([...selectedEnvs], envs, () => {
    selectedEnvs.clear(); rerender();
  });

  const selBtn = document.getElementById("selectEnvMode");
  if (selBtn) {
    selBtn.textContent = selectingEnv ? "Done" : "Select";
    selBtn.disabled = !envs.length;
    selBtn.onclick = () => {
      selectingEnv = !selectingEnv;
      if (!selectingEnv) selectedEnvs.clear();
      rerender();
    };
  }
  document.getElementById("newEnvironment").onclick = () => newEnvModal(onOpen, envs);
}

// Bulk move to a group
function moveEnvs(envs, ids, done) {
  if (!ids.length) return;
  const groups = [...new Set(envs.map((e) => e.group).filter(Boolean))];
  openModal({
    title: `Move ${ids.length} environment${ids.length > 1 ? "s" : ""}`,
    body: `<div class="form-group">
        <label class="form-label">Group <span class="label-optional">leave empty to ungroup</span></label>
        <input id="envBulkGroup" type="text" list="envBulkGrpList" placeholder="e.g. Production, Staging">
        <datalist id="envBulkGrpList">${groups.map((g) =>
          `<option value="${escapeHtml(g)}">`).join("")}</datalist></div>`,
    actions: [
      { label: "Move", kind: "btn-primary", onClick: async () => {
          const group = document.getElementById("envBulkGroup").value.trim();
          closeModal();
          await runEnvBulk(ids.map((id) => api.updateEnvironment(id, { group })), "move");
          done();
        } },
      { label: "Cancel" },
    ],
  });
  document.getElementById("envBulkGroup")?.focus();
}

// Bulk delete - workflows keep their own variables but lose the shared set
function deleteEnvs(ids, envs, done) {
  if (!ids.length) return;
  const names = ids.map((id) => envs.find((e) => e.id === id)?.name).filter(Boolean);
  openModal({
    title: "Delete environments",
    body: `<p>Delete ${ids.length} environment${ids.length > 1 ? "s" : ""}? Workflows
        using them keep their own variables but lose these shared sets (they are
        detached). This cannot be undone.</p>
      ${names.length ? `<p class="muted">${names.map(escapeHtml).join(", ")}</p>` : ""}`,
    actions: [
      { label: "Delete", kind: "btn-danger", onClick: async () => {
          closeModal();
          await runEnvBulk(ids.map((id) => api.deleteEnvironment(id)), "delete");
          done();
        } },
      { label: "Cancel" },
    ],
  });
}

// The New environment modal
function newEnvModal(onOpen, envs = []) {
  const groups = [...new Set(envs.map((e) => e.group).filter(Boolean))];
  openModal({
    title: "New environment",
    body: `<div class="form-group"><label class="form-label">Name</label>
        <input id="neName" type="text" placeholder="e.g. CRM production"></div>
      <div class="form-group"><label class="form-label">Description <span class="label-optional">optional</span></label>
        <textarea id="neDesc" rows="3" placeholder="What these variables are for."></textarea></div>
      <div class="form-group"><label class="form-label">Group <span class="label-optional">optional</span></label>
        <input id="neGroup" type="text" list="neGrpList" placeholder="e.g. Production, Staging">
        <datalist id="neGrpList">${groups.map((g) =>
          `<option value="${escapeHtml(g)}">`).join("")}</datalist></div>`,
    actions: [
      { label: "Create", kind: "btn-primary", onClick: async () => {
          const name = document.getElementById("neName").value.trim();
          const desc = document.getElementById("neDesc").value.trim();
          const group = document.getElementById("neGroup").value.trim();
          closeModal();
          let env;
          try {
            env = await api.createEnvironment(name, desc, group);
          } catch (e) {
            alert(`Creating the environment failed: ${e.message || e}`);
            return;
          }
          onOpen(env.id);
        } },
      { label: "Cancel" },
    ],
  });
  document.getElementById("neName")?.focus();
}

// A secret never shows a value, only whether one is stored; plain values show themselves
function varValue(v) {
  if (v.secret) {
    return v.value_set
      ? `<span class="secret">••••••••</span>`
      : `<span class="muted">(not set)</span>`;
  }
  const s = v.value ?? "";
  return s === "" ? `<span class="muted">(empty)</span>` : escapeHtml(s);
}

// An environment's own page - the one place its variables are edited
export async function renderEnvDetail(envId, { onDeleted }) {
  const host = document.getElementById("envDetail");
  host.innerHTML = `<p class="muted">Loading&#8230;</p>`;
  let env;
  try {
    env = await api.getEnvironment(envId);
  } catch {
    host.innerHTML = `<p class="muted">This environment no longer exists.</p>`;
    return;
  }
  const rerender = () => renderEnvDetail(envId, { onDeleted });

  host.innerHTML = `
    <div class="page-head detail-head">
      <div class="page-head-row">
        <h1 id="envName"></h1>
        <div class="head-actions">
          <button id="envCopy" class="btn btn-secondary"
            title="Make an independent copy of this environment">Copy</button>
          <button id="envDelete" class="btn btn-danger">Delete</button>
        </div>
      </div>
      <p class="page-sub" id="envDesc"></p>
    </div>
    <p class="hint">Variables here are shared: every workflow that attaches this
      environment sees them read-only and uses their values at run time. Secret
      values are stored in the secret store and never shown again.</p>
    <div id="envVars" class="env-vars"></div>`;

  const list = host.querySelector("#envVars");
  const dataRows = env.variables.map((v, i) => `<div class="dt-row" data-idx="${i}">
        <div class="var-name">${escapeHtml(v.label || humanise(v.name))}
          <span class="var-key">${escapeHtml(v.name)}</span></div>
        <div>
          <span class="var-val var-disp">${varValue(v)}</span>
          <span class="var-input hidden"><input type="${v.secret ? "password" : "text"}"
            placeholder="${v.secret ? "type new value" : "value"}"
            autocomplete="off" spellcheck="false"></span>
        </div>
        ${secretCell(v)}
        <div class="dt-actions">
          <button class="btn btn-secondary var-edit-btn">Edit</button>
          <button class="btn btn-primary var-save-btn hidden">Save</button>
          <button class="btn btn-secondary var-cancel-btn hidden">Cancel</button>
          <button class="btn btn-secondary var-del-btn">Delete</button>
          <span class="var-saved" aria-live="polite"></span>
        </div>
      </div>`).join("");
  list.innerHTML = `<div class="dt dt-env-vars">
      <div class="dt-head"><div>Name</div><div>Value</div><div class="dt-check-head">Secret</div><div>Actions</div></div>
      ${dataRows || `<div class="dt-empty muted">No variables yet - add the first one below.</div>`}
      <form id="envAddVar" class="dt-row dt-add">
        <div class="av-namecell">
          <input id="avLabel" type="text" placeholder="What this value is, e.g. Google service account key"
            autocomplete="off" spellcheck="false">
          <div class="av-nameline">
            <span id="avNamePreview" class="var-key"></span>
            <input id="avName" class="hidden" type="text" placeholder="custom_name"
              autocomplete="off" spellcheck="false">
            <label class="form-check"><input id="avCustom" type="checkbox"> Custom name</label>
          </div>
        </div>
        <input id="avValue" type="text" placeholder="value" autocomplete="off">
        <div class="dt-check-cell"><input id="avSecret" type="checkbox" title="Secret"></div>
        <div class="dt-actions"><button type="submit" class="btn btn-primary">Add</button></div>
      </form>
    </div>`;

  list.querySelectorAll(".dt-row[data-idx]").forEach((row) => {
    const v = env.variables[+row.dataset.idx];

    const wrap = row.querySelector(".var-input");
    const input = wrap.querySelector("input");
    const disp = row.querySelector(".var-disp");
    const editBtn = row.querySelector(".var-edit-btn");
    const saveBtn = row.querySelector(".var-save-btn");
    const cancelBtn = row.querySelector(".var-cancel-btn");
    const badge = row.querySelector(".var-saved");
    const delBtn = row.querySelector(".var-del-btn");
    const enterEdit = () => {
      input.value = v.secret ? "" : (v.value ?? "");
      disp.classList.add("hidden");
      wrap.classList.remove("hidden");
      editBtn.classList.add("hidden");
      delBtn.classList.add("hidden");
      saveBtn.classList.remove("hidden");
      cancelBtn.classList.remove("hidden");
      input.focus();
    };
    const exitEdit = () => {
      wrap.classList.add("hidden");
      disp.classList.remove("hidden");
      editBtn.classList.remove("hidden");
      delBtn.classList.remove("hidden");
      saveBtn.classList.add("hidden");
      cancelBtn.classList.add("hidden");
    };
    editBtn.onclick = enterEdit;
    cancelBtn.onclick = exitEdit;
    const save = async () => {
      const value = input.value;

      if (v.secret && value === "") { exitEdit(); return; }
      badge.className = "var-saved";
      try {
        await api.setEnvVariable(env.id, v.name, value);
        if (v.secret) { v.value = null; v.value_set = true; }
        else v.value = value;
        exitEdit();
        disp.innerHTML = varValue(v);
        badge.textContent = "saved";
        setTimeout(() => { badge.textContent = ""; }, 1500);
      } catch (e) {
        badge.textContent = e.message || "error";
        badge.className = "var-saved err";
      }
    };
    saveBtn.onclick = save;
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); save(); }
      if (e.key === "Escape") { e.preventDefault(); exitEdit(); }
    });

    delBtn.onclick = () => openModal({
      title: `Delete "${v.label || humanise(v.name)}"?`,
      body: `<p>Any workflow using this value may stop working: it falls back
          to its own setting of the same name, which starts empty, so the next
          run pauses to ask for it.</p>
        ${v.secret ? `<p>The stored value is deleted from this computer too.</p>` : ""}`,
      actions: [
        { label: "Delete", kind: "btn-danger", onClick: async () => {
            closeModal();
            try { await api.deleteEnvVariable(env.id, v.name); }
            catch (e) { alert(`Deleting failed: ${e.message || e}`); return; }
            rerender();
          } },
      ],
    });
  });

  const labelIn = host.querySelector("#avLabel");
  const nameIn = host.querySelector("#avName");
  const namePreview = host.querySelector("#avNamePreview");
  const customTick = host.querySelector("#avCustom");
  const syncName = () => {
    namePreview.textContent = slugName(labelIn.value) || "variable_name";
  };
  syncName();
  labelIn.oninput = syncName;
  customTick.onchange = () => {
    const custom = customTick.checked;
    namePreview.classList.toggle("hidden", custom);
    nameIn.classList.toggle("hidden", !custom);
    if (custom) { nameIn.value = slugName(labelIn.value); nameIn.focus(); }
  };
  nameIn.oninput = () => {
    const clean = slugName(nameIn.value.replace(/[\s-]+/g, "_"));
    if (nameIn.value !== clean) nameIn.value = clean;
  };

  const avValue = host.querySelector("#avValue");
  const avSecret = host.querySelector("#avSecret");
  if (avValue && avSecret) avSecret.onchange = () => {
    avValue.type = avSecret.checked ? "password" : "text";
    avValue.placeholder = avSecret.checked ? "value (hidden as you type)" : "value";
  };
  host.querySelector("#envAddVar").onsubmit = async (e) => {
    e.preventDefault();
    const label = labelIn.value.trim();
    const name = customTick.checked ? nameIn.value.trim() : slugName(label);
    if (!name) return;
    const value = host.querySelector("#avValue").value;
    const secret = host.querySelector("#avSecret").checked;
    try { await api.addEnvVariable(env.id, { name, label, value, secret }); }
    catch (err) { alert(`Adding failed: ${err.message || err}`); return; }
    rerender();
  };

  const paintName = () => inlineEdit(host.querySelector("#envName"), {
    value: env.name || "", label: "the name", maxlength: 120, required: true,
    type: "title",
    save: async (v) => { await api.updateEnvironment(env.id, { name: v }); env.name = v; },
    repaint: paintName,
  });
  const paintDesc = () => inlineEdit(host.querySelector("#envDesc"), {
    value: env.description || "", label: "the description", maxlength: 240,
    type: "desc",
    emptyText: "No description yet",
    placeholder: "One line on what this environment is for",
    save: async (v) => {
      await api.updateEnvironment(env.id, { description: v });
      env.description = v;
    },
    repaint: paintDesc,
  });
  paintName();
  paintDesc();

  host.querySelector("#envCopy").onclick = () => {
    openModal({
      title: `Copy "${env.name}"`,
      body: `<p>Make a copy of this environment? Its variables - including
          stored secret values - are copied; the copy is independent and
          starts out attached to no workflow.</p>`,
      actions: [
        { label: "Copy", kind: "btn-primary", onClick: async () => {
            closeModal();
            const r = await api.duplicateEnvironment(env.id).catch(() => null);
            if (r?.environment?.id)
              location.hash = `#/env/${r.environment.id}`;
          } },
        { label: "Cancel" },
      ],
    });
  };
  host.querySelector("#envDelete").onclick = () => {
    openModal({
      title: "Delete environment",
      body: `<p>Delete <strong>${escapeHtml(env.name)}</strong>? Workflows using it
          keep their own variables but lose this shared set (it is detached from
          them). This cannot be undone.</p>`,
      actions: [
        { label: "Delete", kind: "btn-danger", onClick: async () => {
            closeModal();
            await api.deleteEnvironment(env.id);
            onDeleted();
          } },
        { label: "Cancel" },
      ],
    });
  };
}
