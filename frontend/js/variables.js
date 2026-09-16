// The variables table: every value edited in place through its typed control
import { escapeHtml, humanise, slugName, envChip, secretCell } from "./util.js";
import * as api from "./api.js";
import { fieldControl, fieldValue, wireFolderFields } from "./valueField.js";

// The row's control by declared type; locked is one readonly field
function fieldFor(v, type, id, workflow, locked) {
  return fieldControl({
    type: type || "text", secret: !!v.secret, value: v.value,
    value_set: v.value_set, options: v.options || [], locked,

    fileName: (workflow?._variable_files || {})[v.name],
  }, id);
}

// Reads a value back in the type the step expects; empty stays empty
function editValue(v, type, id) {
  return fieldValue({ type: type || "text", secret: !!v.secret }, id);
}

// The cell a row's control lives in, re-rendered whole when the mode flips
function paintField(cell, v, type, id, workflow, locked) {
  cell.innerHTML = `<span class="var-input">${
    fieldFor(v, type, id, workflow, locked)}</span>`;
  if (!locked) wireFolderFields(cell);
}

const TYPE_LABELS = {
  text: "Text", longtext: "Text", number: "Number", boolean: "Yes / no",
  date: "Date", folder: "Folder", file: "File kept here",
  filepath: "File on this computer",
};

// The type column, in the same locked-field treatment as the value
function typeCell(v, type, workflow) {
  const t = type || "text";
  return `<div class="var-cell"><span class="var-input">
      <input type="text" class="var-type-field" readonly tabindex="-1"
        value="${escapeHtml(TYPE_LABELS[t] || humanise(t))}"></span></div>`;
}

// The In-use cell: labels plus one Use action, never a checkbox
function useCell(resolution, name, here) {
  const r = (resolution || []).find((x) => x.name === name);
  const winner = r ? r.in_use : "workflow";

  if (!r || !r.read)
    return `<div class="dt-check-cell"><span class="tag tag-default"
        title="Nothing in the workflow reads this value yet">Unused</span></div>`;
  if (winner === here)
    return `<div class="dt-check-cell"><span class="tag tag-inuse"
        title="This value is the one runs use">In use</span></div>`;
  return `<div class="dt-check-cell">
      <button type="button" class="btn btn-secondary inuse-pick" data-name="${escapeHtml(name)}"
        data-source="${escapeHtml(here)}"
        title="Use this one from the next run instead">Use</button></div>`;
}

// Deletable means deleting cannot change a run - the row is not the value runs use
function deletable(resolution, name) {
  const r = (resolution || []).find((x) => x.name === name);
  return !r || !r.read || r.in_use !== "workflow";
}

// The Delete button, disabled with its reason while the value is in use
function deleteBtn(resolution, name) {
  const can = deletable(resolution, name);
  return `<button class="btn btn-secondary var-del-btn" ${can ? "" : `disabled
      title="This value is the one runs use - switch runs to another value first."`}>Delete</button>`;
}

