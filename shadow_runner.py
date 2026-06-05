"""shadow_runner.py — Fire-and-forget shadow calls to alt-models for comparison.

When ai_client.send() runs an Anthropic call for a module in AI_SHADOW_MODULES,
it samples (rate = AI_SHADOW_RATE) and dispatches the same prompt to each
configured shadow model in a background thread. Results land in
shadow_responses table for offline cost/agreement analysis.

The shadow path never blocks the primary call's return or affects auto-trader
decisions — primary response is returned synchronously; shadows fire in a
daemon thread, log their results, exit.

Env vars (Pi .env):
    AI_SHADOW_MODULES   — comma-separated module names to shadow (e.g. "scanner_quick")
    AI_SHADOW_RATE      — float 0.0-1.0 (default 0.0 = disabled)
    AI_SHADOW_MODELS    — comma-separated provider:model entries, e.g.
                          "openrouter:deepseek/deepseek-v3.2,openrouter:meta-llama/llama-3.3-70b-instruct:free,openrouter:google/gemini-2.0-flash-001,openrouter:qwen/qwen3.6-flash"

Set AI_SHADOW_RATE=0 (or unset) to disable cleanly — no overhead, no logging.
"""
import logging
import os
import random
import re
import threading
import time
import uuid
from typing import Optional

_log = logging.getLogger(__name__)

# Extracts the trading symbol from the shadowed prompt so future analytics
# can join shadow_responses → paper_positions / positions by symbol+ts.
# scanner_quick prompts have the form:
#   "Score this LONG setup for BTCUSDT — return score..."
# Falls back to the generic SYMBOLUSDT pattern when the "for" form isn't found.
_SYM_FOR_RE  = re.compile(r"\bfor\s+([A-Z][A-Z0-9]{1,9}USDT)\b")
_SYM_BARE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}USDT)\b")


def _extract_symbol_from_messages(messages: list) -> Optional[str]:
    """Best-effort symbol extraction from a shadowed prompt. Returns None
    when no clean USDT pair is found in the message bodies."""
    if not messages:
        return None
    try:
        for m in messages:
            content = m.get("content") if isinstance(m, dict) else None
            if not content:
                continue
            # content can be a string or a list of {type:text, text:...}
            if isinstance(content, list):
                texts = [c.get("text") or "" for c in content if isinstance(c, dict)]
                text = "\n".join(texts)
            else:
                text = str(content)
            hit = _SYM_FOR_RE.search(text) or _SYM_BARE_RE.search(text)
            if hit:
                return hit.group(1)
    except Exception:
        pass
    return None

# Pricing per 1M tokens (input, output). Used for shadow_cost_usd estimate.
# Update as OpenRouter pricing shifts — these are the 2026-05-31 snapshot.
_SHADOW_PRICING = {
    "deepseek/deepseek-v3.2":                       (0.252, 0.378),
    "deepseek/deepseek-chat-v3.1":                  (0.21,  0.79),
    "meta-llama/llama-3.3-70b-instruct:free":       (0.0,   0.0),
    "meta-llama/llama-3.3-70b-instruct":            (0.10,  0.32),
    "google/gemini-2.0-flash-001":                  (0.10,  0.40),
    "google/gemini-2.5-flash-lite":                 (0.10,  0.40),
    "qwen/qwen3.6-flash":                           (0.1875, 1.125),
}


def _env_bool(name: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(name, default))
    except (ValueError, TypeError):
        return default


def is_shadow_enabled(module: str) -> bool:
    """Return True if this module should fire shadow calls on this invocation."""
    rate = _env_bool("AI_SHADOW_RATE", 0.0)
    if rate <= 0.0:
        return False
    modules = {m.strip() for m in os.environ.get("AI_SHADOW_MODULES", "").split(",") if m.strip()}
    if module not in modules:
        return False
    models = os.environ.get("AI_SHADOW_MODELS", "").strip()
    if not models:
        return False
    return random.random() < rate


def _parse_shadow_specs() -> list[tuple[str, str]]:
    """Parse AI_SHADOW_MODELS env var into list of (provider, model_id) tuples.

    Note: model IDs may contain ':' (e.g. 'llama-3.3-70b-instruct:free').
    We split on the FIRST ':' only.
    """
    raw = os.environ.get("AI_SHADOW_MODELS", "").strip()
    if not raw:
        return []
    out = []
    for spec in raw.split(","):
        spec = spec.strip()
        if not spec or ":" not in spec:
            continue
        provider, model = spec.split(":", 1)
        out.append((provider.strip(), model.strip()))
    return out


def _estimate_cost(model: str, in_tok: int, out_tok: int) -> float:
    """Estimate USD cost from token counts using _SHADOW_PRICING."""
    if model in _SHADOW_PRICING:
        in_p, out_p = _SHADOW_PRICING[model]
    else:
        # Unknown model — log 0 so it's flagged but doesn't crash analysis
        return 0.0
    return round(in_tok * in_p / 1e6 + out_tok * out_p / 1e6, 6)


