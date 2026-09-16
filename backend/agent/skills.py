# Loads the skill files that instruct the builder; the index is what the model sees, bodies load on demand
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import config

STATIC_DIR = config.SKILLS_DIR

AGENT_DIR = STATIC_DIR / "agent"

CONNECTORS_DIR = STATIC_DIR / "connectors"

def _title(content: str, stem: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return re.sub(r"^\d+-", "", stem).replace("-", " ").strip().capitalize()

def _summary(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return (line[:SUMMARY_CHARS - 3] + "...") if len(line) > SUMMARY_CHARS else line
    return ""

# Hand-rolled key-value parser - no yaml dependency
def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    meta: dict = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            meta[key.strip()] = [v.strip() for v in val[1:-1].split(",") if v.strip()]
        elif val.isdigit():
            meta[key.strip()] = int(val)
        else:
            meta[key.strip()] = val
    body = text[end + len("\n---"):].lstrip("\n")
    return meta, body

def _skill_paths(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return [p / "SKILL.md" for p in sorted(d.iterdir())
            if p.is_dir() and (p / "SKILL.md").is_file()]

# A tree's release number, from its own index; 0 when it has none
def tree_version(root: Optional[Path] = None) -> int:
    import json
    idx = (root or STATIC_DIR) / "INDEX.json"
    try:
        return int(json.loads(idx.read_text()).get("skills_version") or 0)
    except (OSError, ValueError):
        return 0

# policy is always-on and baked into the prompt; action loads on demand
def _kind(path: Path) -> str:
    meta, _ = parse_frontmatter(path.read_text())
    return meta.get("kind") or "action"

def _paths_by_kind(d: Path, kind: str) -> list[Path]:
    return [p for p in _skill_paths(d) if _kind(p) == kind]

def _entry(path: Path, family: str) -> dict:
    meta, body = parse_frontmatter(path.read_text())
    sid = path.parent.name
    return {"id": sid, "group": family,
            "title": _title(body, sid),
            "summary": meta.get("description") or _summary(body),
            "tier": meta.get("tier"), "kind": meta.get("kind") or "action",
            "depends_on": meta.get("depends_on") or []}

# The instruction files offered to the model by title; bodies load on request
def static_index() -> list[dict]:
    return ([_entry(p, "shared") for p in _skill_paths(STATIC_DIR / "shared")]
            + [_entry(p, "agent") for p in _skill_paths(AGENT_DIR)]
            + [_entry(p, "connectors") for p in _skill_paths(CONNECTORS_DIR)])

SUMMARY_CHARS = 160
_REF_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Reads one skill or one of its reference files; the resolved path must stay inside the skills dir
def read_static(sid: str) -> Optional[dict]:
    sid, _, ref = str(sid or "").partition("/")
    if ref and not _REF_NAME.match(ref):
        return None
    for d in (STATIC_DIR / "shared", AGENT_DIR, CONNECTORS_DIR):
        fname = f"{ref}.md" if ref else "SKILL.md"
        target = (d / sid / fname).resolve()
        if STATIC_DIR.resolve() not in target.parents:
            return None
        if target.is_file():
            meta, body = parse_frontmatter(target.read_text())
            full = f"{sid}/{ref}" if ref else sid
            return {"id": full, "group": d.name,
                    "title": _title(body, full), "content": body, "meta": meta}
    return None

# The always-on policy bodies - the base system prompt; action skills load on demand instead
def policy_text() -> str:
    bodies = [parse_frontmatter(p.read_text())[1].rstrip("\n")
              for d in (STATIC_DIR / "shared", AGENT_DIR)
              for p in _paths_by_kind(d, "policy")]
    return "\n\n".join(bodies) + "\n"
