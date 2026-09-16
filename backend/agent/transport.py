# Carries the builder credential (an API key, or none for the Claude Code sign-in) into the SDK; nothing else reads it
from __future__ import annotations

from typing import Optional

_NATIVE_AIDS = {"sdk": {"web_search": "WebSearch"}}

def _delta_text(raw: dict) -> Optional[str]:
    if raw.get("type") == "content_block_delta":
        delta = raw.get("delta") or {}
        if delta.get("type") == "text_delta":
            return delta.get("text")
    return None

# A message_start begins exactly one model call and carries its input usage - the only non-heuristic round counter
def _call_start_usage(raw: dict) -> Optional[dict]:
    if raw.get("type") != "message_start":
        return None
    return ((raw.get("message") or {}).get("usage") or {})

# A message_delta closes the call with its output tokens, so a turn can report spend even when no final result arrives
def _call_end_usage(raw: dict) -> Optional[dict]:
    if raw.get("type") != "message_delta":
        return None
    return (raw.get("usage") or {})

# A transport-level failure; the driver decides transient or terminal
class TransportError(Exception):
    pass

class Transport:
    provider = ""

# The builder credential and tool exposure for the SDK driver
class SdkTransport(Transport):
    provider = "sdk"

    def __init__(self, model: str, credential: str,
                 auth: str = "claude-subscription"):
        self.model = model
        self.credential = credential
        self.auth = auth

    # The tool exposure, testable without a network call; an empty allowlist disables every built-in
    def _sdk_config(self, tools, aids) -> tuple:
        native = _NATIVE_AIDS[self.provider]
        builtins = [native[a] for a in sorted(aids) if a in native]
        mcp_names = [f"mcp__cryogram__{t['name']}" for t in tools]
        return builtins, mcp_names + builtins

# The codex builder; the subscription flavour carries no credential of ours at all
class CodexTransport(Transport):
    provider = "codex"

    def __init__(self, model: str, credential: str = "",
                 auth: str = "codex-subscription"):
        self.model = model
        self.credential = credential
        self.auth = auth

# Picks the builder transport by the provider's auth kind
def for_settings() -> Transport:
    import providers
    from storage import secrets_store
    from storage import settings
    model = settings.get()["master_ai"].get("model", "")
    provider = providers.provider_for_model(model) if model else {}

    if not provider:
        provider = providers.builder_provider()
        model = "" if provider else model
    if not provider:
        raise TransportError("No Builder Agent is set. Pick one in Admin, then send your message again.")
    auth = providers.auth_type(provider)
    if auth == "codex-subscription":
        return CodexTransport(model, "", auth="codex-subscription")
    if auth == "codex-api-key":
        cred = secrets_store.get_secret(provider.get("key_name") or "",
                                        secrets_store.OWNER_APP) or ""
        if not cred:
            raise TransportError("the builder provider's key is not set")
        return CodexTransport(model, cred, auth="codex-api-key")
    if auth == "claude-subscription":
        return SdkTransport(model, "", auth="claude-subscription")
    cred = secrets_store.get_secret(provider.get("key_name") or "",
                                    secrets_store.OWNER_APP) or ""
    if not cred:
        raise TransportError("the builder provider's key is not set")

    adapter = (provider.get("adapter") or "anthropic").lower()
    if adapter != "anthropic":
        raise TransportError(
            "the builder's API key is not an Anthropic (Claude) key - the builder runs on Claude. Use an Anthropic API key, or your Claude Code sign-in, in Admin.")
    return SdkTransport(model, cred, auth="api-key")
