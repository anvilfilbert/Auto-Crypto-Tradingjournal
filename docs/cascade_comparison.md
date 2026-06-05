# Cascade Comparison — 8 Providers vs Opus 4.7

**Generated:** 2026-05-19 19:32 UTC  
**Baseline:** `claude-opus-4-7`  
**Setups scored:** 12  
**Runs:** 9 (8 provider + 1 baseline)

## Summary — score agreement with baseline

Agreement = |Δ| ≤ 1 from Opus baseline score. Higher = closer to Opus.

| Run | Provider | Avg \|Δ\| | Agree (Δ≤1) | Strong diverge (Δ>3) | Sound trades | Errors | Avg latency |
|---|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | grok | — | — | — | 0/0 | 0/12 | — |
| Grok 3 Mini (X.AI) | grok | — | — | — | 0/0 | 0/12 | — |
| Qwen 3 235B (Cerebras) | cerebras | 1.00 | 3/4 | 0 | 3/4 | 8/12 | 1.8s |
| Llama 3.1 8B (Cerebras) | cerebras | 1.50 | 2/4 | 0 | 3/4 | 8/12 | 1.6s |
| Llama 3.3 70B (Groq) | groq | 0.00 | 1/1 | 0 | 1/1 | 11/12 | 2.4s |
| Llama 4 Scout (Groq) | groq | 1.33 | 3/6 | 0 | 6/6 | 6/12 | 1.7s |
| DeepSeek V4 (OR) | openrouter | 2.38 | 5/8 | 2 | 6/8 | 4/12 | 28.3s |
| Nemotron 120B (OR) | openrouter | — | — | — | 0/0 | 12/12 | — |

## Ranking (closest to Opus baseline)

1. **Llama 3.3 70B (Groq)** (groq) — avg Δ 0.00, sound 1, errors 11, 2.4s
2. **Qwen 3 235B (Cerebras)** (cerebras) — avg Δ 1.00, sound 3, errors 8, 1.8s
3. **Llama 4 Scout (Groq)** (groq) — avg Δ 1.33, sound 6, errors 6, 1.7s
4. **Llama 3.1 8B (Cerebras)** (cerebras) — avg Δ 1.50, sound 3, errors 8, 1.6s
5. **DeepSeek V4 (OR)** (openrouter) — avg Δ 2.38, sound 6, errors 4, 28.3s
6. **Grok 3 (X.AI)** (grok) — avg Δ —, sound 0, errors 0, —
7. **Grok 3 Mini (X.AI)** (grok) — avg Δ —, sound 0, errors 0, —
8. **Nemotron 120B (OR)** (openrouter) — avg Δ —, sound 0, errors 12, —

## Per-setup detail

---
### KERNELUSDT — Long

**Baseline (Opus):** score 6 · entry 0.0638 · SL 0.06149 · TP1 0.0685 · TP2 0.0715 · R:R 2.03

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 6 | — | 0.0638 | 0.06149 | 0.0685 | 0.0715 | 2.03 | ✓ | 9.9s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | 5 | -1 | 0.064 | 0.06337 | 0.066 | 0.068 | 2.50 | ✓ | 2.0s |
| Llama 3.1 8B (Cerebras) | 5 | -1 | 0.0614967 | 0.0600967 | 0.064 | 0.065 | 1.50 | ✓ | 2.2s |
| Llama 3.3 70B (Groq) | 6 | +0 | 0.0615 | 0.0601 | 0.0625 | 0.064 | 2.10 | ✓ | 2.4s |
| Llama 4 Scout (Groq) | 8 | +2 | 0.0632 | 0.0600967 | 0.0655 | 0.0678 | 2.50 | ✓ | 1.7s |
| DeepSeek V4 (OR) | — | — | — | — | — | — | — | — | ERROR |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> 4H shows clean bullish EMA stack with price holding above support cluster at 0.06337, offering a defined structural long with stop below 0.06149 swing support. However, ADX at 14 signals a weak trend and 1D EMA is mixed with negative MACD, capping this at a monitor-grade setup. TP2 at 0.07150 targets the next liquidity pocket above recent highs where stops likely cluster.