def _log_shadow_row(row: dict) -> None:
    """Insert one shadow_responses row. Best-effort — never raises.
    `symbol` column lets the Model Comparison page join shadow scores to
    paper_positions / positions by symbol+ts."""
    try:
        from database import db_conn
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO shadow_responses "
                "(primary_request_id, primary_module, primary_model, primary_text, "
                " primary_input_tokens, primary_output_tokens, primary_latency_ms, "
                " shadow_provider, shadow_model, shadow_text, shadow_latency_ms, "
                " shadow_input_tokens, shadow_output_tokens, shadow_cost_usd, shadow_error, "
                " symbol) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["primary_request_id"], row["primary_module"], row["primary_model"],
                    row["primary_text"], row["primary_input_tokens"], row["primary_output_tokens"],
                    row["primary_latency_ms"],
                    row["shadow_provider"], row["shadow_model"], row.get("shadow_text"),
                    row.get("shadow_latency_ms"), row.get("shadow_input_tokens"),
                    row.get("shadow_output_tokens"), row.get("shadow_cost_usd"),
                    row.get("shadow_error"),
                    row.get("symbol"),
                )
            )
            conn.commit()
    except Exception as e:
        _log.warning("shadow log insert failed: %s", e)


def _run_one_shadow(provider: str, shadow_model: str, primary_record: dict,
                     messages: list, max_tokens: int, system: Optional[str]) -> None:
    """Fire one shadow call to one alt-model, log result. Best-effort."""
    from ai_client import _messages_to_prompt  # reuse the existing helper
    prompt, sys_text = _messages_to_prompt(messages, system)
    t0 = time.time()
    text = None
    err = None
    in_tok = len(prompt) // 4  # rough estimate; OR doesn't return token counts uniformly
    out_tok = 0
    try:
        if provider == "openrouter":
            import openrouter_client
            text = openrouter_client.send_text(
                prompt, system=sys_text, max_tokens=max_tokens, model=shadow_model
            )
        elif provider == "gemini":
            import gemini_client
            text = gemini_client.send_text(prompt, system=sys_text, max_tokens=max_tokens)
        elif provider == "cerebras":
            import cerebras_client
            text = cerebras_client.send_text(prompt, system=sys_text, max_tokens=max_tokens, model=shadow_model)
        elif provider == "groq":
            import groq_client
            text = groq_client.send_text(prompt, system=sys_text, max_tokens=max_tokens, model=shadow_model)
        else:
            err = f"unknown provider: {provider}"
        if text:
            out_tok = len(text) // 4
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:300]}"
        _log.warning("shadow call %s:%s failed: %s", provider, shadow_model, err)

    latency_ms = int((time.time() - t0) * 1000)
    cost = _estimate_cost(shadow_model, in_tok, out_tok) if text else 0.0

    _log_shadow_row({
        **primary_record,
        "shadow_provider":      provider,
        "shadow_model":         shadow_model,
        "shadow_text":          (text[:8000] if text else None),  # cap for storage
        "shadow_latency_ms":    latency_ms,
        "shadow_input_tokens":  in_tok,
        "shadow_output_tokens": out_tok,
        "shadow_cost_usd":      cost,
        "shadow_error":         err,
    })


def fire_shadows(module: str, primary_model: str, primary_text: str,
                  primary_input_tokens: int, primary_output_tokens: int,
                  primary_latency_ms: int, messages: list, max_tokens: int,
                  system: Optional[str]) -> None:
    """Dispatch shadow calls to each configured alt-model in a daemon thread.

    Returns immediately — never blocks the primary call's return. The caller
    should already have decided to shadow (via is_shadow_enabled).
    """
    specs = _parse_shadow_specs()
    if not specs:
        return

    primary_request_id = uuid.uuid4().hex
    primary_record = {
        "primary_request_id":    primary_request_id,
        "primary_module":        module,
        "primary_model":         primary_model,
        "primary_text":          primary_text[:8000] if primary_text else None,
        "primary_input_tokens":  primary_input_tokens,
        "primary_output_tokens": primary_output_tokens,
        "primary_latency_ms":    primary_latency_ms,
        # Extracted once per dispatch and inherited by each shadow row, so the
        # Model Comparison page can join shadow scores to paper_positions.
        "symbol":                _extract_symbol_from_messages(messages),
    }

    def _runner():
        for provider, shadow_model in specs:
            _run_one_shadow(provider, shadow_model, primary_record,
                            messages, max_tokens, system)

    t = threading.Thread(target=_runner, daemon=True, name=f"shadow:{module}")
    t.start()
