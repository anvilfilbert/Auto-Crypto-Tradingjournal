# Auto-AI Open Questions — Operator Answers Inline

**Status:** ✅ ANSWERED 2026-05-31 (chat) · master plan generated as `AUTO_AI_MASTER_PLAN.md`

## Consolidated answers (2026-05-31)

| # | Topic | Answer | Notes / reminders attached |
|---|---|---|---|
| Q1 | Auto-Suggest vs Auto mode | Full auto (recommendation) | — |
| Q2 | Learner-pause DD trigger | -8% pauses learner only | — |
| Q3 | Learning Phase scope | L-0 through L-5 | — |
| Q4 | Backtest validation mandatory | Yes | A-B blocks L-3+ |
| Q5 | Smallest proof first | L-0 + per-symbol learner | ⏰ daily status update |
| Q6 | Red-Team first | Yes, ship A-A first | — |
| Q7 | Agents cost ceiling | $5/day combined | — |
| Q8 | Framework | Raw Anthropic SDK | — |
| Q9 | Red-Team veto: soft | Soft for first 2 weeks | ⏰ review at +14d for soft→hard switch; in daily report |
| Q10 | Strategy Selector | Defer | ⏰ later review (after L-3) |
| Q11 | Persistence bars | 2 (default) | — |
| Q12 | Variance threshold | 1.5 (default) | — |
| Q13 | VPIN threshold | 0.7 | ⏰ monitor rejection rate, in daily report |
| Q14 | ADX threshold | 20 | — |
| Q15 | Tukey IQR multiplier | 5× (conservative) | — |
| Q16 | Outlier handling | Quarantine | — |
| Q17 | Research fast-track | R-1..R-5 into L-0 prep; R-6..R-10 later | — |
| Q18 | DSPy scope | (a) classifier prompt first | ⏰ review/extend after each tuning, in daily report |
| Q19 | Manual chain inclusion | auto_ai only | — |
| Q20 | Rollout order | Agree (dependency-driven) | — |
| Q21 | Missing? | OK so far | — |

**Operational reminder requests (all funnel into a daily Telegram report):**
- L-5 progress / smallest-proof status
- Red-Team mode: 14-day soft→hard review countdown
- Strategy Selector deferral countdown (after L-3 ships)
- VPIN rejection-rate trend
- DSPy active prompts + next candidate to optimize

---

**Original question status:** awaiting operator answers · 2026-05-31
**How to use:** answer each question by writing **YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_ below
the prompt block. Leave my recommendation as-is so the diff is visible.
If a question doesn't make sense, write "unclear, please rephrase" and
I'll redo it.

Phase code key:
- `L-N` = Learning Architecture (Phase L-0 through L-7)
- `A-X` = Specialized Agents (Phase A-A through A-E)
- `N-N` = Noise Detection (Phase N-1 through N-4)
- `R-N` = Research Findings top-10 items

---

## Section 1 — Strategic / scope decisions

These shape the whole roadmap. Answer first.

### Q1. Auto vs Auto-Suggest mode for the Learner
> **Source:** `AUTO_AI_LEARNING_ARCHITECTURE.md` §11.1
>
> The Learner default design is **full auto with safety guards** — it
> updates parameters on a schedule without operator approval. Alternative
> is **auto-suggest mode** — every proposed change lands in a "pending
> approval" queue you review weekly.
>
> **Trade-off:** Full auto closes the loop fast, lower operator load,
> trusts the safety circuits. Auto-suggest is safer for the first month
> but creates a backlog if you don't review on time.
>
> **My recommendation:** Full auto with safety guards. Adds ~$0 cost,
> closes the loop properly.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q2. Learner-pause drawdown trigger
> **Source:** `AUTO_AI_LEARNING_ARCHITECTURE.md` §11.2
>
> The hard DD breaker pauses trading at -15%. The Learner should
> probably have a **tighter trigger** that pauses *learning* (not
> trading) when calibration is broken. Suggestion: -8% total DD pauses
> the learner; trading continues with last-known-good params.
>
> **Trade-off:** Tighter = less aggressive parameter changes during bad
> regimes. Looser = learner keeps trying to adapt through pain.
>
> **My recommendation:** -8% pauses learner only (trading continues).

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q3. How many Learning Phases?
> **Source:** `AUTO_AI_LEARNING_ARCHITECTURE.md` §11.3
>
> Full plan is `L-0` through `L-7` (~25 dev days). Options:
> - All 7 phases (~25 days)
> - Stop after `L-3` (Score & threshold learners, ~11 days)
> - Stop after `L-5` (Risk learners, ~18 days)
>
> **My recommendation:** L-0 through L-5 (~18 days). L-6 (Pattern
> learners — k-means, decision tree, HMM) is heaviest with marginal
> ROI vs cumulative. Defer.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q4. Backtest validation requirement
> **Source:** `AUTO_AI_LEARNING_ARCHITECTURE.md` §11.4
>
> Should every Learner change pass a backtest replay on held-out
> historical data before going live? This is the **Backtest Validator
> agent** (`A-B`), which adds ~5 days but is the single biggest safety
> improvement.
>
> **Trade-off:** Adds 5 dev days; delays Learner changes by ~minutes
> (replay time). Catches changes that look good on paper but would have
> broken on real candles.
>
> **My recommendation:** YES — make `A-B` mandatory before any Learner
> change writes to `learned_params`. `L-3` becomes blocked-by `A-B`.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q5. Smallest possible proof first?
> **Source:** `AUTO_AI_LEARNING_ARCHITECTURE.md` §11.5
>
> Two options for first ship:
> - (a) Phase `L-0` + one per-symbol learner (~3 days), demonstrates
>   the full loop end-to-end with smallest blast radius
> - (b) Commit to `L-0` through `L-3` minimum (~11 days)
>
> **My recommendation:** (a). Ship `L-0` + per-symbol learner, run for
> 7 days, then expand once the loop is trusted.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