---
### SPACEUSDT — Long

**Baseline (Opus):** score 7 · entry 0.00767 · SL 0.00745 · TP1 0.0082 · TP2 0.0088 · R:R 2.41

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 7 | — | 0.00767 | 0.00745 | 0.0082 | 0.0088 | 2.41 | ✓ | 10.5s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.1 8B (Cerebras) | 5 | -2 | 0.006812 | 0.006358 | 0.008 | 0.0095 | 1.50 | ✓ | 2.3s |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | 7 | +0 | 0.0069 | 0.006358 | 0.00745 | 0.0081 | 2.50 | ✓ | 1.8s |
| DeepSeek V4 (OR) | 6 | -1 | 0.00755 | 0.0073 | 0.00792 | 0.00818 | 2.50 | ✓ | 39.5s |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> Both 4H and 1D show clean bullish EMA stacks with exceptionally strong ADX (52.4 on daily), indicating a powerful established uptrend with room to run. RSI is in healthy momentum territory (59-62), not yet overbought, and long bias aligns with the rulebook warning against shorts. Score capped at 7 due to weak confluence (2.1/11.1) and neutral sentiment, but trend strength and support structure justify a measured long entry.

---
### SAHARAUSDT — Long

**Baseline (Opus):** score 7 · entry 0.0342 · SL 0.03355 · TP1 0.0356 · TP2 0.0372 · R:R 4.62

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 7 | — | 0.0342 | 0.03355 | 0.0356 | 0.0372 | 4.62 | ✓ | 12.8s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | 7 | +0 | 0.02235 | 0.0217933 | 0.03355 | 0.03355 | 2.50 | ⚠×1 | 1.6s |
| Llama 3.1 8B (Cerebras) | 5 | -2 | 0.02235 | 0.0217933 | 0.03355 | 0.03355 | 1.50 | ⚠×1 | 0.8s |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | 8 | +1 | 0.024 | 0.0217933 | 0.0265 | 0.0305 | 2.50 | ✓ | 1.8s |
| DeepSeek V4 (OR) | 0 | -7 | 0 | 0 | 0 | 0 | 0.00 | ⚠×1 | 15.2s |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> Daily trend is exceptionally strong (ADX 45.9, full EMA stack) and price sits above a clear structural support at 0.03355, giving a tight invalidation and asymmetric R:R toward the next liquidity pool near 0.0356-0.0372 where prior swing highs rest. The 4H is consolidating (ADX 17.8, MACD flat) so this is a pullback-continuation long rather than a breakout chase, which fits the rulebook preference for multi-hour holds. Score capped at 7 because recent personal performance is deteriorating and 4H momentum lacks ignition — not a 9/10 conviction setup.

---
### HOMEUSDT — Long

**Baseline (Opus):** score 7 · entry 0.0165 · SL 0.01592 · TP1 0.0177 · TP2 0.0188 · R:R 2.07

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 7 | — | 0.0165 | 0.01592 | 0.0177 | 0.0188 | 2.07 | ✓ | 10.0s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | 9 | +2 | 0.01595 | 0.01501 | 0.01725 | 0.0185 | 2.70 | ✓ | 1.9s |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | 9 | +2 | 0.018 | 0.01592 | 0.021 | 0.025 | 2.50 | ✓ | 1.8s |
| DeepSeek V4 (OR) | 7 | +0 | 0.016 | 0.0158 | 0.0163 | 0.0165 | 1.50 | ✓ | 34.7s |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> Trend is unambiguously bullish on both 4H and 1D with clean EMA stack, ADX above 25, and strong confluence score — this favors longs. However, RSI is deeply overbought on both timeframes suggesting a pullback is likely before continuation, and the trader's recent form is poor with shorts and chasing being key weaknesses. A long entry near the 0.01592 support with TP at prior swing liquidity around 0.01770/0.01880 gives a clean 2:1+ R:R while respecting structure.

---
### MUUSDT — Long

