# Keeps successfully loaded shipped skills available independently of the replay window
from contextlib import closing
import hashlib

from agent import skills
from storage import db

def _table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS active_skills (id TEXT PRIMARY KEY, digest TEXT NOT NULL, version TEXT NOT NULL)")

def _identity(got):
    body = got.get("content") or ""
    return hashlib.sha256(body.encode()).hexdigest(), str((got.get("meta") or {}).get("version") or "")

def register(workflow_id, sid, got):
    if not workflow_id:
        return
    digest, version = _identity(got)
    with closing(db.workflow_conn(workflow_id)) as conn, conn:
        _table(conn)
        conn.execute("INSERT OR REPLACE INTO active_skills VALUES (?, ?, ?)", (sid, digest, version))

def blocks(workflow_id, automatic=()):
    if not workflow_id:
        return []
    with closing(db.workflow_conn(workflow_id)) as conn, conn:
        _table(conn)
        rows = conn.execute("SELECT id, digest, version FROM active_skills ORDER BY id").fetchall()
    registered = {sid for sid, _, _ in rows}
    rows += [(sid, None, "") for sid in sorted(set(automatic) - registered)]
    out = []
    for sid, previous, version in rows:
        got = skills.read_static(sid)
        if got is None:
            out.append((sid, f"[skill {sid} is unavailable in this source version; do not assume its old instructions apply]"))
            continue
        digest, current_version = _identity(got)
        changed = " CHANGED since it was loaded; follow the current body below." if previous is not None and digest != previous else ""
        out.append((sid, f"[active skill {sid}, version {current_version or 'reference'}, sha256 {digest}]{changed}\n{got['content']}"))
    return out

def render(workflow_id):
    got = blocks(workflow_id)
    pinned = {sid for sid, _ in got}
    if not got:
        return "", pinned
    return "\n[loaded skills: current full bodies, independent of historical tool summaries]\n" + "\n\n".join(body for _, body in got) + "\n", pinned
