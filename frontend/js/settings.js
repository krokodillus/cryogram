// Admin preferences: notifications, sharing switches and the server address
import * as api from "./api.js";
import { escapeHtml } from "./util.js";
import { setNotifyEnabled, requestNotifyPermission } from "./notify.js";

// The Preferences tab: notifications, sharing and the server section
export async function renderPreferences(root) {
  const s = await api.getSettings();
  const on = s.preferences?.notifications !== false;
  root.innerHTML = `<div class="admin-card">
    <div class="d-section">
      <h3>Notifications</h3>
      <div class="form-group">
        <label class="form-check"><input type="checkbox" id="prefNotify" ${on ? "checked" : ""}>
          Show desktop notifications</label>
        <p class="form-hint">Get a desktop pop-up when the Builder Agent needs your input, or when
          a run needs you, finishes, or runs into a problem - only while Cryogram is in the
          background. Your browser asks permission the first time. Turn this off for no
          pop-ups and no tab-title badge.</p>
        <p class="form-hint save-status" id="notifyStatus"></p>
      </div>
    </div>
    <div class="d-section">
      <h3>Updates</h3>
      <div class="form-group">
        <label class="form-check"><input type="checkbox" id="prefUpdates"
          ${s.preferences?.check_updates !== false ? "checked" : ""}>
          Check for app updates when Cryogram starts</label>
        <p class="form-hint">New features and fixes for the app itself. You
          see what changed, and nothing installs until you approve it.</p>
        <p class="form-hint save-status" id="updatesStatus"></p>
      </div>
    </div>
    <div class="d-section">
      <h3>When something goes wrong</h3>
      <div class="form-group">
        <label class="form-check"><input type="checkbox" id="prefReports"
          ${(s.preferences?.share_reports || "ask") !== "never" ? "checked" : ""}>
          Offer to send a problem report</label>
        <p class="form-hint">When a run stops, you can send us what failed and
          the step's own workings, so we can stop it happening again. You see
          exactly what would go, in full, and decide every time - nothing is
          ever sent without you pressing send.</p>
        <p class="form-hint save-status" id="shareStatus"></p>
      </div>
    </div>
    <div class="d-section">
      <h3>Server</h3>
      <div class="form-row">
        <div class="form-group"><label class="form-label">Host</label>
          <input data-server="host" value="${escapeHtml(s.server.host)}" /></div>
        <div class="form-group narrow"><label class="form-label">Port</label>
          <input data-server="port" value="${escapeHtml(s.server.port)}" /></div>
      </div>
      <p class="hint">Applied on restart.</p>
      <div class="modal-actions">
        <button class="btn btn-primary" id="serverSave">Save server settings</button>
        <span class="save-status" id="serverStatus"></span>
      </div>
    </div>
    <div class="d-section">
      <h3>Your data</h3>
      <p class="form-hint">Workflows, settings and stored keys live in your
        computer's application data area - deleting the app folder never
        touches them. This removes them too, permanently.</p>
      <div class="modal-actions">
        <button class="btn btn-danger" id="deleteAllData">Delete all my data</button>
      </div>
    </div></div>`;

  const box = root.querySelector("#prefNotify");
  const status = root.querySelector("#notifyStatus");
  box.onchange = async () => {
    const enabled = box.checked;
    setNotifyEnabled(enabled);
    if (enabled) requestNotifyPermission();
    status.textContent = "saving...";
    try {
      await api.putSettings({ preferences: { notifications: enabled } });
      status.textContent = "saved";
    } catch (e) {
      status.textContent = "failed: " + (e.message || e);
    }
  };

  // Saves one preference and reports next to the control that changed it
  const saveShare = async (patch, statusSel = "#shareStatus") => {
    const where = root.querySelector(statusSel);
    where.textContent = "saving...";
    try {
      await api.putSettings({ preferences: patch });
      where.textContent = "saved";
    } catch (e) {
      where.textContent = "failed: " + (e.message || e);
    }
  };
  root.querySelector("#prefReports").onchange = (e) =>
    saveShare({ share_reports: e.target.checked ? "ask" : "never" });
  root.querySelector("#prefUpdates").onchange = (e) =>
    saveShare({ check_updates: e.target.checked }, "#updatesStatus");

  root.querySelector("#deleteAllData").onclick = async () => {
    const { openModal, closeModal } = await import("./modal.js");
    openModal({
      title: "Delete all your data?",
      body: `<p>This permanently removes every workflow, all settings and
        every stored key from this computer, then closes Cryogram. There is
        no undo. The app itself stays - starting it again begins fresh.</p>`,
      actions: [
        { label: "Cancel", kind: "btn-secondary" },
        { label: "Delete everything", kind: "btn-danger", onClick: async () => {
            closeModal();
            try { await api.deleteAllData(); } catch { }
            document.body.innerHTML = `<div class="empty-state" style="margin:80px auto;max-width:420px">
              <p>Everything was deleted and Cryogram has closed.</p>
              <p class="muted">You can close this tab. Starting the app again begins fresh.</p></div>`;
          } },
      ],
    });
  };

  root.querySelector("#serverSave").onclick = async () => {
    const sStatus = root.querySelector("#serverStatus");
    sStatus.textContent = "saving...";
    try {
      await api.putSettings({ server: {
        host: root.querySelector('[data-server="host"]').value.trim(),
        port: Number(root.querySelector('[data-server="port"]').value) || 8000,
      } });
      sStatus.textContent = "saved - applies on restart";
    } catch (e) {
      sStatus.textContent = "failed: " + (e.message || e);
    }
  };
}
