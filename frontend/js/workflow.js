// The workflow canvas: a left-to-right directed graph with live build and run states
import { escapeHtml, NODE_TYPE_LABEL } from "./util.js";

const NODE_W = 216, COL = 262, PAD = 24, VGAP = 28;
const EDGE_W = 2;

const ROUTE_MARGIN = 6;

const SLOT_PREFIX = "when:";
const WHEN_GAP = 6;

const ROW_PASSES = 2;

let seenWorkflow = null;
let seenIds = new Set();

let buildDepth = { pid: null, byName: new Map() };

let nodePos = { pid: null, byKey: new Map() };

let edgeFollowRaf = 0;

// Edges between the same two nodes draw as one line
function mergeParallelEdges(edges) {
  const byPair = new Map();
  edges.forEach((e) => {
    const key = e.src + " " + e.dst;
    if (!byPair.has(key)) byPair.set(key, { ...e });
  });
  return [...byPair.values()];
}

let viewportWorkflow = null;
let vp = { x: 0, y: 0, scale: 1 };
const ZOOM_MIN = 0.25, ZOOM_MAX = 2;

// Forces the next render to fit-to-content again - for deliberate viewport changes only
export function refitWorkflowView() {
  viewportWorkflow = null;
}

// Centres and scales the canvas to the viewport
function fitToContent(viewportEl, canvasW, canvasH) {
  const vw = viewportEl.clientWidth, vh = viewportEl.clientHeight;
  if (!canvasW || !canvasH || !vw || !vh) return { x: 0, y: 0, scale: 1 };
  const scale = Math.max(ZOOM_MIN, Math.min(1, Math.min(vw / canvasW, vh / canvasH) * 0.92));
  return { x: (vw - canvasW * scale) / 2, y: (vh - canvasH * scale) / 2, scale };
}

// Numbers ride CSS custom properties; the class owns the transform rule
const LAYOUT_ZOOM = 2;
// Puts the pan and zoom on the canvas, in the canvas's own doubled units
function applyViewport(canvas) {
  canvas.style.setProperty("--pan-x", vp.x / LAYOUT_ZOOM + "px");
  canvas.style.setProperty("--pan-y", vp.y / LAYOUT_ZOOM + "px");
  canvas.style.setProperty("--pan-scale", vp.scale / LAYOUT_ZOOM);
}

// Zooms keeping the point under the cursor fixed on screen
function zoomTo(canvas, newScale, cx, cy) {
  newScale = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, newScale));
  const canvasX = (cx - vp.x) / vp.scale, canvasY = (cy - vp.y) / vp.scale;
  vp.x = cx - canvasX * newScale;
  vp.y = cy - canvasY * newScale;
  vp.scale = newScale;
  applyViewport(canvas);
}

const ZOOM_STEP = 1.25;
// The plus and minus buttons zoom on the viewport centre
function zoomStep(host, factor) {
  const canvas = host.querySelector(".wf-canvas");
  if (!canvas) return;
  const rect = host.getBoundingClientRect();
  zoomTo(canvas, vp.scale * factor, rect.width / 2, rect.height / 2);
}

// Wired once per host - re-wiring every render would stack listeners
function wireViewport(host) {
  host.classList.add("wf-viewport");
  const canvas = () => host.querySelector(".wf-canvas");

  host.addEventListener("wheel", (e) => {
    const c = canvas();
    if (!c) return;
    e.preventDefault();
    const rect = host.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    const factor = Math.exp(-e.deltaY * 0.0015);
    zoomTo(c, vp.scale * factor, cx, cy);
  }, { passive: false });

  host.addEventListener("dblclick", (e) => {
    const c = canvas();
    if (!c || e.target.closest(".node")) return;
    e.preventDefault();
    const rect = host.getBoundingClientRect();
    zoomTo(c, vp.scale * ZOOM_STEP, e.clientX - rect.left, e.clientY - rect.top);
  });

  let dragging = false, dragStart = null, dragOrigin = null;
  host.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".node") || e.button !== 0) return;
    dragging = true;
    dragStart = { x: e.clientX, y: e.clientY };
    dragOrigin = { x: vp.x, y: vp.y };
    host.setPointerCapture(e.pointerId);
    host.classList.add("wf-viewport-grabbing");
  });
  host.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    vp.x = dragOrigin.x + (e.clientX - dragStart.x);
    vp.y = dragOrigin.y + (e.clientY - dragStart.y);
    const c = canvas();
    if (c) applyViewport(c);
  });
  const endDrag = (e) => {
    if (!dragging) return;
    dragging = false;
    host.classList.remove("wf-viewport-grabbing");
    host.releasePointerCapture(e.pointerId);
  };
  host.addEventListener("pointerup", endDrag);
  host.addEventListener("pointercancel", endDrag);
}

