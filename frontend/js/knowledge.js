// The knowledge view: plain-language pages on how the app behaves
import { renderMarkdown } from "./md.js";
import { escapeHtml } from "./util.js";

let topics = null;

// The Knowledge page: the topic list and the rendered body
export async function renderKnowledge(topicId) {
  const list = document.getElementById("knowledgeTopics");
  const body = document.getElementById("knowledgeBody");
  if (!list || !body) return;
  try {
    if (!topics) topics = await (await fetch("knowledge/index.json")).json();
  } catch {
    body.innerHTML = `<p class="muted">Could not load the knowledge pages.</p>`;
    return;
  }
  const current = topics.find((t) => t.id === topicId) || topics[0];
  if (!current) return;
  list.innerHTML = topics.map((t) =>
    `<button type="button" class="knowledge-topic${t.id === current.id ? " active" : ""}"
       data-id="${escapeHtml(t.id)}">${escapeHtml(t.title)}</button>`).join("");
  list.querySelectorAll("[data-id]").forEach((b) =>
    (b.onclick = () => renderKnowledge(b.dataset.id)));
  try {
    const md = await (await fetch(`knowledge/${current.file}`)).text();
    body.innerHTML = renderMarkdown(md, { reflow: true });
  } catch {
    body.innerHTML = `<p class="muted">Could not load this page.</p>`;
  }
  body.scrollTop = 0;
}
