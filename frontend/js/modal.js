// The one shared modal component; a single modal at a time
import { el, escapeHtml } from "./util.js";

let lastFocus = null;

// The one modal: title, optional subtitle and outcome mark, action buttons
export function openModal({ title, subtitle, body, actions, kind, wide = false,
                            onDismiss = null }) {
  closeModal();
  lastFocus = document.activeElement;

  const dismiss = () => { closeModal(); if (onDismiss) onDismiss(); };
  const root = el(`<div class="modal${wide ? " modal-wide" : ""}" id="appModal" role="dialog" aria-modal="true"
      aria-label="${escapeHtml(title)}">
    <div class="modal-card${kind ? ` modal-${escapeHtml(kind)}` : ""}">
      <button type="button" class="btn btn-secondary modal-x" aria-label="Close" title="Close">&times;</button>
      <h2>${escapeHtml(title)}</h2>
      ${subtitle ? `<p class="modal-sub">${escapeHtml(subtitle)}</p>` : ""}
      <div class="modal-body"></div>
      <div class="modal-actions"></div>
    </div></div>`);
  root.querySelector(".modal-body").innerHTML = body || "";
  const host = root.querySelector(".modal-actions");
  (actions || []).forEach((a) => {
    const b = el(`<button type="button" class="btn ${a.kind || "btn-secondary"}${
      a.side === "left" ? " modal-action-left" : ""}"></button>`);
    b.textContent = a.label;
    b.onclick = () => (a.onClick ? a.onClick(root) : closeModal());
    host.appendChild(b);
  });
  root.querySelector(".modal-x").onclick = dismiss;
  root.onclick = (e) => { if (e.target === root) dismiss(); };
  root.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.stopPropagation(); dismiss(); return; }
    if (e.key !== "Tab") return;
    const focusables = root.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (!focusables.length) return;
    const first = focusables[0], last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  });
  document.body.appendChild(root);
  const focusTarget = root.querySelector(
    'input, select, textarea, .modal-actions button') || root.querySelector("h2");
  focusTarget?.focus?.();
  return root;
}

// Whether a modal is on screen: there is one slot, so this is what anything waiting for it asks
export function modalOpen() {
  return !!document.getElementById("appModal");
}

// Closes the modal, returns focus, and says the slot is free
export function closeModal() {
  const had = document.getElementById("appModal");
  had?.remove();
  if (lastFocus && document.contains(lastFocus)) lastFocus.focus?.();
  lastFocus = null;
  if (had) document.dispatchEvent(new CustomEvent("cryogram:modal-closed"));
}
