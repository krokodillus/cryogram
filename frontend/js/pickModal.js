// The one picker modal for choosing workflows or environments anywhere in the app
import { openModal, closeModal } from "./modal.js";
import { escapeHtml, ARCHIVED } from "./util.js";

const SORTS = [["updated_ts", "Last updated"], ["name", "Name"]];

function chips(s) {
  if (!s) return "";
  const parts = [];
  if (s.nodes != null) parts.push([s.nodes, "steps"]);
  if (s.runs != null) parts.push([s.runs, "runs"]);
  if (s.variables != null) parts.push([s.variables, "variables"]);
  return parts.map(([v, l]) => `<span class="card-stat"><b>${v}</b> ${l}</span>`).join("");
}

// One mini card, radio or checkbox by mode
function card(it, picked, multi) {
  const sel = picked.has(it.id);
  return `<article class="card card-mini selecting${sel ? " selected" : ""}"
      data-pick="${escapeHtml(it.id)}" tabindex="0" role="${multi ? "checkbox" : "radio"}"
      aria-checked="${sel}" aria-label="${escapeHtml(it.name || "")}">
    <div class="card-head">
      <input type="${multi ? "checkbox" : "radio"}" class="card-select" tabindex="-1"
        ${sel ? "checked" : ""} aria-hidden="true">
      <h3>${escapeHtml(it.name || "Untitled")}</h3>
    </div>
    ${it.description ? `<p class="card-desc">${escapeHtml(it.description)}</p>` : ""}
    ${it.stats ? `<div class="card-stats">${chips(it.stats)}</div>` : ""}
    ${it.tags?.length ? `<div class="card-env">${it.tags.map((t) =>
      `<span class="tag">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
  </article>`;
}

// Grouped mini cards, Archived last
function grouped(items, picked, multi) {
  const groups = new Map();
  items.forEach((it) => {
    const g = (it.group || "").trim();
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(it);
  });
  const named = [...groups.keys()].filter((g) => g && g !== ARCHIVED)
    .sort((a, b) => a.localeCompare(b));
  const order = [...named];
  if (groups.has("")) order.push("");
  if (groups.has(ARCHIVED)) order.push(ARCHIVED);
  if (order.length === 1) {
    return `<div class="card-grid">${groups.get(order[0]).map((it) => card(it, picked, multi)).join("")}</div>`;
  }
  return order.map((g) => `
    <details class="card-group" ${g === ARCHIVED ? "" : "open"}>
      <summary class="group-head">${g ? escapeHtml(g) : "Ungrouped"}
        <span class="group-count">${groups.get(g).length}</span></summary>
      <div class="card-grid">${groups.get(g).map((it) => card(it, picked, multi)).join("")}</div>
    </details>`).join("");
}

// The one picker for choosing workflows or environments, as a miniature of the home page grid
export function pickModal({ title, items, multi = false, preselected = [],
                            confirmLabel = "Choose", emptyText = "Nothing to choose from.",
                            onConfirm }) {
  const picked = new Set(preselected.filter((id) => items.some((it) => it.id === id)));
  const order = [...picked];
  let query = "";
  let sortKey = items.some((it) => it.updated_ts != null) ? "updated_ts" : "name";
  let sortDir = sortKey === "name" ? "asc" : "desc";

  const root = openModal({
    title,
    body: `<div class="pick">
      ${items.length ? `<div class="dash-controls pick-controls">
        <input id="pickSearch" type="text" class="dash-search" placeholder="Search..." autocomplete="off">
        <div class="dash-sort">
          <label class="dash-sort-label">Sort by
            <select id="pickSortKey">${SORTS.map(([v, l]) =>
              `<option value="${v}" ${v === sortKey ? "selected" : ""}>${l}</option>`).join("")}</select>
          </label>
          <button type="button" id="pickSortDir" class="btn btn-secondary btn-sm"></button>
        </div>
      </div>` : ""}
      <div id="pickList" class="pick-list"></div>
    </div>`,
    actions: [
      { label: confirmLabel, kind: "btn-primary", onClick: () => {
          const ids = multi ? order.filter((id) => picked.has(id)) : [...picked];
          if (!ids.length) return;
          closeModal();
          onConfirm(ids);
        } },
      { label: "Cancel" },
    ],
  });
  root.classList.add("modal-wide");
  const list = root.querySelector("#pickList");
  const confirmBtn = root.querySelector(".modal-actions .btn-primary");
  const dirBtn = root.querySelector("#pickSortDir");

  const shown = () => {
    const q = query.trim().toLowerCase();
    const filtered = q ? items.filter((it) =>
      (it.name || "").toLowerCase().includes(q) || (it.description || "").toLowerCase().includes(q)) : items;
    const dir = sortDir === "desc" ? -1 : 1;
    return [...filtered].sort((a, b) => sortKey === "name"
      ? dir * (a.name || "").localeCompare(b.name || "")
      : dir * ((a[sortKey] || 0) - (b[sortKey] || 0)));
  };
  const paint = () => {
    const rows = shown();
    list.innerHTML = rows.length ? grouped(rows, picked, multi)
      : `<div class="empty-state"><p class="muted">${items.length ? "Nothing matches your search." : escapeHtml(emptyText)}</p></div>`;
    if (dirBtn) dirBtn.innerHTML = sortKey === "name"
      ? (sortDir === "asc" ? "&#8595; A to Z" : "&#8593; Z to A")
      : (sortDir === "desc" ? "&#8595; Newest first" : "&#8593; Oldest first");
    const n = picked.size;
    confirmBtn.disabled = !n;
    confirmBtn.textContent = multi && n > 1 ? `${confirmLabel} (${n})` : confirmLabel;
    list.querySelectorAll("[data-pick]").forEach((el) => {
      const toggle = () => {
        const id = el.dataset.pick;
        if (multi) {
          if (picked.has(id)) picked.delete(id); else { picked.add(id); order.push(id); }
        } else {
          picked.clear(); picked.add(id);
        }
        paint();
      };
      el.onclick = toggle;
      el.onkeydown = (e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(); } };
    });
  };
  root.querySelector("#pickSearch")?.addEventListener("input", (e) => { query = e.target.value; paint(); });
  root.querySelector("#pickSortKey")?.addEventListener("change", (e) => {
    sortKey = e.target.value; sortDir = sortKey === "name" ? "asc" : "desc"; paint();
  });
  dirBtn?.addEventListener("click", () => { sortDir = sortDir === "desc" ? "asc" : "desc"; paint(); });
  paint();
  root.querySelector("#pickSearch")?.focus();
  return root;
}
