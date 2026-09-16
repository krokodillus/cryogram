// Small shared helpers used across modules
export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

export const NODE_TYPE_LABEL = {
  code: "Code", connector: "Connector", browser: "Browser", ai: "AI", "user-input": "User input",
};

export function sentence(s) {
  const str = String(s ?? "");
  return str.charAt(0).toUpperCase() + str.slice(1);
}

// A code key as a readable name - mirrors the backend, so a key never shows raw in the UI
export function humanise(key) {
  const s = String(key ?? "").replace(/[_-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

// The inverse of humanise: a typed label to the code-facing key
export function slugName(label) {
  return String(label || "").toLowerCase().trim()
    .replace(/\s+/g, "_").replace(/[^a-z0-9_]/g, "");
}

// The one environment pill; pass an id to make it a link
export function envChip(name, id = null) {
  const label = escapeHtml(name || "environment");
  return id
    ? `<span class="env-chip"><a href="#/env/${escapeHtml(id)}"
         title="Variables are edited on the environment page">${label}</a></span>`
    : `<span class="env-chip">${label}</span>`;
}

// No-environment wears the same pill in grey, so the header reads as one row
export function envChipNone(text = "None") {
  return `<span class="env-chip env-chip-none">${escapeHtml(text)}</span>`;
}

const PENCIL_SVG = `<svg style="width:1em;height:1em;vertical-align:middle;fill:currentColor" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg"><path d="M883.626667 300.373333C900.266667 283.733333 900.266667 256 883.626667 240.213333L783.786667 140.373333C768 123.733333 740.266667 123.733333 723.626667 140.373333L645.12 218.453333 805.12 378.453333M128 736 128 896 288 896 759.893333 423.68 599.893333 263.68 128 736Z"/></svg>`;

// Edit-in-place on the element itself, so the page never shifts by a pixel
export function inlineEdit(host, { value, label, save, repaint,
                                   required = false, emptyText = "",
                                   maxlength = 240, placeholder = "",
                                   type = "desc" }) {
  if (!host) return;
  const cls = type === "title" ? "title-input" : "desc-input";

  host.removeAttribute("contenteditable");
  host.removeAttribute("data-placeholder");
  host.classList.remove("title-input", "desc-input");
  const shown = value ? escapeHtml(value)
    : (emptyText ? `<span class="muted">${escapeHtml(emptyText)}</span>` : "");
  host.innerHTML = `${shown}
    <button class="inline-edit" type="button" title="Edit ${escapeHtml(label)}"
      aria-label="Edit ${escapeHtml(label)}">${PENCIL_SVG}</button>`;
  host.querySelector(".inline-edit").onclick = () => {
    host.textContent = value;
    host.classList.add(cls);
    if (placeholder) host.dataset.placeholder = placeholder;

    try { host.contentEditable = "plaintext-only"; }
    catch { host.contentEditable = "true"; }
    host.focus();

    const range = document.createRange();
    range.selectNodeContents(host);
    range.collapse(false);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    let cancelled = false;
    const clamp = () => {
      if (host.textContent.length <= maxlength) return;
      host.textContent = host.textContent.slice(0, maxlength);
      const r = document.createRange();
      r.selectNodeContents(host);
      r.collapse(false);
      sel.removeAllRanges();
      sel.addRange(r);
    };
    host.oninput = clamp;
    host.onpaste = (e) => {
      e.preventDefault();
      const text = (e.clipboardData?.getData("text/plain") || "")
        .replace(/\s+/g, " ");
      document.execCommand("insertText", false, text);
      clamp();
    };
    host.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); host.blur(); }
      if (e.key === "Escape") { cancelled = true; host.blur(); }
    };
    host.onblur = async () => {
      host.oninput = host.onpaste = host.onkeydown = host.onblur = null;
      const v = cancelled ? value : host.textContent.trim();
      if (v !== value && (v || !required)) {
        try { await save(v); } catch { }
      }
      repaint();
    };
  };
}

