# One-time data migrations at startup, each recorded so it never runs twice
from __future__ import annotations

# Runs each one-time data migration once, recorded under a marker; one failure never blocks startup
def run_pending() -> None:
    for step in _STEPS:
        try:
            step()
        except Exception as e:
            print(f"[lift] {step.__name__} did not finish: "
                  f"{type(e).__name__}: {e}")

def done(marker: str) -> bool:
    from storage import settings
    return bool((settings.get().get("migrations") or {}).get(marker))

# Merges the finished step's marker in; other steps' markers stay
def mark(marker: str) -> None:
    from storage import settings
    s = settings.get()
    settings.update({"migrations": {**(s.get("migrations") or {}),
                                    marker: True}})

# The developer's pre-release migrations, when that gitignored module exists; a release tree runs none
def _prelaunch_steps() -> tuple:
    try:
        from storage import lift_prelaunch
    except ImportError:
        return ()
    return tuple(lift_prelaunch.STEPS)

_STEPS: tuple = _prelaunch_steps() + ()
