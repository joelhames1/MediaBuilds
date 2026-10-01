"""Thin Claude wrapper: one structured-output call that returns parsed JSON."""

from __future__ import annotations

import json
import sys

import anthropic


class LLMUnavailable(RuntimeError):
    pass


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
