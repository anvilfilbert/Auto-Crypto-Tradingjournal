"""
scanner_watchlist.py — Watchlist symbols for the setup scanner.

Provides the default Bitget watchlist and a lazy-loaded Binance watchlist.
Call _get_default_watchlist() to get the merged, deduplicated list.
"""

# 2026-05-26 curation: data-driven tiered watchlist per pick-watchlist-coins
# skill. Methodology: Binance 24h volume as quality gate (leader exchange =
# real price discovery), Bitget listing required for execution feasibility,
# |24h move| < 25% to avoid chasing pumps/catching dumps, exclude
# stables / wrappers / tokenised equities / commodities.
#
# Tier bands (Binance 24h quote volume):
#   T1 ≥ $500M  · T2 $100-500M  · T3 $30-100M (capped at 60 total)
#
# Regenerate with /tmp/curate_v3.py whenever the universe drifts; refresh
# weekly at minimum, or after a notable listing event.
_BITGET_WATCHLIST = [
    # ── OPERATOR OVERRIDES (preserve across regenerations) ──────────────────
    # Hand-picked by the operator regardless of the |24h|<25% pump-filter.
    # When regenerating with /tmp/curate_v3.py, MERGE these back in.
    "WLDUSDT",     # OVERRIDE — operator has positive history with this token
    # ── Tier 1 — Majors (6) ──────────────────────────────────────────────────
    "BTCUSDT",     # T1  $6.80B
    "ETHUSDT",     # T1  $6.27B
    "ZECUSDT",     # T1  $1.57B  (Zcash — re-emerging privacy narrative)
    "HYPEUSDT",    # T1  $1.17B  (Hyperliquid native)
    "NEARUSDT",    # T1  $1.12B
    "SOLUSDT",     # T1  $1.11B
    # ── Tier 2 — Liquid mid-caps (16) ───────────────────────────────────────
    "TONUSDT",     # T2  $428M
    "XRPUSDT",     # T2  $385M
    "BNBUSDT",     # T2  $383M
    "DOGEUSDT",    # T2  $269M
    "TAOUSDT",     # T2  $219M
    "SUIUSDT",     # T2  $218M
    "PHAUSDT",     # T2  $204M
    "ONDOUSDT",    # T2  $190M
    "UBUSDT",      # T2  $183M
    "NILUSDT",     # T2  $167M
    "RENDERUSDT",  # T2  $143M
    "INJUSDT",     # T2  $142M
    "ADAUSDT",     # T2  $129M
    "FETUSDT",     # T2  $111M
    "TRXUSDT",     # T2  $110M
    "GRASSUSDT",   # T2  $106M
    # ── Tier 3 — Narrative / momentum (40) ──────────────────────────────────
    "FILUSDT",     # T3  $99M
    "TIAUSDT",     # T3  $96M
    "AVAXUSDT",    # T3  $96M
    "SAGAUSDT",    # T3  $90M
    "INUSDT",      # T3  $89M
    "LINKUSDT",    # T3  $89M
    "VVVUSDT",     # T3  $88M
    "MUUSDT",      # T3  $87M
    "BEATUSDT",    # T3  $85M
    "ENAUSDT",     # T3  $79M
    "ERAUSDT",     # T3  $68M
    "XANUSDT",     # T3  $67M
    "LITUSDT",     # T3  $67M
    "LABUSDT",     # T3  $66M
    "VIRTUALUSDT", # T3  $62M
    "GENIUSUSDT",  # T3  $62M
    "ASTERUSDT",   # T3  $60M
    "DOTUSDT",     # T3  $60M
    "BCHUSDT",     # T3  $58M
    "EIGENUSDT",   # T3  $57M
    "PENGUUSDT",   # T3  $52M
    "PUMPUSDT",    # T3  $52M
    "LTCUSDT",     # T3  $49M
    "AAVEUSDT",    # T3  $46M
    "UNIUSDT",     # T3  $45M
    "SKYAIUSDT",   # T3  $44M
    "MMTUSDT",     # T3  $44M
    "ICPUSDT",     # T3  $44M
    "TRUMPUSDT",   # T3  $43M
    "DASHUSDT",    # T3  $41M
    "DEXEUSDT",    # T3  $41M
    "FIDAUSDT",    # T3  $40M
    "XMRUSDT",     # T3  $39M
    "XPLUSDT",     # T3  $39M
    "ARBUSDT",     # T3  $38M
    "IOUSDT",      # T3  $36M
    "CHZUSDT",     # T3  $36M
    "APTUSDT",     # T3  $32M
    "ATOMUSDT",    # T3  $32M
    "AZTECUSDT",   # T3  $30M
]

# BINANCE_WATCHLIST: fetched lazily on first scan to avoid blocking startup.
BINANCE_WATCHLIST: list = []
_binance_watchlist_loaded = False


def _get_default_watchlist() -> list:
    """Return merged Bitget+Binance watchlist, fetching Binance on first call."""
    global BINANCE_WATCHLIST, _binance_watchlist_loaded
    if not _binance_watchlist_loaded:
        _binance_watchlist_loaded = True
        try:
            import ccxt_client as _ccxt
            BINANCE_WATCHLIST = _ccxt.get_binance_futures_symbols()
        except Exception:
            BINANCE_WATCHLIST = []
    return list(dict.fromkeys(
        _BITGET_WATCHLIST + [s for s in BINANCE_WATCHLIST if s not in set(_BITGET_WATCHLIST)]
    ))