**Baseline (Opus):** score 6 · entry 540 · SL 498 · TP1 625 · TP2 710 · R:R 2.02

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 6 | — | 540 | 498 | 625 | 710 | 2.02 | ✓ | 11.5s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | 5 | -1 | 501 | 490.735 | 540 | 560 | 2.60 | ✓ | 1.6s |
| Llama 3.1 8B (Cerebras) | 5 | -1 | 498 | 490.735 | 540 | 570 | 1.50 | ✓ | 1.0s |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | 7 | +1 | 505 | 490.735 | 520 | 540 | 2.50 | ✓ | 1.5s |
| DeepSeek V4 (OR) | — | — | — | — | — | — | — | — | ERROR |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> 1D trend is strongly bullish (full EMA stack, ADX 54) but 4H is in pullback mode below EMA20/50 with negative MACD, so this is a trend-continuation dip-buy at the 540 support shelf. SL placed below the 500/490 support cluster invalidates the structure, with TP1 at prior swing liquidity ~625 and TP2 targeting extension into 710 where breakout buyers' stops likely rest. Score capped at 6 due to weak confluence (0.1/11.1), 4H momentum still bearish, and trader's recent deteriorating performance demanding conservative sizing.

---
### DEXEUSDT — Long

**Baseline (Opus):** score 7 · entry 12.45 · SL 11.43 · TP1 14.5 · TP2 16.55 · R:R 4.02

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 7 | — | 12.45 | 11.43 | 14.5 | 16.55 | 4.02 | ✓ | 10.0s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | — | — | — | — | — | — | — | — | ERROR |
| DeepSeek V4 (OR) | 10 | +3 | 12.5 | 12.26 | 12.86 | 13.1 | 1.50 | ✓ | 32.7s |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> Both 4H and 1D show fully bullish EMA stacks with ADX 34 on daily confirming a strong trending regime — the highest-probability backdrop per the rulebook. Entry near 12.27 support (now flipped) with SL below 11.43 structural support gives a clean ~4:1 R:R to the next liquidity pocket near prior swing highs around 14.50 and 16.55. Score capped at 7 due to deteriorating recent form and RSI nearing stretched territory on the daily.

---
### BSBUSDT — Long

**Baseline (Opus):** score 7 · entry 0.448075 · SL 0.38254 · TP1 0.58 · TP2 0.68 · R:R 3.55

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 7 | — | 0.448075 | 0.38254 | 0.58 | 0.68 | 3.55 | ✓ | 11.4s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | 9 | +2 | 0.38254 | 0.371 | 0.448075 | 0.5 | 2.50 | ✓ | 1.4s |
| DeepSeek V4 (OR) | 7 | +0 | 0.46 | 0.447 | 0.48 | 0.5 | 1.54 | ✓ | 31.7s |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> Both 4H and 1D show fully bullish EMA stacks with ADX near 38, indicating a strong established uptrend with confluence rated Strong Bullish. However, 4H RSI at 90.6 signals extreme overbought conditions, so the highest-quality entry is a pullback to the 0.448 support rather than chasing. R:R of 3.5:1 to the next liquidity pocket justifies a 7 score despite the late-trend entry risk.

---
### FFUSDT — Long

**Baseline (Opus):** score 7 · entry 0.0655 · SL 0.0637 · TP1 0.069 · TP2 0.07538 · R:R 3.49

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 7 | — | 0.0655 | 0.0637 | 0.069 | 0.07538 | 3.49 | ✓ | 9.4s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | — | — | — | — | — | — | — | — | ERROR |
| DeepSeek V4 (OR) | 0 | -7 | 0 | 0 | 0 | 0 | 0.00 | ⚠×1 | 13.9s |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> The daily trend is exceptionally strong (ADX 59.3, full bullish EMA stack) and 4H confirms with ADX 26.7, giving a high-quality trend-following long. SL placed below structural support at 0.063715 yields ~2.7% risk with TP2 at the 0.07538 resistance liquidity pool producing R:R ~3.5:1, satisfying the minimum 1:2 reward rule. Score capped at 7 due to neutral 4H RSI/MACD and recent performance deterioration warning.

