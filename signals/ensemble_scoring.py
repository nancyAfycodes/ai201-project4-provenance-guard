"""
ensemble_scoring.py

Stretch Feature — Ensemble Detection: combines 3 signals (LLM judgment,
stylometric heuristics, structural/formatting patterns) into a single
confidence score using a documented weighted-voting approach.

This is additive to, not a replacement of, confidence_scoring.py's
Milestone 4 pairwise agreement-band approach — that 2-signal function
remains intact and independently testable as the core project
requirement. This module implements the 3-signal ensemble path used by
app.py once this stretch feature is active. See planning.md Stretch 1
for full design rationale.

    weights = { llm: 0.5, stylometric: 0.3, structural: 0.2 }
    weighted_score = sum(score_i * weight_i)

    variance = population variance of the 3 raw scores
    if variance is high: dampen combined_score toward 0.5, proportional
    to variance (same philosophy as the pairwise agreement-band: broad
    disagreement across signals should reduce confidence, not just be
    averaged through).
"""

WEIGHTS = {
    "llm": 0.5,
    "stylometric": 0.3,
    "structural": 0.2,
}

# Variance threshold above which signals are considered to meaningfully
# disagree. Chosen to be roughly comparable in spirit to the pairwise
# agreement-band's 0.2 spread threshold, adapted for 3-way variance.
VARIANCE_THRESHOLD = 0.02


def combine_ensemble_scores(llm_score: float, stylo_score: float, structural_score: float) -> dict:
    """
    Returns:
        {
            "combined_score": float in [0.0, 1.0],
            "weighted_score": float,   # before any dampening
            "variance": float,         # population variance of the 3 raw scores
            "signals_agree": bool,     # variance < VARIANCE_THRESHOLD
            "per_signal": {"llm": ..., "stylometric": ..., "structural": ...},
        }
    """
    scores = {
        "llm": llm_score,
        "stylometric": stylo_score,
        "structural": structural_score,
    }

    weighted_score = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS)

    mean = sum(scores.values()) / 3
    variance = sum((v - mean) ** 2 for v in scores.values()) / 3

    signals_agree = variance < VARIANCE_THRESHOLD

    if signals_agree:
        combined_score = weighted_score
    else:
        # Dampen toward 0.5 proportional to how far variance exceeds the
        # threshold. Capped so a single wildly-disagreeing signal can't
        # push the damping factor negative.
        excess = min(variance, 0.25)  # cap for stability
        damping_factor = 1 - (excess / 0.25)  # 1.0 = no damping, 0.0 = full damping to 0.5
        combined_score = 0.5 + (weighted_score - 0.5) * damping_factor

    combined_score = max(0.0, min(1.0, combined_score))

    return {
        "combined_score": combined_score,
        "weighted_score": weighted_score,
        "variance": variance,
        "signals_agree": signals_agree,
        "per_signal": scores,
    }
