#!/usr/bin/env bash
# check_bias_fix_impact.sh
#
# Pre-staged review queries for the 2026-06-01 Long-bias fix.
# Run on Pi: bash /home/fbauer/trading-journal/scripts/check_bias_fix_impact.sh
#
# What this measures:
#   1. Stage 3a Haiku Short pass rate (target: >10% post-fix; was 1.9% pre-fix)
#   2. consensus_approved direction split last 24h
#   3. Any Short positions opened
#   4. Cost impact of the changes (compare 24h before fix vs after)
#   5. Recent bear_phase classifier outputs (with F&G paused)

cd /home/fbauer/trading-journal || exit 1

PRE_FIX_LONG_PASS=76.4
PRE_FIX_SHORT_PASS=1.9
FIX_TS='2026-06-01 00:55:00'  # approximate restart time after Step 1-6 deploy

echo "================================================"
echo " BIAS-FIX IMPACT REVIEW (Steps 1-6 of 2026-06-01)"
echo "================================================"
echo ""

echo "=== 1. Stage 3a Haiku funnel — 24h post-fix ==="
echo "Pre-fix baseline: Long ${PRE_FIX_LONG_PASS}% / Short ${PRE_FIX_SHORT_PASS}%"
sudo journalctl -u trading-journal --since '24 hours ago' --no-pager 2>/dev/null \
  | grep 'Stage3a (Haiku) funnel' \
  | awk '
    {
      for (i=1; i<=NF; i++) {
        if ($i == "Long")  { split($(i+1), a, "/"); long_pass += a[1]; long_total += a[2] }
        if ($i == "Short") { split($(i+1), s, "/"); short_pass += s[1]; short_total += s[2] }
      }
    }
    END {
      printf "Post-fix Long:  %d/%d = %.1f%%\n", long_pass, long_total, (long_total ? 100.0*long_pass/long_total : 0)
      printf "Post-fix Short: %d/%d = %.1f%%\n", short_pass, short_total, (short_total ? 100.0*short_pass/short_total : 0)
    }
  '
echo ""

echo "=== 2. consensus_approved direction split last 24h ==="
sqlite3 -header -column trading_journal.db "
SELECT
  date(ts) AS day,
  SUM(CASE WHEN payload_json LIKE '%\"direction\": \"Long\"%' THEN 1 ELSE 0 END) AS approved_longs,
  SUM(CASE WHEN payload_json LIKE '%\"direction\": \"Short\"%' THEN 1 ELSE 0 END) AS approved_shorts
FROM futures_ai_log
WHERE event='consensus_approved'
  AND ts > datetime('now','-24 hours')
GROUP BY day ORDER BY day DESC;"
echo ""

echo "=== 3. Short positions opened (any time) ==="
sqlite3 -header -column trading_journal.db "
SELECT id, symbol, direction, open_time, ai_score_at_open, ROUND(realized_pnl, 2) AS pnl
FROM positions
WHERE chain='auto_ai' AND direction='Short'
ORDER BY id DESC LIMIT 10;"
echo ""

echo "=== 4. Cost — last 30min token_usage by module ==="
sqlite3 -header -column trading_journal.db "
SELECT module, COUNT(*) AS calls,
       SUM(input_tokens) AS in_tok,
       SUM(cached_tokens) AS cached,
       SUM(cache_creation_tokens) AS cache_create,
       SUM(output_tokens) AS out_tok
FROM token_usage WHERE ts > datetime('now','-30 minutes')
GROUP BY module ORDER BY in_tok DESC;"
echo ""

echo "=== 5. bear_phase outputs (last 10 from journal) ==="
sudo journalctl -u trading-journal --since '24 hours ago' --no-pager 2>/dev/null \
  | grep -E 'bear-phase mod applied|bear-phase:' | tail -10
echo ""

echo "=== 6. Recent rejected_consensus reasons (for Shorts specifically) ==="
sqlite3 trading_journal.db "
SELECT id, ts, substr(payload_json, 1, 250) AS payload
FROM futures_ai_log
WHERE event='consensus_rejected'
  AND payload_json LIKE '%\"direction\": \"Short\"%'
  AND ts > datetime('now','-24 hours')
ORDER BY id DESC LIMIT 5;"
echo ""

echo "=== 7. Shadow stats post-fix ==="
curl -s http://localhost:8082/api/shadow-stats?days=1 \
  | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    data = d.get('data', d)
    rows = data.get('by_model', [])
    print(f'  shadow samples: {sum(r[\"samples\"] for r in rows)}')
    for r in rows:
        print(f'    {r[\"shadow_model\"]:50s} samples={r[\"samples\"]:4d} success={r[\"success_pct\"]:5.1f}% mean_lat={r[\"mean_lat_ms\"]}ms cost_ratio={r.get(\"cost_ratio_vs_primary\")}')
except Exception as e:
    print(f'  error: {e}')
"
echo ""

echo "=============================================="
echo "INTERPRETATION GUIDE:"
echo "  - Short pass rate >10%: prompt fixes worked. Next: monitor real outcomes."
echo "  - Short pass rate 5-10%: partial fix. Investigate chart_confluence.py + Stage 2 filters."
echo "  - Short pass rate <5%: structural issue remains. Step 7 (chart_confluence audit) needed."
echo "=============================================="
