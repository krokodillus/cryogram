# The one place a turn's in-flight state lives: which card is open, which fix this is, what was refused, what the build asked for
from __future__ import annotations

FIELDS = ("ask_open", "built", "pending_fix", "fix_issue_id",
          "last_plan_attempt", "opening_card_show_now",
          "build_now", "env_seen")

KEY = "_turn"

class TurnState(dict):
    __slots__ = ()

    def __getattr__(self, k):
        if k in FIELDS:
            return self.get(k)
        raise AttributeError(k)

    def __setattr__(self, k, v):
        if k not in FIELDS:
            raise AttributeError(f"turn state has no field {k!r}")
        if v is None or v is False:
            self.pop(k, None)
        else:
            self[k] = v

    # Reads and clears a field in one step - the turn-ending flags are consumed this way
    def take(self, k):
        if k not in FIELDS:
            raise AttributeError(f"turn state has no field {k!r}")
        return self.pop(k, None)

# The turn's state object, created on first use
def of(workflow: dict) -> TurnState:
    t = workflow.get(KEY)
    if not isinstance(t, TurnState):
        t = TurnState(t or {})
        workflow[KEY] = t
    return t
