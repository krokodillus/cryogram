# Cross-workflow notes on how external systems behave; saved anonymised, retrieved when relevant
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import config
from storage import secrets_store

INDEX_CAP = 40

_MIN_VALUE_LEN = 8

def learnings_dir() -> Path:
    return config.LEARNINGS_DIR

def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "learning").lower()).strip("-")
    return s[:80] or "learning"

def _title_of(content: str, stem: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return stem.replace("-", " ").strip()

_SUMMARY_CHARS = 160
_PREVIEW_CHARS = 600

def _opening_line(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""

def _clip(text: str, cap: int) -> str:
    return (text[:cap - 3] + "...") if len(text) > cap else text

def _files() -> list[Path]:
    d = learnings_dir()
    return sorted(d.glob("*.md")) if d.is_dir() else []

def _entry(path: Path) -> dict:
    content = path.read_text()
    opening = _opening_line(content)
    return {"id": path.stem, "title": _title_of(content, path.stem),
            "summary": _clip(opening, _SUMMARY_CHARS),

            "preview": _clip(opening, _PREVIEW_CHARS),
            "updated": path.stat().st_mtime}

def index() -> list[dict]:
    return sorted((_entry(p) for p in _files()),
                  key=lambda e: e["updated"], reverse=True)

# The note titles the agent can see and load; the full text loads on demand
def index_for(workflow: Optional[dict] = None,
              cap: int = INDEX_CAP) -> tuple[list[dict], int]:
    everything = index()
    return everything[:cap], max(0, len(everything) - cap)

INLINE_BUDGET = 4000

_W_TITLE, _W_SUMMARY, _W_BODY = 6.0, 3.0, 1.0

_W_CONTRIBUTED = 1.15

_MIN_SCORE = 1.0

_STOP = frozenset("""a an and are as at be but by for from has have how i if in
into is it its of on or that the their then there these this to was were what
when where which who will with you your step run get set use using need needs
first last new old one two do does done make made take takes""".split())

# Lowercased stems with crude plural folding - matching, not linguistics
def _terms(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9.]+", str(text or "").lower()):
        w = w.strip(".")
        if len(w) < 3 or w in _STOP:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out

# IDF-weighted overlap, stdlib only - an embedding call per learning per turn is the cost this exists to avoid
def _score(query_terms: dict[str, float], entry: dict, body: str) -> float:
    fields = ((entry.get("title", ""), _W_TITLE),
              (entry.get("summary", ""), _W_SUMMARY),
              (body, _W_BODY))
    total = 0.0
    for text, weight in fields:
        seen = set(_terms(text))
        total += weight * sum(idf for term, idf in query_terms.items()
                              if term in seen)
    return total

# Picks the notes relevant to what the agent is about to do, by weighted word overlap - no model call involved
def select(query: str, workflow: Optional[dict] = None,
           budget: int = INLINE_BUDGET) -> tuple[list[dict], list[dict]]:
    import math
    entries = index()
    if not entries:
        return [], []
    bodies = {}
    for e in entries:
        got = read(e["id"])
        bodies[e["id"]] = (got or {}).get("content", "")
    q = _terms(query)
    if not q:
        return [], entries[:INDEX_CAP]

    n = len(entries)
    docs = {e["id"]: set(_terms(e.get("title", "")) + _terms(bodies[e["id"]]))
            for e in entries}
    query_terms: dict[str, float] = {}
    for term in set(q):
        hits = sum(1 for terms in docs.values() if term in terms)
        query_terms[term] = math.log((n + 1) / (hits + 1)) + 1.0
    contributed = set((workflow or {}).get("learning_ids") or [])
    scored = []
    for e in entries:
        s = _score(query_terms, e, bodies[e["id"]])
        if e["id"] in contributed:
            s *= _W_CONTRIBUTED
        scored.append((s, e))
    scored.sort(key=lambda x: (-x[0], -x[1]["updated"]))
    inline, listed, spent = [], [], 0
    for s, e in scored:
        body = bodies[e["id"]]
        if s >= _MIN_SCORE and spent + len(body) <= budget:
            inline.append({**e, "content": body})
            spent += len(body)
        else:
            listed.append(e)
    return inline, listed[:INDEX_CAP]

def read(learning_id: str) -> Optional[dict]:
    d = learnings_dir()
    target = (d / f"{learning_id}.md").resolve()
    if not d.is_dir() or d.resolve() != target.parent or not target.is_file():
        return None
    content = target.read_text()
    return {"id": learning_id, "title": _title_of(content, learning_id),
            "content": content, "updated": target.stat().st_mtime}

# Removes one learning; workflows keep the id and skip whatever is gone
def delete(learning_id: str) -> bool:
    got = read(learning_id)
    if not got:
        return False
    (learnings_dir() / f"{learning_id}.md").unlink()
    return True

# Exact values a learning must not contain; no entropy guessing
def _identifying_values(workflow: Optional[dict]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for v in secrets_store.all_values():
        out.append(("secret", v))
    if not workflow:
        return out
    name = str(workflow.get("name") or "").strip()
    if len(name) >= 4:
        out.append(("workflow", name))
    pid = str(workflow.get("id") or "").strip()
    if len(pid) >= 4:
        out.append(("workflow", pid))
    for var in workflow.get("variables") or []:
        if var.get("secret"):
            continue
        val = str(var.get("value") if var.get("value") is not None else "")
        if len(val.strip()) >= _MIN_VALUE_LEN:
            out.append(("value", val.strip()))
    return out

# Refuses a note that names its workflow or carries any stored value, without quoting what it matched
def check(title: str, content: str,
          workflow: Optional[dict] = None) -> Optional[dict]:
    title = str(title or "").strip()
    content = str(content or "").strip()
    if not content:
        return {"error": "the learning has no content - write what you learned and when it applies"}
    if not title:
        return {"error": "the learning needs a title naming the SITUATION it applies to, so a later build knows when to read it"}
    haystack = f"{title}\n{content}".lower()
    for kind, value in _identifying_values(workflow):
        if value.lower() in haystack:
            reason = {
                "secret": "a stored credential VALUE - reference credentials by name only",
                "workflow": "this workflow's name or id - a note must read as general knowledge, not as a story about one workflow",
                "value": "a value from this workflow's settings (a link, id, path or address the user gave) - describe the KIND of value, never the value itself",
            }[kind]
            return {"error": f"this learning contains {reason}. Learnings are shared "
                             "beyond this workflow, so rewrite it for someone who has never seen it: what you learned about how the system behaves, and when that applies."}
    return None

# A learning is refused if it names the workflow it came from or carries any stored value - notes must travel clean
def save(title: str, content: str,
         workflow: Optional[dict] = None) -> dict[str, Any]:
    bad = check(title, content, workflow)
    if bad:
        return bad
    title = str(title or "").strip()
    content = str(content or "").strip()
    d = learnings_dir()
    d.mkdir(parents=True, exist_ok=True)
    nid = _slug(title)
    body = content.rstrip("\n") + "\n"
    if not body.startswith("# "):
        body = f"# {title}\n\n{body}"
    (d / f"{nid}.md").write_text(body)
    return {"ok": True, "id": nid}