// Column per step: how far it sits from the start, following the edges
function computeDepths(nodes, edges) {
  const incoming = {};
  nodes.forEach((n) => (incoming[n.id] = []));
  edges.forEach((e) => incoming[e.dst]?.push(e.src));
  const depth = {};
  const visit = (id, seen = new Set()) => {
    if (depth[id] !== undefined) return depth[id];
    if (seen.has(id)) return 0;
    seen.add(id);
    const ins = incoming[id] || [];
    depth[id] = ins.length ? Math.max(...ins.map((p) => visit(p, seen))) + 1 : 0;
    return depth[id];
  };
  nodes.forEach((n) => visit(n.id));
  return depth;
}

const STATE_LABEL = { planned: "planned", validated: "validated",
                      coded: "ready to build" };

// A planned step's state badge; built steps wear no badge
function pills(node, state = "") {

  if (node._planned)
    return `<span class="status-badge planned">${
      escapeHtml(STATE_LABEL[state] || "planned")}</span>`;

  const out = [];
  if ((node.type === "connector" || node.type === "browser") && node.read_only === false)
    out.push(node.approval_suppressed
      ? `<span class="status-badge planned">approval off</span>`
      : `<span class="status-badge irrev">needs approval</span>`);
  return out.join("");
}

// Draws one step box with its type, badges and current state
function makeNode(node, depth, onSelect, removing = false, state = "") {
  const box = document.createElement("div");
  box.className = node._planned ? "node planned" : "node";

  if (state === "coded") box.classList.add("cell-coded");
  if (removing) box.classList.add("removing");
  box.dataset.type = node.type;
  box.dataset.id = node.id;
  box.style.setProperty("--x", PAD + depth * COL + "px");
  box.style.setProperty("--y", "0px");
  box.classList.add("measuring");

  box.innerHTML = `<div class="n-type">${escapeHtml(NODE_TYPE_LABEL[node.type] || node.type)}</div>
    <div class="n-name">${escapeHtml(node.name)}</div>
    <div class="n-pills">${removing
      ? '<span class="status-badge removing">Will be removed</span>'
      : pills(node, state)}</div>`;
  if (!node._planned) {
    box.onclick = () => onSelect(node);

    box.tabIndex = 0;
    box.setAttribute("role", "button");
    box.setAttribute("aria-label", `Step: ${node.name}`);
    box.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(node); }
    };
  }
  return box;
}

// The text of a condition, laid out like a step between the two it joins
function makeSlot(slot, depth) {
  const box = document.createElement("div");
  box.className = "wf-when";
  box.dataset.id = slot.id;
  box.style.setProperty("--x", PAD + depth * COL + "px");
  box.style.setProperty("--y", "0px");
  box.classList.add("measuring");
  box.textContent = slot.when;
  box.title = slot.when;
  return box;
}

