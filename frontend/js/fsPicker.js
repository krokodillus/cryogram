// Folder and file pickers - native dialogs where the platform allows, since the server runs on this machine
import { headers } from "./api.js";
import { escapeHtml } from "./util.js";

// Lists one directory through the local server
async function listDir(path) {
  const r = await fetch(`/api/fs${path ? `?path=${encodeURIComponent(path)}` : ""}`,
                        { headers: headers() });
  if (!r.ok) throw new Error(`${r.status} fs`);
  return r.json();
}

// A folder tree served by the local backend, for when the native dialog is unavailable
export function renderFsPicker(host, onPick, want = "folder") {
  const file = want === "file";
  host.innerHTML = `
    <button type="button" class="btn btn-secondary fs-native">&#128193; Choose ${file ? "file" : "folder"}&#8230;</button>
    <span class="hint fs-native-note"></span>`;
  const btn = host.querySelector(".fs-native");
  const note = host.querySelector(".fs-native-note");
  btn.onclick = async () => {
    btn.disabled = true;
    note.textContent = `The ${file ? "file" : "folder"} window is open - it may be behind this one.`;
    let d = null;
    try {
      const r = await fetch(`/api/fs/pick${file ? "?want=file" : ""}`,
                            { method: "POST", headers: headers() });
      if (r.ok) d = await r.json();
    } catch { }
    btn.disabled = false;
    note.textContent = "";
    if (d?.path) return onPick(d.path);
    if (d?.cancelled) return;
    renderFsTree(host, onPick);
  };
}

// The in-app directory tree - the fallback when no desktop dialog exists
function renderFsTree(host, onPick) {
  let current = null;

  async function paint(path) {
    let d;
    try {
      d = await listDir(path);
    } catch {
      host.innerHTML = `<p class="hint">Couldn't read that folder.</p>`;
      return;
    }
    current = d.path;
    host.innerHTML = `
      <div class="fs-current" title="${escapeHtml(d.path)}">${escapeHtml(d.path)}</div>
      <div class="fs-dirs">
        ${d.parent ? `<button type="button" class="fs-dir fs-up" data-p="${escapeHtml(d.parent)}">&#8593; up</button>` : ""}
        ${d.dirs.map((n) =>
          `<button type="button" class="fs-dir" data-p="${escapeHtml(d.path)}/${escapeHtml(n)}">&#128193; ${escapeHtml(n)}</button>`).join("")}
        ${!d.dirs.length ? `<span class="hint">no folders inside</span>` : ""}
      </div>
      <button type="button" class="btn btn-primary fs-pick">Use this folder</button>`;
    host.querySelectorAll(".fs-dir").forEach((b) =>
      (b.onclick = () => paint(b.dataset.p)));
    host.querySelector(".fs-pick").onclick = () => onPick(current);
  }
  paint(null);
}