## Section 2 — Specialized Agents decisions

### Q6. Red-Team agent first as 2-day pilot?
> **Source:** `AUTO_AI_SPECIALIZED_AGENTS.md` §8.1
>
> The Red-Team agent (`A-A`) has asymmetric payoff: each prevented
> losing trade pays for ~50 days of agent costs. It runs as the final
> pre-execution check, can only veto (never trigger trades).
>
> **Option:** Ship `A-A` first, before any Learning Phase. Or wait
> until you've digested the architecture doc.
>
> **My recommendation:** Ship `A-A` first. 2 days, lowest blast radius,
> highest ROI demo of "specialized agent" value.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q7. Cost ceiling per day for all new agents combined
> **Source:** `AUTO_AI_SPECIALIZED_AGENTS.md` §8.2
>
> Estimated combined cost of all 5 agents: ~$0.85/day base, up to
> ~$2.50/day in high-activity periods. Need a ceiling for safety.
>
> **My recommendation:** $5/day combined ceiling. If exceeded, fall
> back to last-known-good cached responses.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q8. Framework choice
> **Source:** `AUTO_AI_SPECIALIZED_AGENTS.md` §8.3
>
> Options: raw Anthropic SDK + plain Python orchestration vs DSPy vs
> LangGraph vs CrewAI vs AutoGen.
>
> **My recommendation:** Raw SDK. DSPy already installed for
> prompt-tuning use cases; not needed as orchestration framework.
> Other frameworks add dependency surface without solving real problems
> at this agent count.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q9. Red-Team veto: hard (block) or soft (score penalty)?
> **Source:** `AUTO_AI_SPECIALIZED_AGENTS.md` §8.4
>
> - **Soft:** Red-Team veto adds score penalty (e.g., -0.5 per high-
>   severity reason); trade may still execute if remaining score clears
>   threshold
> - **Hard:** Veto blocks the trade outright
>
> **My recommendation:** Soft for first 2 weeks. Track veto-correctness.
> If vetoed trades had below-avg WR after 2 weeks, switch to Hard mode.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q10. Strategy Selector agent — add or skip?
> **Source:** `AUTO_AI_SPECIALIZED_AGENTS.md` §8.5
>
> A "Strategy Selector" agent would pick which playbook to apply
> (breakout vs continuation vs reversal). Theoretical merit but
> requires discrete playbooks first.
>
> **My recommendation:** Defer. Revisit after `L-3` (per-archetype
> thresholds) ships, since that's when discrete playbooks emerge
> naturally.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

## Section 3 — Noise Detection tuning

These are threshold numbers. Quick to answer.

### Q11. Signal persistence bars (Type 1)
> **Source:** `AUTO_AI_NOISE_DETECTION.md` §8.1
>
> How many consecutive bars must a signal hold before it counts?
> - Aggressive: 1 bar (more setups, more noise)
> - Default: 2 bars (current + prior)
> - Conservative: 3 bars (fewer setups, fewer fakeouts)
>
> **My recommendation:** 2 (default). Reversal signals (failure swing,
> divergence) get 1 because they're inherently momentary.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q12. Consensus variance threshold (Type 6)
> **Source:** `AUTO_AI_NOISE_DETECTION.md` §8.2
>
> Reject setup when `stdev(scores)` across LLM voters exceeds this
> threshold. Score range is 0-10.
> - Strict: 1.0 (more rejections, fewer ambiguous trades)
> - Default: 1.5
> - Loose: 2.0 (fewer rejections, more borderline trades)
>
> **My recommendation:** 1.5.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q13. VPIN threshold
> **Source:** `AUTO_AI_NOISE_DETECTION.md` §8.3
>
> Skip new entries when VPIN exceeds this value. Literature standard
> is 0.7 for "high toxicity."
>
> **My recommendation:** Ship at 0.7, monitor rejection rate for 14
> days, then let the learner auto-tune via `L-2`.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q14. ADX threshold for trend-following archetypes
> **Source:** `AUTO_AI_NOISE_DETECTION.md` §8.4
>
> Below this ADX value, skip trend-following setups (breakout,
> continuation). Reversal and range-bound setups exempt.
> - Standard: 20
> - Stricter: 25
>
> **My recommendation:** 20.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q15. Tukey IQR multiplier for outlier candles
> **Source:** `AUTO_AI_NOISE_DETECTION.md` §8.5
>
> A candle's high/low gets quarantined if it exceeds this × IQR from
> rolling median. Standard Tukey fence is 1.5–3×. I proposed 5× because
> we want to catch only clear data errors, not legitimate volatility
> spikes.
>
> **My recommendation:** 5× (very conservative). Reduces false-flagging.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q16. Quarantine vs reject for outlier candles
> **Source:** `AUTO_AI_NOISE_DETECTION.md` §8.6
>
> When a candle is flagged as outlier:
> - **Quarantine:** keep in DB, skip in indicator computation only
>   (reversible, audit-friendly)
> - **Reject:** drop entirely from the data pipeline
>
> **My recommendation:** Quarantine. Reversible, conservative.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

