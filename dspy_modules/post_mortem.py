"""DSPy module for post-mortem classification of closed losing trades.

Mirrors the function of trading.post_mortem._SYSTEM_PROMPT + _build_user_prompt,
but expressed as a DSPy Signature so it can be optionally compiled with
BootstrapFewShot against historical labeled positions.

Usage (bare zero-shot):
    from dspy_modules.post_mortem import PostMortemClassifier
    clf = PostMortemClassifier.load()  # uses compiled artifact if present
    result = clf(trade_dict)
    # result.tag, result.severity, result.reason, result.evidence

Compile (run from CLI when ready to spend training tokens):
    python scripts/dspy_compile_post_mortem.py --min-samples 30
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

import dspy

# Import the canonical taxonomy from trading/post_mortem.py — single source
# of truth. Both modules must agree on the tag vocabulary, otherwise DSPy
# outputs get silently rewritten to "unknown" during persistence.
# Path manipulation needed because dspy_modules/ is a sibling of trading/
# and may be imported via various paths.
_here = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_here)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
from trading.post_mortem import ALLOWED_TAGS  # noqa: E402

ALLOWED_SEVERITIES = ["low", "medium", "high"]

_COMPILED_PATH = os.path.join(
    os.path.dirname(__file__), "compiled", "post_mortem_v1.json"
)


class PostMortemSignature(dspy.Signature):
    """Classify the failure mode of one closed losing crypto-futures trade.

    Pick ONE tag from the allowed taxonomy. Rate severity. Give a one-sentence
    reason and 2-4 concrete evidence points pulled from the trade context.
    Default to `unknown` when the data is genuinely ambiguous — do not invent
    causes from thin signals.
    """

    trade_context: str = dspy.InputField(
        desc="Closed losing trade — symbol/direction/entry/close/PnL/close_reason "
             "/archetype/bear_phase/leverage/timestamps. Plain text key:value lines."
    )

    tag: str = dspy.OutputField(
        # Built lazily at class-construction time; ALLOWED_TAGS is imported
        # from trading/post_mortem.py so both paths use one canonical list.
        desc="One of: " + ", ".join(ALLOWED_TAGS)
    )
    severity: str = dspy.OutputField(
        desc="low | medium | high"
    )
    reason: str = dspy.OutputField(
        desc="One sentence — the causal narrative behind the loss."
    )
    evidence: str = dspy.OutputField(
        desc="2-4 concrete bullet points (comma-separated), each citing a "
             "specific data point from the trade context."
    )


def _build_trade_context(trade: dict) -> str:
    """Render the trade dict into a flat context string for the Signature."""
    parts = [
        f"Symbol: {trade.get('symbol')}",
        f"Direction: {trade.get('direction')}",
        f"Entry: {trade.get('entry_price')}  Close: {trade.get('close_price')}",
        f"TP ladder: {trade.get('tp_levels')}",
        f"Close reason: {trade.get('close_reason')}",
        f"Realized P&L: ${trade.get('realized_pnl')}",
        f"Open: {trade.get('open_time')}  Close: {trade.get('close_time')}",
        f"Archetype: {trade.get('archetype_at_open')}",
        f"Setup type: {trade.get('setup_type')}",
        f"AI score at open: {trade.get('ai_score_at_open')}",
        f"Bear phase: {trade.get('bear_phase_at_open')}",
        f"Leverage: {trade.get('leverage')}",
    ]
    return "\n".join(parts)


class _LoggingLM(dspy.LM):
    """dspy.LM subclass that logs every call to the token_usage table.

    Without this, DSPy-mode calls bypass ai_client.send and their token
    spend is invisible in the cost dashboard. Logs as module name supplied
    at construction time (e.g. "post_mortem_dspy") so cost can be attributed.
    """

    def __init__(self, *args, log_module: str = "dspy", **kwargs):
        super().__init__(*args, **kwargs)
        self._log_module = log_module

    def forward(self, *args, **kwargs):
        result = super().forward(*args, **kwargs)
        self._safe_log_last()
        return result

    async def aforward(self, *args, **kwargs):
        result = await super().aforward(*args, **kwargs)
        self._safe_log_last()
        return result

    def _safe_log_last(self) -> None:
        try:
            if not getattr(self, "history", None):
                return
            entry = self.history[-1]
            # litellm/DSPy usage shape across versions:
            #   {"usage": {"prompt_tokens", "completion_tokens",
            #              "cache_read_input_tokens", "cache_creation_input_tokens"}}
            usage = (entry.get("usage") if isinstance(entry, dict) else None) or {}
            in_tok = int(usage.get("prompt_tokens") or 0)
            out_tok = int(usage.get("completion_tokens") or 0)
            cache_r = int(usage.get("cache_read_input_tokens") or 0)
            cache_w = int(usage.get("cache_creation_input_tokens") or 0)
            if in_tok == 0 and out_tok == 0:
                return  # no usage info — skip rather than write zero row
            # Strip the "anthropic/" provider prefix DSPy adds; dashboards
            # already key on bare model IDs.
            model = (self.model or "").split("/", 1)[-1]
            from token_log import log_token_usage  # local import — avoid circular
            log_token_usage(self._log_module, model, in_tok, out_tok, cache_r, cache_w)
        except Exception:
            # Never let logging break the calling path.
            pass


def _configure_lm() -> None:
    """Point DSPy at our existing Anthropic Haiku via the SDK directly.

    Reads ANTHROPIC_API_KEY from env. Uses the _LoggingLM subclass so every
    DSPy call is logged in token_usage (module='post_mortem_dspy').
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot configure DSPy LM")
    lm = _LoggingLM(
        "anthropic/claude-haiku-4-5-20251001",
        api_key=api_key,
        max_tokens=400,
        log_module="post_mortem_dspy",
    )
    dspy.settings.configure(lm=lm)


