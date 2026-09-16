// The admin view: builder AI, workflow AI providers, preferences and learnings
import { renderBuilderTab } from "./adminBuilder.js";
import { renderProvidersTab } from "./adminProviders.js";
import { renderLearningsTab } from "./adminLearnings.js";
import { renderPreferences } from "./settings.js";
import { tabMenu } from "./util.js";

// The four tab panes, looked up fresh per render
const panes = () => ({
  builder: document.getElementById("builderPane"),
  workflow: document.getElementById("providersPane"),
  learnings: document.getElementById("learningsPane"),
  preferences: document.getElementById("preferencesPane"),
});
const renderers = {
  builder: renderBuilderTab,
  workflow: renderProvidersTab,
  learnings: renderLearningsTab,
  preferences: renderPreferences,
};

let wired = false;

// The admin page entry, called by the router; a tab click just changes the hash and the router calls back in
export async function renderAdminPage(tab) {
  const p = panes();
  if (!p[tab]) tab = "builder";
  if (!wired) {
    document.getElementById("adminTabs").addEventListener("click", (e) => {
      const btn = e.target.closest(".tab");
      if (btn && panes()[btn.dataset.tab]) location.hash = `#/admin/${btn.dataset.tab}`;
    });
    wired = true;
  }
  tabMenu(document.getElementById("adminTabs"), document.getElementById("adminActions"));
  document.querySelectorAll("#adminTabs .tab").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === tab));
  Object.entries(p).forEach(([k, el]) => el.classList.toggle("hidden", k !== tab));
  try {
    await renderers[tab](p[tab]);
  } catch (e) {
    p[tab].textContent = "Failed to load: " + e.message;
  }
}
