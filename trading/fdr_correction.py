"""
N-2 (Master plan Noise §2.2): Benjamini-Hochberg FDR correction helper.

When the rulebook generator looks at 50+ buckets (per archetype × day ×
session × score × ...) for "is this bucket significantly different from
50% WR or zero expectancy", controlling FAMILY-WISE error rate
(Bonferroni) is too conservative. FDR (Benjamini-Hochberg) controls the
EXPECTED PROPORTION OF FALSE DISCOVERIES while keeping more power.

For the rulebook this means: significant buckets surface, but 5% of them
on average are still false positives — much better than naïve no-
correction (where 50 tests at α=0.05 expect 2.5 false positives every
run, populating the rulebook with noise).

Public:
  benjamini_hochberg(p_values, alpha=0.05) -> list[bool]
      Returns one bool per p-value: True = significant after FDR
  apply_to_candidates(candidates, alpha=0.05) -> list[dict]
      Filters a list of candidate dicts (must have 'p_value' field)

Effect-size sidecar:
  significant_with_effect(cands, alpha, min_effect_size) -> list[dict]
      Additionally requires |effect_size| ≥ threshold so statistically
      significant but operationally trivial findings get dropped.
"""
from __future__ import annotations



def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[bool]:
    """Returns mask of which p-values survive FDR control at level alpha.

    Algorithm:
      1. Sort p-values ascending, remember original index
      2. For each rank i (1-indexed), check if p_i ≤ (i/m) * alpha
      3. Find largest k where this holds; all p-values with rank ≤ k are
         significant
    """
    n = len(p_values)
    if n == 0:
        return []
    # Sort by p-value, keep original indices
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    significant = [False] * n
    # Walk descending — first one to pass marks all below
    largest_passing = -1
    for rank_minus_1, (_orig_i, p) in enumerate(indexed):
        rank = rank_minus_1 + 1
        if p <= (rank / n) * alpha:
            largest_passing = rank
    # Mark all p-values at rank ≤ largest_passing as significant
    if largest_passing > 0:
        for rank_minus_1, (orig_i, _p) in enumerate(indexed[:largest_passing]):
            significant[orig_i] = True
    return significant


def apply_to_candidates(candidates: list[dict], alpha: float = 0.05,
                         p_field: str = "p_value") -> list[dict]:
    """Filter candidate rulebook entries by FDR-corrected significance.

    Each candidate dict must contain `p_field` (default 'p_value').
    Candidates without p_value are kept (treated as 'manual override').
    """
    with_p = [(i, c) for i, c in enumerate(candidates) if c.get(p_field) is not None]
    if not with_p:
        return candidates
    p_values = [c[p_field] for _i, c in with_p]
    mask = benjamini_hochberg(p_values, alpha)
    out: list[dict] = []
    keep_indices = {with_p[i][0] for i, sig in enumerate(mask) if sig}
    for i, c in enumerate(candidates):
        if c.get(p_field) is None or i in keep_indices:
            out.append(c)
    return out


def significant_with_effect(candidates: list[dict],
                              alpha: float = 0.05,
                              min_effect_size: float = 0.10,
                              p_field: str = "p_value",
                              effect_field: str = "effect_size") -> list[dict]:
    """Apply FDR + a minimum effect-size threshold.

    Effect size is typically |Δ WR|, e.g., 0.10 = 10 percentage points.
    Stops statistically significant but operationally trivial findings
    from appearing in the rulebook (e.g., "Thursday WR 51% vs baseline 50%
    with p=0.04" — significant but useless).
    """
    fdr_passed = apply_to_candidates(candidates, alpha, p_field)
    return [c for c in fdr_passed
             if c.get(effect_field) is None
                or abs(c[effect_field]) >= min_effect_size]