## Section 4 — Cross-cutting / open additions

### Q17. Research items — which to fast-track?
> **Source:** `AUTO_AI_RESEARCH_FINDINGS.md` — top-10 list
>
> The research findings doc has 10 ranked items. Items `R-1` through
> `R-3` are ~1 day each, immediately surface new KPIs / sizing logic.
> Items `R-4` and `R-5` are also ~1 day each, foundational for the
> Learner safety circuits.
>
> **Question:** which of `R-1..R-10` get queued into the final
> master plan?
>
> **My recommendation:** Items `R-1`, `R-2`, `R-3`, `R-4`, `R-5` go
> into Phase L-0 prep work (they're prerequisites or independent
> wins). Items `R-6`, `R-7`, `R-9`, `R-10` integrate into specific
> Learning phases (`L-4`, `L-5`, `L-6`). Item `R-8` (vectorbt+Optuna)
> waits until `L-3` is stable so we have proper objective metric.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q18. DSPy prompt-optimization scope
> **Source:** my note after installing DSPy on 2026-05-31
>
> DSPy is installed and ready. To produce value, it needs:
> - Training examples (closed trades, classified positions, etc.)
> - A metric function (predictive accuracy, agreement with ground truth)
> - 2-3 days per prompt to wire properly
>
> **Question:** which prompt do we tune first?
> - (a) Setup classifier prompt — easiest, 138 labeled examples
>   already exist, ~1 day
> - (b) Rulebook generator prompt — high value, needs label
>   "did the rule predict outcome", ~2 days
> - (c) Red-Team agent prompt (after `A-A` ships) — high value but
>   blocked by `A-A`
> - (d) Skip DSPy for now, revisit after master plan ships
>
> **My recommendation:** (a) first as a demonstration after `A-A` and
> a couple of Learners are running. Then (b) once we have outcome-
> labeled rulebook trades.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q19. Manual chain inclusion
> **Source:** `AUTO_AI_LEARNING_ARCHITECTURE.md` §11.4 (rephrased)
>
> All four docs are scoped to `auto_ai` chain. Manual chain has its own
> rulebook (existing) but no learner, no noise gates, no specialized
> agents.
>
> **Option:** Apply some of these systems to manual chain too. Adds
> complexity but covers a larger volume of trades. Risk: manual chain
> is operator-driven, so learning loops there have different
> assumptions.
>
> **My recommendation:** Stay auto_ai only for now. Manual chain has
> a different decision model (operator judgement); applying learner
> logic to it would risk over-engineering. Re-evaluate after auto_ai
> proves out.

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q20. Phased rollout order across all four docs
> **Source:** synthesis question
>
> Given dependencies, the natural order across all four docs is roughly:
>
> 1. Research items `R-1`, `R-2`, `R-3`, `R-4`, `R-5` (independent wins)
> 2. `L-0` Foundation + smallest-proof per-symbol learner
> 3. `N-1` + `N-2` (cheap noise gates, FDR correction)
> 4. `A-A` Red-Team agent
> 5. `L-1` Read path refactor + `L-2` Cheap learners
> 6. `A-B` Backtest Validator
> 7. `L-3` Score & threshold learners (now backstopped by `A-B`)
> 8. `A-C` Post-Mortem investigator
> 9. `N-4` VPIN pipeline (shared with `A-E`, `L-6`)
> 10. `L-4`, `L-5`, `A-D`, `A-E`, `L-6`, `L-7` in parallel where possible
>
> **Question:** does this order make sense, or do you want to reshape?
>
> **My recommendation:** This order is dependency-driven. Open to
> swapping `A-A` and `N-1`/`N-2` if you prefer the noise sprint first
> (both have low blast radius).

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

### Q21. Anything I missed?
> Open field. Anything in the four docs that feels wrong, missing,
> ambiguous, or worth challenging?

**YOUR ANSWER:** _(filled in 2026-05-31 — see consolidated answers at top)_

---

## Section 5 — Instructions for me after you answer

Once you've filled in answers:

1. I read every answer.
2. I flag any that I don't understand and ask follow-ups in chat.
3. Once all answers are clear, I write the final master plan:
   `AUTO_AI_MASTER_PLAN.md` — step-by-step implementation roadmap
   that honors your decisions and the dependencies across all four
   docs.
4. The master plan replaces the per-doc Phased rollout sections; the
   individual docs become reference material.
