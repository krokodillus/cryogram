// A small dependency-free markdown renderer; all output is escaped before any markup is applied
import { escapeHtml } from "./util.js";

// Inline markdown - code spans first, so their contents are never re-transformed
function inline(s) {

  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return "\u0000" + (codes.length - 1) + "\u0000";
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[\s(])\*([^*\n]+)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");

  const links = [];
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_, t, u) => {
    links.push(`<a href="${u.replace(/&amp;/g, "&")}" target="_blank" rel="noopener">${t}</a>`);
    return "\u0001" + (links.length - 1) + "\u0001";
  });
  s = linkifyEscaped(s);
  s = s.replace(/\u0001(\d+)\u0001/g, (_, i) => links[+i]);
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
}

// Auto-links bare URLs in already-escaped text, peeling trailing sentence punctuation
export function linkifyEscaped(s) {
  return s.replace(/https?:\/\/[^\s<\u0000\u0001]+/g, (u) => {
    let trail = "";
    for (;;) {
      const ent = u.match(/&(?:quot|gt|lt|#39);$/);
      if (ent) { u = u.slice(0, -ent[0].length); trail = ent[0] + trail; continue; }
      if (/[),.;:!?\x27"\]]$/.test(u) && !/&amp;$/.test(u)) {

        if (u.endsWith(")")
            && (u.match(/\(/g) || []).length >= (u.match(/\)/g) || []).length)
          break;
        trail = u.slice(-1) + trail; u = u.slice(0, -1); continue;
      }
      break;
    }
    if (!/^https?:\/\/./.test(u)) return u + trail;
    return `<a href="${u.replace(/&amp;/g, "&")}" target="_blank" rel="noopener">${u}</a>${trail}`;
  });
}

// Markdown to HTML with everything escaped first; reflow joins wrapped source lines into paragraphs
export function renderMarkdown(src, opts = {}) {

  const reflow = !!opts.reflow;

  const esc = escapeHtml((src || "").replace(/[\u0000\u0001]/g, ""));
  const out = [];
  const lines = esc.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    if (/^```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;

      out.push(`<div class="copy-box"><button type="button" class="copy-btn">Copy</button>`
               + `<pre><code>${buf.join("\n")}</code></pre></div>`);
      continue;
    }

    const h = line.match(/^(#{1,3})\s+(.*)/);
    if (h) {
      const lvl = h[1].length;
      out.push(`<h${lvl + 3}>${inline(h[2])}</h${lvl + 3}>`);
      i++;
      continue;
    }

    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length
        && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) rows.push(lines[i++]);
      const cells = (r) => r.trim().replace(/^\|/, "").replace(/\|$/, "")
        .split("|").map((c) => inline(c.trim()));
      const head = cells(rows[0]).map((c) => `<th>${c}</th>`).join("");
      const body = rows.slice(2).map((r) =>
        `<tr>${cells(r).map((c) => `<td>${c}</td>`).join("")}</tr>`).join("");
      out.push(`<table class="md-table"><thead><tr>${head}</tr></thead>`
               + `<tbody>${body}</tbody></table>`);
      continue;
    }

    const isBlockStart = (l) =>
      /^(```|#{1,3}\s|\s*[-*]\s|\s*\d+\.\s|\s*\|)/.test(l);
    const item = (marker) => {
      const parts = [lines[i++].replace(marker, "")];
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i]))
        parts.push(lines[i++].trim());
      return `<li>${inline(parts.join(" "))}</li>`;
    };

    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i]))
        items.push(item(/^\s*[-*]\s+/));
      out.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i]))
        items.push(item(/^\s*\d+\.\s+/));
      out.push(`<ol>${items.join("")}</ol>`);
      continue;
    }

    if (!line.trim()) { i++; continue; }

    const buf = [];
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i]))
      buf.push(lines[i++]);
    out.push(`<p>${reflow
      ? inline(buf.map((l) => l.trim()).join(" "))
      : buf.map(inline).join("<br>")}</p>`);
  }
  return out.join("");
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest?.(".copy-btn");
  if (!btn) return;
  const code = btn.parentElement.querySelector("code");
  if (!code) return;
  navigator.clipboard.writeText(code.textContent).then(() => {
    btn.textContent = "Copied";
    btn.classList.add("copied");
    setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1500);
  });
});