// Token counts for humans - 1.2k, 3.4M; never money
export function humanTokens(n) {
  const v = Number(n) || 0;
  if (v >= 1e9) return (v / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (v >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
  return String(v);
}

// An element from an HTML string
export function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export const NARROW_MQ = "(max-width: 960px)";

const tabMenus = new WeakMap();
// On narrow screens a tab bar becomes this menu; clicks forward to the real tabs
export function tabMenu(tabBar, host) {
  if (!tabBar || !host) return;
  let menu = host.querySelector(".tab-menu");
  if (!menu) {
    menu = el(`<div class="tab-menu">
      <button class="btn btn-secondary tab-menu-btn" type="button"
        aria-haspopup="menu" aria-expanded="false" title="Switch section">
        <span class="tab-menu-ico" aria-hidden="true">&#9776;</span>
        <span class="tab-menu-current"></span></button>
      <div class="tab-menu-list hidden" role="menu"></div></div>`);
    host.appendChild(menu);
    const btn = menu.querySelector(".tab-menu-btn");
    const list = menu.querySelector(".tab-menu-list");
    const close = () => { list.classList.add("hidden"); btn.setAttribute("aria-expanded", "false"); };
    btn.onclick = () => {
      const open = list.classList.toggle("hidden") === false;
      btn.setAttribute("aria-expanded", String(open));
    };
    list.onclick = (e) => {
      const item = e.target.closest("[data-tab-index]");
      if (!item) return;
      close();
      const bar = tabMenus.get(host)?.bar;
      bar?.querySelectorAll(".tab")[Number(item.dataset.tabIndex)]?.click();
    };
    document.addEventListener("click", (e) => { if (!menu.contains(e.target)) close(); });
  }
  const prev = tabMenus.get(host);
  if (prev?.bar === tabBar) { prev.sync(); return; }
  prev?.observer.disconnect();
  const sync = () => {
    const tabs = [...tabBar.querySelectorAll(".tab")];
    const list = menu.querySelector(".tab-menu-list");
    list.innerHTML = tabs.map((t, i) =>
      `<button type="button" role="menuitem" data-tab-index="${i}"
         class="${t.classList.contains("active") ? "current" : ""}">${escapeHtml(t.textContent.trim())}</button>`).join("");
    const cur = tabs.find((t) => t.classList.contains("active"));
    menu.querySelector(".tab-menu-current").textContent = cur ? cur.textContent.trim() : "";
  };
  const observer = new MutationObserver(sync);
  observer.observe(tabBar, { attributes: true, attributeFilter: ["class"], subtree: true, childList: true });
  tabMenus.set(host, { bar: tabBar, observer, sync });
  sync();
}

// A timestamp (seconds, or an ISO string) as a short local date and time; "Never" when unset
export function fmtTs(v) {
  if (!v) return "Never";
  const d = typeof v === "number" ? new Date(v * 1000) : new Date(v);
  if (isNaN(d)) return "Never";
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

// A workflow card's count chips: steps, runs, ai steps, connectors
export function statChips(s, runsLabel = "runs") {
  const chips = [
    { v: s?.nodes ?? 0, l: "steps" },
    { v: s?.runs ?? 0, l: runsLabel },
    { v: s?.ai_nodes ?? 0, l: "ai" },
    { v: s?.connectors ?? 0, l: "connectors" },
  ];
  return chips.map((c) => `<span class="card-stat"><b>${c.v}</b> ${c.l}</span>`).join("");
}

// A byte count as B, KB or MB with one decimal
export function fmtSize(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// The group name that always sorts last and starts collapsed
export const ARCHIVED = "Archived";

// A secret's table cell: set or not, never the value
export function secretCell(v) {
  return `<div class="dt-check-cell">
      <input type="checkbox" ${v.secret ? "checked" : ""} disabled
        title="Set when the variable was created - can't be changed after">
    </div>`;
}
