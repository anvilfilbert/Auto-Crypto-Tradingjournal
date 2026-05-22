"""Single AI classification call for debugging — prints whatever error
classify_ai swallows internally."""
import sys, traceback
sys.path.insert(0, "/home/fbauer/trading-journal")

# Reach into the internals so we see the raw exception, not the
# swallowed one.
import setup_classifier as sc
from ai_client import send as ai_send
from constants import FAST_MODEL
from helpers import strip_fence
import json

prompt = sc._AI_PROMPT_TEMPLATE.format(
    symbol="BTCUSDT",
    direction="Long",
    open_time="2026-05-20 12:00:00",
    prompt_text_4h="4H: RSI 65, ADX 30, vol 1.2x. Price above EMA20.",
    prompt_text_1h="1H: Mild pullback to EMA50.",
)

print(f"FAST_MODEL = {FAST_MODEL}")
print("Calling ai_send…")
try:
    raw, cached = ai_send(
        "setup_classifier", FAST_MODEL,
        [{"role": "user", "content": prompt}],
        max_tokens=256,
    )
    print(f"raw response: {raw[:400]}")
    print(f"cached: {cached}")
    data = json.loads(strip_fence(raw.strip()))
    print(f"parsed: {data}")
except Exception:
    print("EXCEPTION:")
    traceback.print_exc()
