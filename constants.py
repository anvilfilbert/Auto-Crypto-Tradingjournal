import os

VERSION                = "1.6.0"

# KNOWLEDGE_VERSION — bumps whenever we change a calibration that affects
# how stored data (hindsight verdicts, setup_type labels, rulebook rules,
# etc.) was computed. Subsystems record this version with their outputs
# so /api/system/health can flag historical rows that need re-running
# under the current logic. Format: YYYY-MM-DD.N (N counts within-day
# bumps). Bump when you change ENTER_THRESHOLD, classifier taxonomy,
# TP/SL multipliers, score caps, or the rulebook prompt.
KNOWLEDGE_VERSION      = "2026-05-23.1"   # trader-sheet wave + close_reason + BE fix + hedge-mode close

# ── Anthropic models ──────────────────────────────────────────────────────────
ANTHROPIC_API_KEY      = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL                  = "claude-sonnet-4-6"
FAST_MODEL             = "claude-haiku-4-5-20251001"

# ── Cache TTLs (seconds) ──────────────────────────────────────────────────────
# CHART_CACHE_TTL reduced 600→120 (2026-05-26) to shrink the scan-to-fill
# window. Bitget's klines endpoint returns the in-progress bar so close.iloc[-1]
# is effectively live AT FETCH TIME — but a stale 10-min cache could surface
# 10-min-old prices to the scanner. 2 min keeps the scanner fresh without
# hitting Bitget too often (8 scans/hour × 80 symbols = 640 candle requests).
CHART_CACHE_TTL        = 120    # 2 min — candle cache in chart_context
SCANNER_CACHE_TTL      = 1800   # 30 min — scanner result cache
MARKET_CACHE_TTL       = 300    # 5 min  — Fear & Greed / funding rates
NANSEN_CACHE_TTL       = 1800   # 30 min — Nansen smart money cache

# ── Accuracy tracking ─────────────────────────────────────────────────────────
ACCURACY_TARGET        = 35     # calls needed for 85% statistical confidence

# ── Scanner pipeline ──────────────────────────────────────────────────────────
SCANNER_MIN_SCORE         = 6   # restored from 7 → 6 (2026-05-26): the original
                                # 50/50 finding predates the safety layers added
                                # since — low_conviction archetype gate, Path 3
                                # R:R viability check, pre-flight drift guard,
                                # tiered Opus sizing (score=5 → half-size). A
                                # score-6 setup that survives ALL of these is a
                                # different distribution than the unsupervised
                                # 50/50 trades that informed the original raise.
                                # Re-evaluate at n ≥ 30 closed score-6 auto_ai
                                # trades; revert if WR < 45%.
# SCANNER_FULL_DETAIL_TOP_N reduced 6→3 (2026-05-26) to cut Stage-3 LLM time.
# At 80-symbol curated watchlist, Top-3 covers the genuinely-elite setups
# without burning 4+ min on borderline marginal ones. Trade-off: a setup
# scored just outside the top-3 by the Haiku quick-score may miss Sonnet's
# deeper read. Acceptable because the bottom-3 are usually low-conviction.
SCANNER_FULL_DETAIL_TOP_N = 3   # was 6 — see comment above
SCANNER_MAX_WORKERS       = 4   # ThreadPoolExecutor — tuned to Pi 4-core CPU

# ── Position sizing ───────────────────────────────────────────────────────────
DEFAULT_LEVERAGE         = 10
DEFAULT_RISK_PCT         = 1.0
DEFAULT_DCA_RISK_PCT     = 2.0
FALLBACK_EQUITY_USDT     = 1000.0  # only when ALL exchange equity calls fail

# ── Prompt budget ─────────────────────────────────────────────────────────────
MAX_CONTEXT_CHARS        = 5_600
PROMPT_CACHE_MIN_CHARS   = 4_096   # Anthropic cache_control minimum

# ── Chart S/R & trendline tolerance ──────────────────────────────────────────
PRICE_TOLERANCE          = 0.004   # 0.4% — S/R clustering and trendline validation

# ── Google Gemini ─────────────────────────────────────────────────────────────
GEMINI_FAST_MODEL        = "gemini-2.0-flash"       # pre-proof, scanner consensus
GEMINI_MODEL             = "gemini-2.5-flash"        # deep analysis (configurable)
GEMINI_CACHE_TTL         = 1800    # 30 min — same as scanner cycle

# ── Consensus scoring thresholds ─────────────────────────────────────────────
CONSENSUS_HIGH_DELTA     = 1       # |claude - gemini| ≤ 1 → high confidence
CONSENSUS_MED_DELTA      = 2       # ≤ 2 → medium
CONSENSUS_LOW_DELTA      = 3       # ≤ 3 → low (Claude 60% weight)
                                   # > 3 → very_low / REVIEW flag

# ── Trade monitor background thread ──────────────────────────────────────────
MONITOR_INTERVAL           = int(os.environ.get("MONITOR_INTERVAL",   "600"))   # 10 min
MONITOR_THRESHOLD_PCT      = float(os.environ.get("MONITOR_THRESHOLD_PCT", "-5.0"))
MONITOR_THRESHOLD_DURATION = int(os.environ.get("MONITOR_THRESHOLD_DURATION", "240"))

# v1.6.0 feature constants
LIQ_PROXIMITY_PCT  = 0.03    # liquidation wall proximity threshold (3%)
LIQ_TTL            = 900     # 15-min cache for liquidation clusters
ONCHAIN_TTL        = 3600    # 1-h cache for on-chain daily metrics
REGIME_TTL         = 14400   # 4-h retrain window for HMM
ML_SCORER_TTL      = 86400   # 24-h retrain interval for XGBoost scorer
ML_MIN_SAMPLES     = 20      # min labeled outcomes to activate ML scorer
