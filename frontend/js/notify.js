// Desktop notifications for the moments worth coming back for; only when the tab is in the background
let baseTitle = "Cryogram";

// The tab title is the page name; the unread badge prefixes whatever is current
export function setTitle(t) {
  baseTitle = (t || "").trim() || "Cryogram";
  bumpTitle();
}
let unread = 0;
let enabled = true;

// Follows the stored preference, live when the toggle changes
export function setNotifyEnabled(on) { enabled = on !== false; }

function bumpTitle() {
  document.title = unread > 0 ? `(${unread}) ${baseTitle}` : baseTitle;
}

function backgrounded() {

  return document.hidden || (document.hasFocus && !document.hasFocus());
}

// Asks once, from a user gesture; a no-op if unsupported or already decided
export function requestNotifyPermission() {
  if (!enabled) return;
  try {
    if ("Notification" in window && Notification.permission === "default")
      Notification.requestPermission().catch(() => {});
  } catch { }
}

// Shows a desktop notification only when the tab is in the background; clicking it opens the workflow
export function notify(title, body, { workflowId = "", tag = "" } = {}) {
  if (!enabled) return;
  if (!backgrounded()) return;
  unread++;
  bumpTitle();
  try {
    if ("Notification" in window && Notification.permission === "granted") {
      const n = new Notification(title, { body: body || "", tag: tag || undefined });
      n.onclick = () => {
        window.focus();
        if (workflowId) location.hash = `#/workflow/${workflowId}`;
        n.close();
      };
    }
  } catch { }
}

// Wires the badge to clear the moment the user returns
export function initNotify() {
  baseTitle = document.title || "Cryogram";
  const clear = () => { if (!document.hidden) { unread = 0; bumpTitle(); } };
  document.addEventListener("visibilitychange", clear);
  window.addEventListener("focus", clear);
}
