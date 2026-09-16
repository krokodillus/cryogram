// Admin tab for reading and deleting the assistant's saved notes
import * as api from "./api.js";
import { renderMarkdown } from "./md.js";
import { escapeHtml } from "./util.js";
import { openModal, closeModal } from "./modal.js";

// The Learnings tab: read and delete what the assistant has saved
export async function renderLearningsTab(root) {
  root.innerHTML = `<p class="muted">Loading&#8230;</p>`;
  let items = [];
  try {
    items = await api.getLearnings();
  } catch {
    root.innerHTML = `<p class="muted">Failed to load the learnings.</p>`;
    return;
  }
  const rerender = () => renderLearningsTab(root);

  const rows = items.map((s) => `
    <div class="dt-row" data-learning="${escapeHtml(s.id)}" tabindex="0">
      <div class="var-name">${escapeHtml(s.title || s.id)}</div>
      <div class="dt-actions">
        <button type="button" class="btn btn-danger btn-sm"
          data-del="${escapeHtml(s.id)}">Delete</button>
      </div>
      <div class="dt-note learning-summary"><div class="clamp-3">${
        escapeHtml(s.preview || s.summary || "")}</div></div>
      <div class="dt-note learning-when">${s.updated
        ? `Last updated: ${new Date(s.updated * 1000).toLocaleDateString()}`
        : ""}</div>
    </div>`).join("");
  root.innerHTML = `
    <p class="hint">What the Builder Agent has found out about how things work -
      how a system really behaves, an approach that failed and why. Every
      workflow can draw on all of them, and the Builder Agent is shown the ones
      that fit whatever it is about to do. They are written to give nothing
      away about any single workflow, so they stay safe to share.</p>
    <div class="dt dt-learnings">
      ${rows || `<div class="dt-empty muted">Nothing learned yet. The
        agent writes one whenever it discovers how something works.</div>`}
    </div>`;

  root.querySelectorAll(".dt-row[data-learning]").forEach((row) => {
    const s = items.find((x) => x.id === row.dataset.learning);
    const open = (e) => { if (!e.target.closest("[data-del]")) learningModal(s); };
    row.onclick = open;
    row.onkeydown = (e) => { if (e.key === "Enter") learningModal(s); };
  });
  root.querySelectorAll("[data-del]").forEach((btn) => {
    btn.onclick = () => {
      const s = items.find((x) => x.id === btn.dataset.del);
      openModal({
        title: "Delete this learning",
        body: `<p>Delete <strong>${escapeHtml(s.title || s.id)}</strong>?
            The Builder Agent stops being shown it. Workflows that produced it
            are unaffected - they simply stop listing it.</p>`,
        actions: [
          { label: "Delete", kind: "btn-danger", onClick: async () => {
              closeModal();
              await api.deleteLearning(s.id).catch(() => {});
              rerender();
            } },
          { label: "Cancel" },
        ],
      });
    };
  });
}

// A learning's file opens with its own title line - dropped here so the modal heading never prints twice
function bodyWithoutTitle(content) {
  return String(content || "").replace(/^\s*#\s+.*(\r?\n)+/, "");
}

// The read-only learning modal, with delete
function learningModal(s) {
  if (!s) return;
  openModal({
    title: s.title || s.id,
    subtitle: s.updated
      ? `Last updated: ${new Date(s.updated * 1000).toLocaleDateString()}` : "",
    body: `<div class="learning-body">${
      renderMarkdown(bodyWithoutTitle(s.content))}</div>`,
    actions: [{ label: "Close" }],
  });
}