---
### NILUSDT — Long

**Baseline (Opus):** score 7 · entry 0.0427 · SL 0.04077 · TP1 0.0462 · TP2 0.0485 · R:R 3.01

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 7 | — | 0.0427 | 0.04077 | 0.0462 | 0.0485 | 3.01 | ✓ | 8.6s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | — | — | — | — | — | — | — | — | ERROR |
| DeepSeek V4 (OR) | 8 | +1 | 0.047 | 0.0459 | 0.04865 | 0.04975 | 1.50 | ✓ | 36.7s |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> Strong daily trend (ADX 47.4) with fully bullish EMA stack on both 4H and 1D provides a clean trend-following long structure. Entry near 0.04270 places SL below the 0.04077 swing support, with TP1 at the 0.04620 prior resistance (liquidity pool of stops above equal highs) yielding ~3:1 R:R. Score capped at 7 due to neutral RSI, weak MACD on 4H, and recent losing streak warning from rulebook.

---
### MUSDT — Long

**Baseline (Opus):** score 6 · entry 3.20238 · SL 3.10084 · TP1 3.40546 · TP2 3.60854 · R:R 2.00

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 6 | — | 3.20238 | 3.10084 | 3.40546 | 3.60854 | 2.00 | ✓ | 8.7s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | — | — | — | — | — | — | — | — | ERROR |
| DeepSeek V4 (OR) | — | — | — | — | — | — | — | — | ERROR |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> Trend is cleanly bullish on both 4H and 1D with full EMA stack and ADX above 20, supporting a long bias from the nearest support cluster at 3.20. However, 4H RSI at 71.6 signals overbought conditions and confluence is only middling at 4.9/11.1, capping conviction at 6. Entry at structural support with SL below the next support layer yields a clean 2:1 R:R to the next liquidity pool above.

---
### EDGEUSDT — Long

**Baseline (Opus):** score 6 · entry 1.2668 · SL 1.2313 · TP1 1.32 · TP2 1.375 · R:R 3.05

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 6 | — | 1.2668 | 1.2313 | 1.32 | 1.375 | 3.05 | ✓ | 10.0s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | — | — | — | — | — | — | — | — | ERROR |
| DeepSeek V4 (OR) | 6 | +0 | 1.2688 | 1.2546 | 1.3 | 1.32 | 2.21 | ✓ | 22.1s |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> Both 4H and 1D show clean bullish EMA stacks with ADX in trending territory, supporting a long bias from the 1.2668 support pivot. R:R of ~3:1 to TP2 at 1.3750 (prior swing liquidity) qualifies under the decision table, but weak confluence (2.8) and a slightly negative 4H MACD keep conviction moderate. Score capped at 6 — monitor/enter small rather than full size given recent account deterioration.

---
### KITEUSDT — Long

**Baseline (Opus):** score 7 · entry 0.208 · SL 0.2041 · TP1 0.22 · TP2 0.235 · R:R 3.08

| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |
|---|---|---|---|---|---|---|---|---|---|
| Baseline (Opus 4.7) | 7 | — | 0.208 | 0.2041 | 0.22 | 0.235 | 3.08 | ✓ | 9.5s |
| Grok 3 (X.AI) | — | — | — | — | — | — | — | — | not run |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — | — | — | not run |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — | — | — | ERROR |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — | — | — | ERROR |
| Llama 4 Scout (Groq) | — | — | — | — | — | — | — | — | ERROR |
| DeepSeek V4 (OR) | — | — | — | — | — | — | — | — | ERROR |
| Nemotron 120B (OR) | — | — | — | — | — | — | — | — | ERROR |

**Baseline reasoning (Opus):**
> Both 4H and 1D show fully bullish EMA stacks with exceptionally strong ADX (43/47) indicating a powerful trend, and confluence score is strongly bullish. TP2 at 0.2350 targets prior swing high liquidity where stops cluster above equal highs, while SL sits just below the 0.2041 structural support. Score capped at 7 due to 1D RSI 79.8 overbought condition and recent deteriorating performance, but R:R >3:1 justifies entry.