class PostMortemClassifier:
    """Thin wrapper — either bare Predict or compiled program."""

    def __init__(self, program: Optional[dspy.Module] = None):
        if program is None:
            program = dspy.Predict(PostMortemSignature)
        self._program = program

    @classmethod
    def load(cls, compiled_path: str = _COMPILED_PATH) -> "PostMortemClassifier":
        """Load compiled program if available; else fall back to bare Predict."""
        _configure_lm()
        if os.path.isfile(compiled_path):
            try:
                program = dspy.Predict(PostMortemSignature)
                program.load(compiled_path)
                return cls(program)
            except Exception as e:
                # Compiled artifact exists but won't load — fall back cleanly
                import logging
                logging.getLogger(__name__).warning(
                    "compiled program load failed (%s); using bare Predict", e
                )
        return cls()

    def __call__(self, trade: dict) -> dict[str, Any]:
        """Classify one trade. Returns a normalized result dict."""
        ctx = _build_trade_context(trade)
        try:
            pred = self._program(trade_context=ctx)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        tag = (getattr(pred, "tag", "") or "").strip()
        if tag not in ALLOWED_TAGS:
            tag = "unknown"
        severity = (getattr(pred, "severity", "low") or "low").strip().lower()
        if severity not in ALLOWED_SEVERITIES:
            severity = "low"
        reason = (getattr(pred, "reason", "") or "")[:500]
        evidence_raw = getattr(pred, "evidence", "") or ""
        # Accept either a comma-separated string or a JSON-ish list
        if evidence_raw.startswith("["):
            try:
                evidence = json.loads(evidence_raw)
            except Exception:
                evidence = [s.strip() for s in evidence_raw.strip("[]").split(",")]
        else:
            evidence = [s.strip() for s in evidence_raw.split(",") if s.strip()]

        return {
            "ok": True,
            "tag": tag,
            "severity": severity,
            "reason": reason,
            "evidence": evidence[:6],
        }