// One merged table of workflow and environment values, each edited through its typed control
export function renderVariables(workflow, envs, resolution) {
  const el = document.getElementById("variablesList");
  envs = Array.isArray(envs) ? envs : (envs ? [envs] : []);

  const vars = workflow.variables || [];

  const rows = [];
  vars.forEach((v, i) => rows.push({ home: "workflow", v, idx: i }));
  envs.forEach((e) => (e.variables || []).forEach((v) =>
    rows.push({ home: e.id, envName: e.name || "", v })));
  rows.sort((a, b) => a.v.name.localeCompare(b.v.name)
    || (a.home === "workflow" ? -1 : b.home === "workflow" ? 1 : 0));

  const dataRows = rows.map((r) => {
    const v = r.v;

    const desc = r.home === "workflow"
      ? (workflow._variable_descriptions || {})[v.name] : "";
    const name = `<div class="var-name">${escapeHtml(v.label || humanise(v.name))}
        ${desc ? `<span class="info-dot" title="${escapeHtml(desc)}">&#9432;</span>` : ""}
        <span class="var-key">${escapeHtml(v.name)}</span></div>`;
    if (r.home === "workflow")
      return `<div class="dt-row" data-idx="${r.idx}">
        ${name}
        ${typeCell(v, (workflow._variable_types || {})[v.name], workflow)}
        <div class="var-cell" data-value>
          <span class="var-input">${fieldFor(
            v, (workflow._variable_types || {})[v.name],
            `pv_${r.idx}`, workflow, true)}</span>
        </div>
        ${secretCell(v)}
        ${useCell(resolution, v.name, "workflow")}
        <div class="dt-actions">
          <button class="btn btn-secondary var-edit-btn">Edit</button>
          <button class="btn btn-primary var-save-btn hidden">Save</button>
          <button class="btn btn-secondary var-cancel-btn hidden">Cancel</button>
          ${deleteBtn(resolution, v.name)}
          <span class="var-saved" aria-live="polite"></span>
        </div>
      </div>`;

    return `<div class="dt-row var-ro">
        ${name}
        ${typeCell(v, (workflow._variable_types || {})[v.name], workflow)}
        <div class="var-cell" data-value>
          <span class="var-input">${fieldFor(
            v, (workflow._variable_types || {})[v.name],
            `ev_${r.home}_${v.name}`, workflow, true)}</span>
        </div>
        ${secretCell(v)}
        ${useCell(resolution, v.name, r.home)}
        <div class="dt-actions">${envChip(r.envName, r.home)}</div>
      </div>`;
  }).join("");

  const places = new Map();
  rows.forEach(({ v }) => places.set(v.name, (places.get(v.name) || 0) + 1));
  const overlap = [...places.values()].some((n) => n > 1);

  el.innerHTML = `<div class="dt dt-vars">
        <div class="dt-head"><div>Variable</div><div>Type</div><div>Value</div><div class="dt-check-head">Secret</div><div class="dt-check-head">In use</div><div class="dt-check-head">Actions</div></div>
        ${dataRows || `<div class="dt-empty muted">No variables yet. The Builder Agent adds the
            ones this workflow needs; you can add your own too.</div>`}
        <form id="projAddVar" class="dt-row dt-add">
          <div class="av-namecell">
            <input id="pvLabel" type="text" placeholder="What this value is, e.g. Region"
              autocomplete="off" spellcheck="false">
            <div class="av-nameline">
              <span id="pvNamePreview" class="var-key"></span>
              <input id="pvName" class="hidden" type="text" placeholder="custom_name"
                autocomplete="off" spellcheck="false">
              <label class="form-check"><input id="pvCustom" type="checkbox"> Custom name</label>
            </div>
          </div>
          <div class="var-cell">
            <span class="var-input">
              <select id="pvType">
                ${Object.entries(TYPE_LABELS).filter(([k]) => k !== "longtext")
                  .map(([k, lab]) =>
                    `<option value="${k}">${escapeHtml(lab)}</option>`).join("")}
              </select>
            </span>
          </div>
          <div class="var-cell" id="pvValueCell">
            <span class="var-input">
              <input id="pvValue" type="text" placeholder="value (optional)"
                autocomplete="off"></span>
          </div>
          <div class="dt-check-cell"><input id="pvSecret" type="checkbox" title="Secret"></div>
          <div class="dt-check-cell"></div>
          <div class="dt-actions"><button type="submit" class="btn btn-secondary">Add</button></div>
        </form>
      </div>
      ${overlap ? `<p class="hint">The same name appears in more than one place:
        the green label marks the value runs use. Click <strong>Use</strong> on
        another row to switch - nothing else changes, and it applies from the
        next run.</p>` : ""}
      <p class="hint">A variable is stored and used every run without asking (the
        run pauses only if it has no value). Secrets are stored locally and only
        ever shown as &bull;&bull;&bull;&bull;; a variable's Secret setting is
        fixed when it's created.</p>`;

  wireFolderFields(el);
  el.querySelectorAll(".inuse-pick").forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await api.setVariableUse(workflow.id, b.dataset.name, b.dataset.source);
      workflow._variable_resolution = r.resolution;
      renderVariables(workflow, envs, r.resolution);
    } catch (err) {
      alert(err.message || err);

      b.checked = false;
      b.disabled = false;
    }
  }));

  const labelIn = el.querySelector("#pvLabel");
  const nameIn = el.querySelector("#pvName");
  const namePreview = el.querySelector("#pvNamePreview");
  const customTick = el.querySelector("#pvCustom");

  const typeSel = el.querySelector("#pvType");
  const valueCell = el.querySelector("#pvValueCell");
  const secretTick = el.querySelector("#pvSecret");
  const addType = () => (secretTick.checked ? "text" : typeSel.value || "text");
  const paintAddValue = () => {
    valueCell.innerHTML = `<span class="var-input">${fieldControl(
      { type: addType(), secret: secretTick.checked, value: "",
        options: [] }, "pvValue")}</span>`;
    wireFolderFields(valueCell);
  };

  secretTick.onchange = () => {
    typeSel.disabled = secretTick.checked;
    paintAddValue();
  };
  paintAddValue();
  const syncName = () => {
    namePreview.textContent = slugName(labelIn.value) || "variable_name";

    const key = customTick.checked ? nameIn.value.trim() : slugName(labelIn.value);
    const known = (workflow._variable_types || {})[key];
    if (known && known !== typeSel.value
        && [...typeSel.options].some((o) => o.value === known)) {
      typeSel.value = known;
      paintAddValue();
    }
  };
  syncName();
  labelIn.oninput = syncName;
  typeSel.onchange = paintAddValue;
  customTick.onchange = () => {
    const custom = customTick.checked;
    namePreview.classList.toggle("hidden", custom);
    nameIn.classList.toggle("hidden", !custom);
    if (custom) { nameIn.value = slugName(labelIn.value); nameIn.focus(); }
    syncName();
  };
  nameIn.oninput = () => {
    const clean = slugName(nameIn.value.replace(/[\s-]+/g, "_"));
    if (nameIn.value !== clean) nameIn.value = clean;
    syncName();
  };

  el.querySelector("#projAddVar").onsubmit = async (e) => {
    e.preventDefault();
    const label = labelIn.value.trim();
    const name = customTick.checked ? nameIn.value.trim() : slugName(label);
    if (!name) return;
    const secret = secretTick.checked;
    const type = addType();

    const value = await fieldValue({ type, secret }, "pvValue");
    try {
      const fresh = await api.addVariable(workflow.id,
        { name, label, value, secret, type });
      workflow.variables = fresh.variables;

      if (secret && value) {
        const created = workflow.variables.find((x) => x.name === name);
        if (created) created.value = true;
      }
      renderVariables(workflow, envs, fresh._variable_resolution || resolution);
    } catch (err) {
      alert(err.message || err);
    }
  };

  el.querySelectorAll(".dt-row[data-idx]").forEach((row) => {
    const v = vars[+row.dataset.idx];

    const cell = row.querySelector("[data-value]");
    const editBtn = row.querySelector(".var-edit-btn");
    const saveBtn = row.querySelector(".var-save-btn");
    const cancelBtn = row.querySelector(".var-cancel-btn");
    const delBtn = row.querySelector(".var-del-btn");
    const badge = row.querySelector(".var-saved");

    const vtype = (workflow._variable_types || {})[v.name];
    const ctlId = `pv_${row.dataset.idx}`;

    const setMode = (locked) => {
      paintField(cell, locked ? v : { ...v, value: v.secret ? "" : v.value },
                 vtype, ctlId, workflow, locked);

      row.classList.toggle("editing", !locked);
      editBtn.classList.toggle("hidden", !locked);
      if (delBtn) delBtn.classList.toggle("hidden", !locked);
      saveBtn.classList.toggle("hidden", locked);
      cancelBtn.classList.toggle("hidden", locked);
    };
    const enterEdit = () => {
      setMode(false);
      document.getElementById(ctlId)?.focus();
    };
    const exitEdit = () => setMode(true);
    const save = async () => {
      const value = v.secret
        ? (document.getElementById(ctlId)?.value ?? "")
        : await editValue(v, vtype, ctlId);

      if (v.secret && value === "") { exitEdit(); return; }
      badge.className = "var-saved";
      try {
        await api.setVariable(workflow.id, v.name, value);
        if (v.secret) { v.value = true; v.value_set = true; }
        else v.value = value;
        exitEdit();
        badge.textContent = "saved";
        setTimeout(() => { badge.textContent = ""; }, 1500);
      } catch (e) {

        badge.textContent = e.message || "error";
        badge.className = "var-saved err";
      }
    };

    editBtn.onclick = enterEdit;
    cancelBtn.onclick = exitEdit;
    saveBtn.onclick = save;

    if (delBtn) delBtn.onclick = async () => {
      delBtn.disabled = true;
      try {
        await api.deleteVariable(workflow.id, v.name);
        const fresh = await api.getWorkflow(workflow.id);
        Object.assign(workflow, fresh);
        renderVariables(workflow, envs, workflow._variable_resolution);
      } catch (err) {
        alert(err.message || err);
        delBtn.disabled = false;
      }
    };

    cell.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); save(); }
      if (e.key === "Escape") { e.preventDefault(); exitEdit(); }
    });
  });
}
