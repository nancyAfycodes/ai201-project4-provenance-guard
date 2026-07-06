"""
confidence_scoring.py

Combines Signal 1 (LLM judgment) and Signal 2 (stylometric heuristics)
into a single calibrated confidence score, using the Agreement-Band
Approach specified in planning.md (Milestone 1 Decision Log #3 /
Milestone 2 §1) — chosen over a flat weighted average because averaging
can mask disagreement between signals.

    spread = |llm_score - stylo_score|

    if spread < 0.2:
        combined_score = (llm_score + stylo_score) / 2
    else:
        combined_score = 0.5 + (midpoint - 0.5) * (1 - spread)

Agreeing signals produce a confident, extreme score. Disagreeing signals
are dampened toward 0.5 (uncertain), proportional to how much they
disagree.

Thresholds (planning.md Milestone 2 §2):
    score >= 0.80            -> "ai"
    0.20 < score < 0.80      -> "uncertain"
    score <= 0.20            -> "human"
    (score == 0.50 exactly   -> "uncertain", direction = "no clear lean")

CALIBRATION FINDING (Milestone 4 testing, documented in README):
Testing against the milestone's 4 sample inputs showed that a spread as
"small" as 0.25 between two SAME-DIRECTION signals (e.g. llm=0.80,
stylo=0.55 — both lean AI, just with different intensity) is still
dampened toward uncertain by this rule, same as it would dampen a
genuinely opposite-direction disagreement (e.g. 0.90 vs 0.20). This means
even a fairly clear-cut AI-generated test passage landed as "uncertain"
(0.632) rather than "high-confidence AI", despite one signal alone
already exceeding the 0.80 threshold on its own.

This was a deliberate choice, not a bug: the agreement-band's job is to
never let two only-loosely-corroborating signals produce a falsely
confident verdict, and same-direction-but-different-magnitude signals are
exactly the case where a plain average would have looked convincingly
confident without actually deserving to be. The system is intentionally
conservative — it would rather say "uncertain" than be confidently wrong,
which is the core philosophy behind representing genuine uncertainty
(see planning.md Milestone 2 §2 and Goals). An alternative design could
distinguish same-direction disagreement from opposite-direction
disagreement and dampen only the latter; this was considered and
deliberately not implemented, to keep the confidence rule simple,
auditable, and consistently conservative rather than adding another
tunable branch.
"""

AGREEMENT_THRESHOLD = 0.2
HIGH_CONFIDENCE_AI_THRESHOLD = 0.80
HIGH_CONFIDENCE_HUMAN_THRESHOLD = 0.20


def combine_scores(llm_score: float, stylo_score: float) -> dict:
    """
    Returns:
        {
            "combined_score": float in [0.0, 1.0],
            "spread": float,          # |llm_score - stylo_score|
            "signals_agree": bool,    # spread < AGREEMENT_THRESHOLD
        }
    """
    spread = abs(llm_score - stylo_score)
    midpoint = (llm_score + stylo_score) / 2
    signals_agree = spread < AGREEMENT_THRESHOLD

    if signals_agree:
        combined_score = midpoint
    else:
        combined_score = 0.5 + (midpoint - 0.5) * (1 - spread)

    combined_score = max(0.0, min(1.0, combined_score))

    return {
        "combined_score": combined_score,
        "spread": spread,
        "signals_agree": signals_agree,
    }


def determine_attribution_result(combined_score: float) -> str:
    """
    Maps a combined score to one of 3 result categories, per the
    thresholds in planning.md Milestone 2 §2.
    """
    if combined_score >= HIGH_CONFIDENCE_AI_THRESHOLD:
        return "ai"
    if combined_score <= HIGH_CONFIDENCE_HUMAN_THRESHOLD:
        return "human"
    return "uncertain"


def determine_direction(combined_score: float) -> str:
    """
    For 'uncertain' results only: which way the score leans.
    Used by the label generator (Milestone 5) to fill in {direction}.
    """
    if combined_score == 0.5:
        return "no clear lean"
    return "AI-generated" if combined_score > 0.5 else "human-written"