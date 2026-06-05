# Cascade Comparison — Merged Final Report

Baseline (Opus 4.7) data merged from earlier `cascade_comparison.md` (20:50 UTC) into the partial-providers data from the v2 run (21:19 UTC).

## Provider ranking — closest to Opus 4.7

| Rank | Provider | n | Avg |Δ| | Agree (Δ≤1) | Diverge (Δ>3) | Sound | Score |
|---|---|---|---|---|---|---|---|
| 1 | Llama 3.3 70B (Groq) | 1/12 | 0.00 | 1/1 | 0/1 | 1/1 | 6.70 |
| 2 | Qwen 3 235B (Cerebras) | 4/12 | 1.00 | 3/4 | 0/4 | 4/4 | 6.60 |
| 3 | Llama 4 Scout (Groq) | 6/12 | 1.33 | 4/6 | 0/6 | 6/6 | 6.87 |
| 4 | Llama 3.1 8B (Cerebras) | 5/12 | 1.60 | 2/5 | 0/5 | 4/5 | 6.00 |
| 5 | DeepSeek V4 (OR) | 9/12 | 3.33 | 5/9 | 4/9 | 5/9 | 4.57 |

**No data:** Grok 3 (X.AI), Grok 3 Mini (X.AI), Nemotron 120B (OR)


## Per-setup detail (provider scores vs Opus baseline)

### BSBUSDT
**Opus baseline:** score 7 · entry 0.45317 · SL 0.44298 · TPs 0.47355/0.49393 · R:R 2.00

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | — | — | — | — | — | — |
| DeepSeek V4 (OR) | 0 | -7 | 0 | 0 | 0.00 | ⚠×1 |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### DEXEUSDT
**Opus baseline:** score 7 · entry 12.45 · SL 11.43 · TPs 14.5/15.95 · R:R 2.01

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | 8 | +1 | 11.4343 | 11.162 | 2.50 | ✓ |
| DeepSeek V4 (OR) | — | — | — | — | — | — |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### EDGEUSDT
**Opus baseline:** score 6 · entry 1.273 · SL 1.254 · TPs 1.311/1.349 · R:R 2.00

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | — | — | — | — | — | — |
| DeepSeek V4 (OR) | 6 | +0 | 1.27 | 1.2545 | 1.50 | ✓ |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### FFUSDT
**Opus baseline:** score 7 · entry 0.077 · SL 0.07538 · TPs 0.0798/0.0825 · R:R 2.78

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | — | — | — | — | — | — |
| DeepSeek V4 (OR) | 0 | -7 | 0 | 0 | 0.00 | ⚠×1 |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### HOMEUSDT
**Opus baseline:** score 7 · entry 0.01605 · SL 0.01501 · TPs 0.0181/0.02 · R:R 2.55

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | 9 | +2 | 0.01595 | 0.01501 | 3.20 | ✓ |
| Llama 3.1 8B (Cerebras) | 5 | -2 | 0.01492 | 0.01464 | 1.75 | ✓ |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | 9 | +2 | 0.0153 | 0.01464 | 2.50 | ✓ |
| DeepSeek V4 (OR) | 0 | -7 | 0 | 0 | 0.00 | ⚠×1 |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### KERNELUSDT
**Opus baseline:** score 6 · entry 0.0638 · SL 0.06149 · TPs 0.0675/0.071 · R:R 3.12

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | 5 | -1 | 0.064 | 0.06337 | 2.50 | ✓ |
| Llama 3.1 8B (Cerebras) | 5 | -1 | 0.0614967 | 0.0600967 | 1.50 | ✓ |
| Llama 3.3 70B (Groq) | 6 | +0 | 0.0615 | 0.0601 | 2.10 | ✓ |
| Llama 4 Scout (Groq) | 8 | +2 | 0.061 | 0.0600967 | 2.50 | ✓ |
| DeepSeek V4 (OR) | 5 | -1 | 0.0635 | 0.0628 | 1.57 | ✓ |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### KITEUSDT
**Opus baseline:** score 7 · entry 0.208 · SL 0.2041 · TPs 0.22/0.235 · R:R 3.08

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | — | — | — | — | — | — |
| DeepSeek V4 (OR) | 8 | +1 | 0.205 | 0.203 | 2.50 | ✓ |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### MUSDT
**Opus baseline:** score 6 · entry 3.20238 · SL 3.10084 · TPs 3.40546/3.55777 · R:R 2.50

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | — | — | — | — | — | — |
| DeepSeek V4 (OR) | 5 | -1 | 3.25 | 3.2 | 1.50 | ✓ |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### MUUSDT
**Opus baseline:** score 6 · entry 545 · SL 538.5 · TPs 558/572 · R:R 2.00

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — |
| Llama 3.1 8B (Cerebras) | 5 | -1 | 500.82 | 490.735 | 2.25 | ✓ |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | 7 | +1 | 520 | 500.82 | 2.50 | ✓ |
| DeepSeek V4 (OR) | 0 | -6 | 0 | 0 | 0.00 | ⚠×1 |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### NILUSDT
**Opus baseline:** score 7 · entry 0.0468 · SL 0.0462 · TPs 0.0482/0.0495 · R:R 2.33

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | — | — | — | — | — | — |
| Llama 3.1 8B (Cerebras) | — | — | — | — | — | — |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | — | — | — | — | — | — |
| DeepSeek V4 (OR) | — | — | — | — | — | — |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### SAHARAUSDT
**Opus baseline:** score 7 · entry 0.02295 · SL 0.02235 · TPs 0.0245/0.0268 · R:R 2.58

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | 7 | +0 | 0.02245 | 0.0217933 | 2.50 | ✓ |
| Llama 3.1 8B (Cerebras) | 5 | -2 | 0.02235 | 0.0217933 | 1.50 | ⚠×1 |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | 8 | +1 | 0.024 | 0.0217933 | 2.50 | ✓ |
| DeepSeek V4 (OR) | — | — | — | — | — | — |
| Nemotron 120B (OR) | — | — | — | — | — | — |

### SPACEUSDT
**Opus baseline:** score 7 · entry 0.00768 · SL 0.00745 · TPs 0.00815/0.0086 · R:R 4.00

| Provider | Score | Δ | Entry | SL | R:R | Sound |
|---|---|---|---|---|---|---|
| Grok 3 (X.AI) | — | — | — | — | — | — |
| Grok 3 Mini (X.AI) | — | — | — | — | — | — |
| Qwen 3 235B (Cerebras) | 8 | +1 | 0.00745 | 0.006812 | 2.60 | ✓ |
| Llama 3.1 8B (Cerebras) | 5 | -2 | 0.006812 | 0.006358 | 1.50 | ✓ |
| Llama 3.3 70B (Groq) | — | — | — | — | — | — |
| Llama 4 Scout (Groq) | 8 | +1 | 0.0069 | 0.006358 | 2.50 | ✓ |
| DeepSeek V4 (OR) | 7 | +0 | 0.0079 | 0.00745 | 3.56 | ✓ |
| Nemotron 120B (OR) | — | — | — | — | — | — |
