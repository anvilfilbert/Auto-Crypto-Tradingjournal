"""Trace scanner_quick stable_prefix size + verify what goes to Anthropic."""
import sys
sys.path.insert(0, "/home/fbauer/trading-journal")

from constants import PROMPT_CACHE_MIN_CHARS
from helpers import build_cached_messages
from scanner_prompts import _build_scanner_stable
from scanner_criteria import CRITERIA_DEFAULTS
from database import db_conn
import ai_rulebook

with db_conn() as conn:
    rulebook_str = ai_rulebook.get_rulebook_for_prompt(conn)
print(f"rulebook_str length: {len(rulebook_str or '')} chars")

stable = _build_scanner_stable(rulebook_str or "", 7, CRITERIA_DEFAULTS)
print(f"stable_prefix length: {len(stable)} chars (≈ {len(stable)//4} tokens)")
print(f"PROMPT_CACHE_MIN_CHARS threshold: {PROMPT_CACHE_MIN_CHARS}")
print(f"clears threshold? {'YES' if len(stable) >= PROMPT_CACHE_MIN_CHARS else 'NO'}")

# What does build_cached_messages actually produce?
msgs = build_cached_messages(
    context="MARKET CONTEXT:\nVIX 17, F&G 28, regime risk-on",
    prompt="Score this LONG setup for ATESTUSDT…",
    stable_prefix=stable,
)
print()
print("=== built messages ===")
for m in msgs:
    for blk in m["content"]:
        has_cc = "cache_control" in blk
        print(f"  type={blk['type']}  len={len(blk.get('text',''))}  cache_control={has_cc}")

# Sanity check the prompt fragment sizes
from prompt_fragments import (
    SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES, DRAW_ON_LIQUIDITY_RULES,
)
print()
print("=== fragment sizes ===")
print(f"  SCORING_SCALE:           {len(SCORING_SCALE)}")
print(f"  LEVEL_PROXIMITY_RULES:   {len(LEVEL_PROXIMITY_RULES)}")
print(f"  MARKET_CONTEXT_RULES:    {len(MARKET_CONTEXT_RULES)}")
print(f"  DRAW_ON_LIQUIDITY_RULES: {len(DRAW_ON_LIQUIDITY_RULES)}")
try:
    from scanner_prompts import SCANNER_PLAYBOOK
    print(f"  SCANNER_PLAYBOOK:        {len(SCANNER_PLAYBOOK)}")
except Exception as e:
    print(f"  SCANNER_PLAYBOOK: ERR {e}")
