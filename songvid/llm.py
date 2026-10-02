"""Thin Claude wrapper: one structured-output call that returns parsed JSON."""

from __future__ import annotations

import json
import sys
import threading

import anthropic


class LLMUnavailable(RuntimeError):
    pass


# $ per million tokens (input, output), list prices; cache discounts not applied, so this is an upper estimate.
PRICES = {
    "claude-fable-5-1": (10, 50), "claude-fable-5": (10, 50), "claude-opus-5-5": (4, 20), "claude-opus-5": (5, 25),
    "claude-opus-4-8": (5, 25), "claude-sonnet-5-5": (2, 10), "claude-sonnet-5": (2, 10), "claude-haiku-4-5": (1, 5),
}

_calls = threading.local()


def record(msg, asked: str) -> dict:
    """Note which model actually answered (a refusal fallback can switch it) and what it cost."""
    served = getattr(msg, "model", None) or asked
    usage = getattr(msg, "usage", None)
    tin = getattr(usage, "input_tokens", 0) or 0
    tin += (getattr(usage, "cache_read_input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    tout = getattr(usage, "output_tokens", 0) or 0
    fell = [b for b in getattr(msg, "content", []) if getattr(b, "type", "") == "fallback"]
    price = next((v for k, v in PRICES.items() if served.startswith(k)), None)
    call = {"asked": asked, "model": served, "input_tokens": tin, "output_tokens": tout,
            "cost": round((tin * price[0] + tout * price[1]) / 1e6, 4) if price else None,
            "fallback": bool(fell) or served != asked}
    if not hasattr(_calls, "log"):
        _calls.log = []
    _calls.log.append(call)
    return call


def drain() -> list[dict]:
    """Calls recorded on this thread since the last drain."""
    out = getattr(_calls, "log", [])
    _calls.log = []
    return out


def describe(calls: list[dict]) -> str:
    """'claude-opus-5-5 · 8.1k in / 3.2k out · ~$0.10' (one entry per model used)."""
    if not calls:
        return ""
    parts = []
    for model in dict.fromkeys(c["model"] for c in calls):
        cs = [c for c in calls if c["model"] == model]
        tin, tout = sum(c["input_tokens"] for c in cs), sum(c["output_tokens"] for c in cs)
        cost = sum(c["cost"] or 0 for c in cs)
        note = f" (fallback from {cs[0]['asked']})" if any(c["fallback"] for c in cs) and model != cs[0]["asked"] else ""
        parts.append(f"{model}{note} · {tin / 1000:.1f}k in / {tout / 1000:.1f}k out" + (f" · ~${cost:.2f}" if cost else ""))
    return "; ".join(parts)


def ask_json(system: str, user: str, schema: dict, cfg: dict, max_tokens: int = 32000) -> dict:
    """Ask Claude for a JSON object matching `schema` (structured outputs)."""
    llm = cfg["llm"]
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError as e:  # no credentials at all
        raise LLMUnavailable(str(e)) from e

    kwargs = dict(
        model=llm["model"],
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={
            "effort": llm.get("effort", "high"),
            "format": {"type": "json_schema", "schema": schema},
        },
    )
    if llm.get("fallbacks", True):
        # Re-run on a fallback model server-side if a safety classifier declines.
        kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")

    print(f"  asking {llm['model']}...", file=sys.stderr)
    try:
        with client.beta.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
    except TypeError as e:  # SDK raises this when no credential source resolves
        if "authentication" not in str(e).lower():
            raise
        raise LLMUnavailable("No Anthropic credentials. Set ANTHROPIC_API_KEY or run `ant auth login`.") from e
    except anthropic.AuthenticationError as e:
        raise LLMUnavailable("Anthropic credentials rejected. Set ANTHROPIC_API_KEY.") from e
    except anthropic.APIConnectionError as e:
        raise LLMUnavailable(f"Could not reach the Anthropic API: {e}") from e

    record(msg, llm["model"])
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"Claude declined this request: {msg.stop_details}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("Response hit max_tokens before finishing; raise max_tokens.")
    text = next(b.text for b in msg.content if b.type == "text")
    return json.loads(text)


def obj(props: dict, required: list[str] | None = None) -> dict:
    """Strict JSON-schema object helper (all props required, no extras)."""
    return {
        "type": "object",
        "properties": props,
        "required": required if required is not None else list(props),
        "additionalProperties": False,
    }
