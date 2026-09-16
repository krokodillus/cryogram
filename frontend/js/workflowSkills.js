// The notes and build-materials sections of a workflow's information tab
import * as api from "./api.js";
import { renderMarkdown } from "./md.js";
import { escapeHtml, fmtSize } from "./util.js";
import { openModal, closeModal } from "./modal.js";

// What this workflow contributed to the shared learnings
export async function renderWorkflowSkills(workflow) {
  const el = document.getElementById("workflowSkillsList");
  if (!el) return;
  let items = [];
  try {
    items = await api.getWorkflowLearnings(workflow.id);
  } catch {
    el.innerHTML = `<p class="muted vars-empty">Failed to load the learnings.</p>`;
    return;
  }
  const rows = items.map((s) => `
    <div class="dt-row" data-skill="${escapeHtml(s.id)}" tabindex="0">
      <div class="var-name">${escapeHtml(s.title || s.id)}</div>
      <div class="var-val">${s.updated ? new Date(s.updated * 1000).toLocaleDateString() : ""}</div>
      ${s.summary ? `<div class="dt-note">${escapeHtml(s.summary)}</div>` : ""}
    </div>`).join("");
  el.innerHTML = `
    <p class="hint">What the Builder Agent worked out while building this
      workflow. They are kept centrally and offered to every other workflow
      too - read and remove them under Admin &gt; Learnings.</p>
    <div class="dt dt-skills">
      <div class="dt-head"><div>Title</div><div>Updated</div></div>
      ${rows || `<div class="dt-empty muted">Nothing yet. Anything the
        agent works out while building this shows up here.</div>`}
    </div>`;
  el.querySelectorAll(".dt-row[data-skill]").forEach((row) => {
    const s = items.find((x) => x.id === row.dataset.skill);
    const open = () => skillModal(s);
    row.onclick = open;
    row.onkeydown = (e) => { if (e.key === "Enter") open(); };
  });
}

// The read-only learning modal
function skillModal(s) {
  if (!s) return;
  openModal({
    title: s.title || s.id,
    body: renderMarkdown(s.content || ""),
    actions: [{ label: "Close" }],
  });
}

// The Samples table: reopen by name, delete; uploads happen through the chat paperclip
export function renderSamples(workflow, { onDelete } = {}) {
  const el = document.getElementById("samplesList");
  if (!el) return;
  const fmtWhen = (ts) => ts
    ? new Date(ts * 1000).toLocaleString([], { dateStyle: "medium",
                                              timeStyle: "short" }) : "";
  const rows = (workflow.samples || []).map((s) => `
    <div class="dt-row var-ro">
      <div class="var-name">${s.ref
        ? `<a href="/api/blobs/${escapeHtml(String(s.ref).replace(/^blob:/, ""))}"
             target="_blank" rel="noopener">${escapeHtml(s.name)}</a>`
        : escapeHtml(s.name)}</div>
      <div class="var-val">${s.kind === "recording" ? "Recording &middot; " : ""}${escapeHtml(s.mime || "file")}</div>
      <div class="var-val">${fmtSize(s.size)}</div>
      <div class="var-val">${escapeHtml(fmtWhen(s.ts))}</div>
      <div class="dt-actions">
        <button class="btn btn-secondary mat-delete"
          data-name="${escapeHtml(s.name)}">Delete</button>
      </div>
      ${s.kind === "recording" && (s.domains || []).length
        ? `<div class="dt-note">Talks to ${escapeHtml(s.domains.join(", "))}</div>` : ""}
    </div>`).join("");
  el.innerHTML = `
    <p class="hint">What you shared for the Builder Agent to design against -
      example documents and recordings. The finished workflow cannot see or
      access these; they are stored here for your reference. Click a name to
      open it; share something new with the paperclip in the chat.</p>
    <div class="dt dt-materials">
      <div class="dt-head"><div>Name</div><div>Type</div><div>Size</div>
        <div>Shared</div><div></div></div>
      ${rows || `<div class="dt-empty muted">Nothing shared yet. Use the paperclip
        in the chat.</div>`}
    </div>`;
  el.querySelectorAll(".mat-delete").forEach((b) => (b.onclick = () => {
    const name = b.dataset.name;
    openModal({
      title: "Delete this material?",
      body: `<p>&ldquo;${escapeHtml(name)}&rdquo; will no longer be shown here
        or visible to the Builder Agent. Workflows are not affected.</p>`,
      actions: [
        { label: "Delete", kind: "btn-danger", onClick: async () => {
            closeModal();
            await onDelete?.(name);
          } },
        { label: "Cancel" },
      ],
    });
  }));
}