DEFAULT_WATCHLIST = _BITGET_WATCHLIST  # backward compat; callers should use _get_default_watchlist()


# ── Dynamic watchlist: volume + OI filtered, cached 24h ──────────────────────
_DYNAMIC_TTL = 24 * 3600  # 24 hours in seconds
_dynamic_cache: dict = {"symbols": None, "ts": 0.0}


# Operator-tunable defaults (env-overridable, evaluated at module load).
# 2026-05-26 retune: defaults reduced from 500/3M/1.5M → 80/30M/10M to match
# the data-driven tiered watchlist above (per pick-watchlist-coins skill).
# The curated _BITGET_WATCHLIST already contains 62 symbols all clearing
# these thresholds. Dynamic-feed additions (if env-overridden looser) get
# capped at SCANNER_MAX_SYMBOLS.
#
# Rationale: 314-symbol scans were producing ~27% Stage-2 JSON-parse
# failures + frequent execution drift on pumping alts. A focused 62-symbol
# universe with real Binance liquidity feeds the Stage-3 funnel with higher
# signal-to-noise candidates.
import os as _os
_DEFAULT_MAX_SYMBOLS = int(_os.environ.get("SCANNER_MAX_SYMBOLS",  "80"))
_DEFAULT_MIN_VOL_USD = float(_os.environ.get("SCANNER_MIN_VOL_USD", "30000000"))
_DEFAULT_MIN_OI_USD  = float(_os.environ.get("SCANNER_MIN_OI_USD",  "10000000"))


def _get_dynamic_watchlist(
    max_symbols: int = None,
    min_vol_usd: float = None,
    min_oi_usd: float = None,
) -> list:
    """
    Return up to max_symbols liquid USDT-M symbols, refreshed every 24h.
    Filters: 24h volume >= min_vol_usd AND open interest >= min_oi_usd.
    Falls back to _get_extended_watchlist() on any API error.

    Defaults come from env vars SCANNER_MAX_SYMBOLS (500), SCANNER_MIN_VOL_USD
    (3M), SCANNER_MIN_OI_USD (1.5M). Passing explicit args overrides.
    """
    if max_symbols is None: max_symbols = _DEFAULT_MAX_SYMBOLS
    if min_vol_usd is None: min_vol_usd = _DEFAULT_MIN_VOL_USD
    if min_oi_usd  is None: min_oi_usd  = _DEFAULT_MIN_OI_USD
    import time as _time
    now = _time.time()
    if _dynamic_cache["symbols"] is not None and (now - _dynamic_cache["ts"]) < _DYNAMIC_TTL:
        return _dynamic_cache["symbols"]

    try:
        import ccxt_client
        volume_syms = ccxt_client.get_binance_futures_symbols(min_vol_usd=min_vol_usd)
        if not volume_syms:
            raise RuntimeError("Binance volume fetch returned empty")

        oi_map = ccxt_client.get_binance_oi_map(volume_syms)

        # Filter by OI; symbols missing from OI map are kept (OI fetch is best-effort)
        filtered = [
            s for s in volume_syms
            if oi_map.get(s, min_oi_usd) >= min_oi_usd
        ]

        # Ensure hand-picked Bitget list is always included
        bitget_set = set(_BITGET_WATCHLIST)
        extra = [s for s in filtered if s not in bitget_set]
        merged = list(dict.fromkeys(_BITGET_WATCHLIST + extra))[:max_symbols]

        _dynamic_cache["symbols"] = merged
        _dynamic_cache["ts"] = now
        print(
            f"[Watchlist] Dynamic: {len(merged)} symbols "
            f"(vol>${min_vol_usd/1e6:.0f}M + OI>${min_oi_usd/1e6:.0f}M)",
            flush=True,
        )
        return merged
    except Exception as e:
        print(f"[Watchlist] Dynamic fetch failed: {e} — using extended static list", flush=True)
        return _get_extended_watchlist(max_symbols=max_symbols, min_vol_usd=min_vol_usd)


def _get_extended_watchlist(max_symbols: int = 500, min_vol_usd: float = 3_000_000) -> list:
    """
    Return up to max_symbols USDT futures sorted by liquidity.

    Strategy: Binance top-volume futures (reliable, keyless) merged with the
    hand-picked Bitget list.  Bitget's fetch_tickers() returns spot pairs via
    ccxt, not perpetuals, so we rely on Binance for volume-ranked discovery.

    Falls back to _get_default_watchlist() on any error.
    """
    try:
        import ccxt_client
        # Lower threshold to $3M — gives ~200-300 Binance symbols
        binance_syms = ccxt_client.get_binance_futures_symbols(min_vol_usd=min_vol_usd)
        if not binance_syms:
            raise RuntimeError("Binance returned empty list")

        # Merge: Bitget manual list first (preferred), then Binance additions
        bitget_set = set(_BITGET_WATCHLIST)
        extra      = [s for s in binance_syms if s not in bitget_set]
        merged     = list(dict.fromkeys(_BITGET_WATCHLIST + extra))[:max_symbols]
        print(
            f"[Watchlist] {len(merged)} symbols "
            f"(Bitget manual {len(_BITGET_WATCHLIST)} + Binance {len(extra)} extras, "
            f"vol>${min_vol_usd/1e6:.0f}M)",
            flush=True,
        )
        return merged
    except Exception as e:
        print(f"[Watchlist] Extended fetch failed: {e} — using default list")
        return _get_default_watchlist()