// Planned-but-unbuilt steps draw as dashed outlines in their future positions
function plannedSteps(workflow) {
  const plan = workflow.plan;
  if (!plan || !["draft", "building"].includes(plan.status)) return { nodes: [], edges: [] };

  const realNames = new Set((workflow.nodes || []).map((n) => n.name));
  const realIds = new Set((workflow.nodes || []).map((n) => n.id));
  const planned = (plan.nodes || [])
    .filter((pn) => pn.name && !realNames.has(pn.name) && !realIds.has(pn.id))
    .map((pn) => ({ id: "planned:" + (pn.id || pn.name), name: pn.name,
                    type: pn.type || "code", status: "planned",
                    _planned: true }));
  const plannedIds = new Set(planned.map((g) => g.id));
  const idFor = (ref) => {
    const real = (workflow.nodes || []).find((n) => n.name === ref || n.id === ref);
    if (real) return real.id;
    const pn = (plan.nodes || []).find((n) => n.id === ref || n.name === ref);
    return "planned:" + (pn ? (pn.id || pn.name) : ref);
  };
  const known = (id) => plannedIds.has(id) || (workflow.nodes || []).some((n) => n.id === id);
  const edges = (plan.edges || [])
    .map((e) => ({ src: idFor(e.src), dst: idFor(e.dst) }))
    .filter((e) => known(e.src) && known(e.dst));
  return { nodes: planned, edges };
}

// An orthogonal path with rounded corners through the waypoints
function roundedPath(pts, r) {
  if (pts.length < 2) return "";
  const P = (p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`;
  let d = `M ${P(pts[0])}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i - 1], c = pts[i], n = pts[i + 1];
    const d1 = Math.hypot(c[0] - p[0], c[1] - p[1]);
    const d2 = Math.hypot(n[0] - c[0], n[1] - c[1]);
    if (d1 < 0.5 || d2 < 0.5) { d += ` L ${P(c)}`; continue; }
    const rr = Math.min(r, d1 / 2, d2 / 2);
    const a = [c[0] + (p[0] - c[0]) / d1 * rr, c[1] + (p[1] - c[1]) / d1 * rr];
    const b = [c[0] + (n[0] - c[0]) / d2 * rr, c[1] + (n[1] - c[1]) / d2 * rr];
    d += ` L ${P(a)} Q ${P(c)} ${P(b)}`;
  }
  return d + ` L ${P(pts[pts.length - 1])}`;
}

