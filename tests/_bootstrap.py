from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

if "CRYOGRAM_TEST_TMP" not in os.environ:
    _tmp = tempfile.mkdtemp(prefix="cryogram_tests_")
    os.environ["CRYOGRAM_TEST_TMP"] = _tmp
    os.environ["CRYOGRAM_DATA_DIR"] = _tmp

    os.environ["CRYOGRAM_SECRET_KEY_FILE"] = os.path.join(_tmp, "secret.key")

    import atexit
    import shutil
    atexit.register(shutil.rmtree, _tmp, True)

os.environ.setdefault("CRYOGRAM_CARD_PATIENCE", "0.05")

os.environ.setdefault("CRYOGRAM_BUILD", "dev")

TMP = os.environ["CRYOGRAM_TEST_TMP"]

_BACKEND = str(Path(__file__).resolve().parent.parent / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import contextlib

@contextlib.contextmanager
def shown_through(pid, fn):
    import turns
    began = turns.begin(pid, "chat")
    turns.bind_emit(pid, fn)
    try:
        yield
    finally:
        turns.unbind_emit(pid)
        if began:
            turns.finish(pid)

def workflow(nodes=None, pid="p_smoke"):
    from storage import store

    p = {"id": pid, "nodes": nodes or [], "edges": [], "variables": [],
         "transcript": [], "transcript_seq": 0, "plan_approved_ts": 1.0}
    store.save(p)
    return p

def said(workflow, sender, text):
    from agent import transcript
    return transcript.append_message(workflow, sender, text)

def shown_requests(workflow, request=""):
    from agent import transcript
    return [i for i in transcript.items(workflow)
            if i.get("kind") == "request"
            and (not request or i.get("request") == request)]
