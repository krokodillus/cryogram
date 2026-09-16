# Seeds the default provider cards once per install; nothing the user set up is ever overwritten
from __future__ import annotations
# Runs each seeding step once per install, recorded under a marker so it never repeats
def run() -> None:
    for step in (_seed_default_providers_once,
                 _seed_more_default_providers_once,
                 _lift_workflow_notes_once,
                 _clear_starter_models_once,
                 _reconcile_providers_every_boot):
        try:
            step()
        except Exception as e:
            print(f"[seed] {step.__name__} did not finish: "
                  f"{type(e).__name__}: {e}")

# One-time: the first three default provider cards
def _seed_default_providers_once() -> None:
    _seed_defaults("default_providers")

# One-time: the default cards the spec table gained after the first seed, for installs that ran it
def _seed_more_default_providers_once() -> None:
    _seed_defaults("default_providers_2")

# Every boot: a card without its key is switched off and the Default tick sits on an active card
def _reconcile_providers_every_boot() -> None:
    from storage import settings
    s = settings.get()
    settings.update({"providers": s.get("providers") or [],
                     "default_provider": s.get("default_provider", "")})

# One-time: a default card with no key stored loses its starter model rows; the vendor's list is the setup step
def _clear_starter_models_once() -> None:
    from storage import secrets_store, settings
    s = settings.get()
    if (s.get("migrations") or {}).get("starter_models_cleared"):
        return
    providers = [dict(p) for p in s.get("providers", []) or []]
    changed = False
    for p in providers:
        if p.get("id") not in settings.DEFAULT_PROVIDER_IDS or not p.get("models"):
            continue
        key = secrets_store.get_secret(p.get("key_name") or "", secrets_store.OWNER_APP) \
            if p.get("key_name") else ""
        if not key:
            p["models"] = []
            changed = True
    settings.update({"migrations": {**(s.get("migrations") or {}),
                                    "starter_models_cleared": True},
                     **({"providers": providers} if changed else {})})

# One-time: per-workflow notes move to the global store; a taken name keeps both copies
def _lift_workflow_notes_once() -> None:
    import shutil
    from storage import settings
    s = settings.get()
    if (s.get("migrations") or {}).get("notes_global"):
        return
    import config
    moved = 0

    sources = list(config.WORKFLOWS_DIR.glob("*/skills"))
    legacy_notes = config.DATA_DIR / "notes"
    if legacy_notes.is_dir():
        sources.append(legacy_notes)
    for src_dir in sorted(sources):
        if not src_dir.is_dir():
            continue
        for md in sorted(src_dir.glob("*.md")):
            config.LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)
            target = config.LEARNINGS_DIR / md.name
            n = 2
            while target.exists():
                target = config.LEARNINGS_DIR / f"{md.stem}-{n}.md"
                n += 1
            shutil.move(str(md), str(target))
            moved += 1
        shutil.rmtree(src_dir, ignore_errors=True)
    settings.update({"migrations": {**(s.get("migrations") or {}),
                                    "notes_global": True}})
    if moved:
        print(f"[seed] lifted {moved} workflow notes into the shared learnings store")

# Seeds every default spec not already present; a future default is one spec row and one marker
def _seed_defaults(marker: str) -> None:
    from urllib.parse import urlparse
    from storage import model_catalog, settings
    s = settings.get()
    if (s.get("migrations") or {}).get(marker):
        return
    existing = [dict(p) for p in s.get("providers", []) or []]
    taken_keys = {p.get("key_name") for p in existing if p.get("key_name")}

    def unique_key(prefix: str) -> str:
        name, n = f"{prefix}_API_KEY", 2
        while name in taken_keys:
            name = f"{prefix}_{n}_API_KEY"
            n += 1
        taken_keys.add(name)
        return name

    present = {p.get("id") for p in existing}
    specs = [sp for sp in settings.DEFAULT_PROVIDER_SPECS
             if sp["id"] not in present]
    defaults = {sp["id"]: {
        "id": sp["id"], "name": sp["name"], "adapter": sp["adapter"],
        "auth": "api-key", "use": "workflow", "enabled": True,
        "endpoint": sp["endpoint"], "key_name": "", "tags": [],

        "models": [],
    } for sp in specs}

    def match_default(p: dict) -> str:
        if "local" in (p.get("tags") or []):
            return ""
        ep = (p.get("endpoint") or "").strip()
        for sp in specs:
            if p.get("adapter") != sp["adapter"]:
                continue
            if sp["host"]:
                if sp["host"] in ep:
                    return sp["id"]
            elif not ep:
                return sp["id"]
        return ""

    builders, kept, customs, merged = [], [], [], set()
    for p in existing:
        if p.get("use") == "builder" or p.get("auth") in (
                "claude-subscription", "codex-subscription", "codex-api-key"):
            p.setdefault("name", "Builder")

            p["id"] = "builder" if not builders \
                else (p.get("id") or settings.mint_provider_id())
            builders.append(p)
            continue
        if p.get("id") in settings.DEFAULT_PROVIDER_IDS:
            kept.append(p)
            continue
        did = match_default(p)
        if did and did not in merged:
            d = defaults[did]
            if p.get("key_name"):
                d["key_name"] = p["key_name"]
            if p.get("tags"):
                d["tags"] = p["tags"]
            named = [m for m in p.get("models", []) if m.get("name")]
            if named:
                d["models"] = named
            merged.add(did)
        else:
            if not p.get("id"):
                p["id"] = settings.mint_provider_id()
            if not p.get("name"):
                host = urlparse(p.get("endpoint") or "").hostname or ""
                p["name"] = f"Custom ({host or p.get('adapter', 'provider')})"
            customs.append(p)
    for sp in specs:
        if not defaults[sp["id"]]["key_name"]:
            defaults[sp["id"]]["key_name"] = unique_key(sp["key"])
    settings.update({
        "providers": builders + kept + [defaults[sp["id"]] for sp in specs]
                     + customs,
        "migrations": {**(s.get("migrations") or {}), marker: True}})