// Draws the whole graph: boxes by depth, edges as curves, moves animated between renders
export function renderWorkflow(workflow, onSelect) {
  const host = document.getElementById("workflow");

  const overlay = document.getElementById("wfWorking");
  cancelAnimationFrame(edgeFollowRaf);
  host.innerHTML = "";
  if (overlay) host.appendChild(overlay);

  const gh = plannedSteps(workflow);

  const states = workflow._steps || {};
  const nodes = [...(workflow.nodes || []), ...gh.nodes];

  const plan = workflow.plan;
  const building = !!plan && ["draft", "building"].includes(plan.status);
  const nameSet = new Set(nodes.map((n) => n.name));
  const idByName = new Map(nodes.map((n) => [n.name, n.id]));
  const nameOf = (ref) => {
    const real = (workflow.nodes || []).find((n) => n.id === ref || n.name === ref);
    if (real && nameSet.has(real.name)) return real.name;
    const pn = (plan && plan.nodes || []).find((p) => p.name === ref || p.id === ref);
    if (pn && nameSet.has(pn.name)) return pn.name;
    return nameSet.has(ref) ? ref : null;
  };
  let edges;
  if (building) {

    const pairs = new Map();
    [...(plan.edges || []), ...(workflow.edges || [])].forEach((e) => {
      const sn = nameOf(e.src), dn = nameOf(e.dst);
      if (!sn || !dn || sn === dn) return;
      const key = sn + " " + dn;
      const seen = pairs.get(key);
      if (!seen)
        pairs.set(key, { src: idByName.get(sn), dst: idByName.get(dn),
                         when: e.when || "" });
      else if (!seen.when && e.when) seen.when = e.when;
    });
    edges = [...pairs.values()];
  } else {
    edges = mergeParallelEdges(workflow.edges || []);
    buildDepth = { pid: null, byName: new Map() };
  }
  if (!nodes.length) {
    seenWorkflow = workflow.id;
    seenIds = new Set();
    viewportWorkflow = null;

    const isWorking = overlay && !overlay.hidden;
    if (!isWorking) host.insertAdjacentHTML("beforeend",
      `<div class="wf-empty">No steps yet. Describe the task in the chat and the
        builder will sketch the workflow here.</div>`);
    return;
  }

  const fresh = seenWorkflow === workflow.id
    ? new Set((workflow.nodes || []).filter((n) => !seenIds.has(n.id)).map((n) => n.id))
    : new Set();
  seenWorkflow = workflow.id;
  seenIds = new Set((workflow.nodes || []).map((n) => n.id));

  const nameById = new Map(nodes.map((n) => [n.id, n.name]));
  const slots = [], slotIds = new Set();
  const split = [];
  edges.forEach((e) => {
    const when = String(e.when || "").trim();
    if (!when) { split.push({ src: e.src, dst: e.dst, when: "" }); return; }
    const id = SLOT_PREFIX + (nameById.get(e.src) || e.src) + ":" + when;
    if (!slotIds.has(id)) { slotIds.add(id); slots.push({ id, name: id, _slot: true, when }); }
    split.push({ src: e.src, dst: id, when });
    split.push({ src: id, dst: e.dst, when });
  });
  edges = split;
  const all = [...nodes, ...slots];

  const depth = computeDepths(all, edges);
  if (building) {
    if (buildDepth.pid !== workflow.id)
      buildDepth = { pid: workflow.id, byName: new Map() };
    all.forEach((n) => {
      depth[n.id] = Math.max(buildDepth.byName.get(n.name) ?? 0, depth[n.id]);
      buildDepth.byName.set(n.name, depth[n.id]);
    });
  }

  {
    const hasIncoming = new Set(edges.map((e) => e.dst));
    const succMin = {};
    edges.forEach((e) => {
      if (depth[e.dst] !== undefined)
        succMin[e.src] = Math.min(succMin[e.src] ?? Infinity, depth[e.dst]);
    });
    all.forEach((n) => {
      if (!hasIncoming.has(n.id) && succMin[n.id] !== undefined)
        depth[n.id] = Math.max(depth[n.id], succMin[n.id] - 1);
    });
  }
  const byDepth = {};
  all.forEach((n) => ((byDepth[depth[n.id]] ||= []).push(n)));
  const depths = Object.keys(byDepth).map(Number).sort((a, b) => a - b);

  {
    if (building) {
      const planIndex = new Map((plan.nodes || []).map((pn, i) => [pn.name, i]));
      Object.values(byDepth).forEach((col) =>
        col.sort((a, b) => (planIndex.get(a.name) ?? 1e9) - (planIndex.get(b.name) ?? 1e9)));
    }
    const preds = {}, succs = {};
    edges.forEach((e) => {
      (preds[e.dst] ||= []).push(e.src);
      (succs[e.src] ||= []).push(e.dst);
    });
    const row = {};
    const stamp = () => depths.forEach((d) => byDepth[d].forEach((n, i) => (row[n.id] = i)));
    const settle = (col, links) => {
      const mean = (n) => {
        const rs = (links[n.id] || []).map((m) => row[m]).filter((r) => r !== undefined);
        return rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : row[n.id];
      };
      const bary = new Map(col.map((n) => [n.id, mean(n)]));
      col.sort((a, b) => bary.get(a.id) - bary.get(b.id));
    };
    stamp();
    for (let pass = 0; pass < ROW_PASSES; pass++) {
      depths.forEach((d) => { settle(byDepth[d], preds); stamp(); });
      [...depths].reverse().forEach((d) => { settle(byDepth[d], succs); stamp(); });
    }
  }

  const canvas = document.createElement("div");
  canvas.className = "wf-canvas";
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("class", "wf-edges");
  canvas.appendChild(svg);
  host.appendChild(canvas);

  const removedRefs = new Set((building && (plan.changes || {}).removed) || []);
  const isRemoving = (n) => !n._planned
    && (removedRefs.has(n.name) || removedRefs.has(n.id));

  const probe = document.createElement("div");
  probe.style.cssText = "position:absolute;left:0;top:0;width:100px;height:100px;visibility:hidden;pointer-events:none";
  canvas.appendChild(probe);
  const perLayoutPx = probe.getBoundingClientRect().height / 100 || 1;
  probe.remove();
  const els = {}, hgt = {};
  all.forEach((n) => {
    const el = n._slot
      ? makeSlot(n, depth[n.id])
      : makeNode(n, depth[n.id], onSelect, isRemoving(n), states[n.name] || "");
    canvas.appendChild(el);
    els[n.id] = el;

    hgt[n.id] = el.getBoundingClientRect().height / perLayoutPx;
    if (fresh.has(n.id)) el.classList.add("appear");
  });

  const colH = {};
  depths.forEach((d) => {
    colH[d] = byDepth[d].reduce((s, n) => s + hgt[n.id], 0) + VGAP * (byDepth[d].length - 1);
  });
  const maxColH = Math.max(...Object.values(colH));

  const pos = {};
  let maxRight = 0;
  depths.forEach((d) => {
    let y = PAD + (maxColH - colH[d]) / 2;
    byDepth[d].forEach((n) => {
      const x = PAD + d * COL, h = hgt[n.id];
      pos[n.id] = { x, y, h };
      y += h + VGAP;
      maxRight = Math.max(maxRight, x + NODE_W);
    });
  });

  const slotDst = new Map(edges.filter((e) => slotIds.has(e.src)).map((e) => [e.src, e.dst]));
  const lines = edges.flatMap((e) => {
    if (slotIds.has(e.src)) return [];
    if (slotIds.has(e.dst)) return [{ src: e.src, dst: slotDst.get(e.dst), when: e.when,
                                      _through: true, _slot: e.dst }];
    return [e];
  });

  const isSkip = (e) => depth[e.dst] - depth[e.src] > 1;
  const LANE = 24, LANE_STEP = 12, CORNER = 12, STUB = 22;
  const GUTTER = COL - NODE_W;
  const bottoms = Object.values(pos).map((p) => p.y + p.h);
  const globalBottom = bottoms.length ? Math.max(...bottoms) : PAD;
  const steps = all.filter((n) => !slotIds.has(n.id));
  const rowBlocked = (s, t, y) => steps.some((n) => {
    const d = depth[n.id], p = pos[n.id];
    return d > s && d < t && p.y - ROUTE_MARGIN < y && y < p.y + p.h + ROUTE_MARGIN;
  });
  const route = new Map();
  const lanes = [];
  lines.filter(isSkip)
    .sort((a, b) => (depth[a.src] - depth[b.src]) || (depth[a.dst] - depth[b.dst]))
    .forEach((e) => {
      const s = depth[e.src], t = depth[e.dst];
      const a = pos[e.src], b = pos[e.dst];
      if (!a || !b) return;
      const rowY = { "src-row": a.y + a.h / 2, "dst-row": b.y + b.h / 2 };
      const tried = e._through ? ["dst-row", "src-row"] : ["src-row", "dst-row"];
      const clear = tried.find((r) => !rowBlocked(s, t, rowY[r]));
      if (clear) { route.set(e, clear); return; }
      let k = 0;
      while ((lanes[k] || (lanes[k] = [])).some(([s2, t2]) => s < t2 && s2 < t)) k++;
      lanes[k].push([s, t]);
      route.set(e, k);
    });

  lines.filter((l) => l._through).forEach((l) => {
    const a = pos[l.src], b = pos[l.dst], sp = pos[l._slot];
    if (!a || !b || !sp) return;
    const r = route.get(l);
    const lineY = r === "src-row" ? a.y + a.h / 2
      : typeof r === "number" ? globalBottom + LANE + r * LANE_STEP
      : b.y + b.h / 2;
    sp.y = lineY - hgt[l._slot] - WHEN_GAP;
  });

  if (nodePos.pid !== workflow.id) nodePos = { pid: workflow.id, byKey: new Map() };
  all.forEach((n) => {
    const p = pos[n.id], start = nodePos.byKey.get(n.name) || p;
    els[n.id].style.setProperty("--x", start.x + "px");
    els[n.id].style.setProperty("--y", start.y + "px");
    els[n.id].classList.remove("measuring");
  });
  void canvas.offsetWidth;
  all.forEach((n) => {
    const p = pos[n.id];
    els[n.id].style.setProperty("--x", p.x + "px");
    els[n.id].style.setProperty("--y", p.y + "px");
    nodePos.byKey.set(n.name, { x: p.x, y: p.y });
  });

  const W = maxRight + PAD;
  const H = Math.max(maxColH + 2 * PAD,
                     globalBottom + LANE + Math.max(0, lanes.length - 1) * LANE_STEP + PAD);
  canvas.style.setProperty("--w", W + "px");
  canvas.style.setProperty("--h", H + "px");
  svg.setAttribute("width", W);
  svg.setAttribute("height", H);

  if (viewportWorkflow !== workflow.id) {
    viewportWorkflow = workflow.id;
    vp = fitToContent(host, W, H);
  }
  applyViewport(canvas);
  if (!host.dataset.wired) { wireViewport(host); host.dataset.wired = "1"; }

  const resetBtn = document.getElementById("wfResetView");
  if (resetBtn) resetBtn.onclick = () => {
    vp = fitToContent(host, W, H);
    applyViewport(canvas);
  };
  const zoomInBtn = document.getElementById("wfZoomIn");
  if (zoomInBtn) zoomInBtn.onclick = () => zoomStep(host, ZOOM_STEP);
  const zoomOutBtn = document.getElementById("wfZoomOut");
  if (zoomOutBtn) zoomOutBtn.onclick = () => zoomStep(host, 1 / ZOOM_STEP);

  const edgeGeom = (e, posOf) => {
    const a = posOf(e.src), b = posOf(e.dst);
    if (!a || !b) return null;
    const x1 = a.x + NODE_W, y1 = a.y + a.h / 2;
    const x2 = b.x, y2 = b.y + b.h / 2;

    const r = route.get(e) ?? (e._through ? "dst-row" : undefined);
    if (Math.abs(y1 - y2) < 1 && (r === undefined || r === "src-row" || r === "dst-row"))
      return [[x1, y1], [x2, y2]];
    if (r === "src-row")
      return [[x1, y1], [x2 - GUTTER / 2, y1], [x2 - GUTTER / 2, y2], [x2, y2]];
    if (r === "dst-row")
      return [[x1, y1], [x1 + GUTTER / 2, y1], [x1 + GUTTER / 2, y2], [x2, y2]];
    if (r !== undefined) {
      const laneY = globalBottom + LANE + r * LANE_STEP;
      const xm = (x1 + x2) / 2;
      const tx1 = Math.min(x1 + STUB, xm), tx2 = Math.max(x2 - STUB, xm);
      return [[x1, y1], [tx1, y1], [tx1, laneY], [tx2, laneY], [tx2, y2], [x2, y2]];
    }
    const xm = (x1 + x2) / 2;
    return [[x1, y1], [xm, y1], [xm, y2], [x2, y2]];
  };
  const drawn = [];
  lines.forEach((e) => {
    if (!pos[e.src] || !pos[e.dst]) return;
    const path = document.createElementNS(svgNS, "path");
    path.setAttribute("stroke-width", EDGE_W);

    if (String(e.when || "").trim()) path.setAttribute("class", "edge-when");
    svg.appendChild(path);
    drawn.push({ e, path });
  });
  const paintEdges = (posOf) => {
    drawn.forEach(({ e, path }) => {
      const pts = edgeGeom(e, posOf);
      if (pts) path.setAttribute("d", roundedPath(pts, CORNER));
    });
  };

  const livePos = (id) =>
    els[id] ? { x: els[id].offsetLeft, y: els[id].offsetTop, h: hgt[id] } : pos[id];
  paintEdges(livePos);
  const FOLLOW_MS = 250;
  const t0 = performance.now();
  const follow = () => {
    if (performance.now() - t0 > FOLLOW_MS) { paintEdges((id) => pos[id]); return; }
    paintEdges(livePos);
    edgeFollowRaf = requestAnimationFrame(follow);
  };
  edgeFollowRaf = requestAnimationFrame(follow);
}
