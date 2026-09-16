# Starter model lists per provider for the admin page, and the facts about what each model reads (PDFs, images)
from __future__ import annotations

import json
from pathlib import Path

PROVIDERS: dict[str, dict] = {
    "anthropic":  {"adapter": "anthropic",  "reads": ["pdf", "image"],
                   "request_bytes": 32 * 1024 * 1024, "pdf_pages": 600},
    "openai":     {"adapter": "openai",     "reads": ["pdf", "image"],
                   "request_bytes": 32 * 1024 * 1024, "file_bytes": 50 * 1024 * 1024,
                   "pdf_pages": 100},
    "gemini":     {"adapter": "gemini",     "reads": ["pdf", "image"],
                   "request_bytes": 20 * 1024 * 1024, "pdf_pages": 1000},
    "codex":      {"adapter": "compatible", "reads": []},
    "compatible": {"adapter": "compatible", "reads": []},
}

BUILDER_MODELS_FILE = Path(__file__).resolve().parents[2] / "builder-models.json"

# The Builder AI tab's model rows per engine family, read from builder-models.json; the ticked one carries default
def builder_models() -> dict[str, list[dict]]:
    try:
        raw = json.loads(BUILDER_MODELS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, list[dict]] = {}
    for family, spec in raw.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("models"), list):
            continue
        reads = list(PROVIDERS.get(family, PROVIDERS["compatible"])["reads"])
        out[family] = [{"name": str(m), "reads": reads,
                        **({"default": True} if str(m) == str(spec.get("default") or "") else {})}
                       for m in spec["models"] if str(m).strip()]
    return out

CATALOG: dict[str, list[dict]] = builder_models()

def get() -> dict[str, list[dict]]:
    return {adapter: [dict(m) for m in models] for adapter, models in CATALOG.items()}

# The catalogue key a provider record answers to: its own id when the catalogue knows it, else its adapter's family
def catalog_key(provider: dict) -> str:
    pid = str((provider or {}).get("id") or "")
    if pid in PROVIDERS:
        return pid
    if not provider:
        return "compatible"
    adapter = provider.get("adapter", "anthropic")
    if adapter == "anthropic":
        return "anthropic"

    if adapter == "gemini":
        return "gemini"
    return "compatible"

# What one model can be sent and how: the request shape, the file types it reads, the vendor's limits
def facts(provider: dict, model: str = "") -> dict:
    key = catalog_key(provider)
    meta = PROVIDERS.get(key) or PROVIDERS["compatible"]
    reads, source = list(meta.get("reads") or []), "provider"
    row = next((m for m in (provider or {}).get("models", []) or []
                if m.get("name") == model), None)
    if row and isinstance(row.get("reads"), list):
        reads, source = list(row["reads"]), "row"
    else:
        cat = next((m for m in CATALOG.get(key, []) if m.get("name") == model), None)
        if cat and isinstance(cat.get("reads"), list):
            reads, source = list(cat["reads"]), "catalogue"
    return {"adapter": meta.get("adapter", "compatible"),
            "reads_pdf": "pdf" in reads, "reads_image": "image" in reads,
            "reads": reads,
            "request_bytes": meta.get("request_bytes"),
            "file_bytes": meta.get("file_bytes") or meta.get("request_bytes"),
            "pdf_pages": meta.get("pdf_pages"), "source": source}

# The words a person or the Builder Agent reads for what a model takes
def reads_phrase(f: dict) -> str:
    if f.get("reads_pdf") and f.get("reads_image"):
        return "reads PDFs and images"
    if f.get("reads_image"):
        return "reads images"
    if f.get("reads_pdf"):
        return "reads PDFs"
    return "text only"
