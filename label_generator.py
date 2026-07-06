"""
label_generator.py

Generates the exact transparency label text shown to end users, per the
3 variants designed in planning.md (Milestone 2 §3) / README.

The label text is fixed (verbatim, per project requirements) with only
{score} and {direction} filled in at runtime.
"""

from confidence_scoring import (
    determine_attribution_result,
    determine_direction,
)

HIGH_CONFIDENCE_AI_TEMPLATE = (
    "This content is likely AI-generated. Our analysis found strong, "
    "consistent signals of AI authorship (confidence: {score})."
)

HIGH_CONFIDENCE_HUMAN_TEMPLATE = (
    "This content is likely human-written. Our analysis found strong, "
    "consistent signals of human authorship (confidence: {score})."
)

UNCERTAIN_TEMPLATE = (
    "We're not confident in this content's origin. Our signals give "
    "mixed results, leaning slightly toward {direction} (confidence: "
    "{score}). Treat this result with caution."
)

UNCERTAIN_NO_LEAN_TEMPLATE = (
    "We're not confident in this content's origin. Our signals give "
    "mixed results, with no clear lean toward either AI or human "
    "authorship (confidence: {score}). Treat this result with caution."
)


def generate_label(combined_score: float) -> dict:
    """
    Returns:
        {
            "label_text": str,           # exact text to show the user
            "variant": "ai" | "human" | "uncertain",
            "direction": str | None,     # only set when variant == "uncertain"
        }
    """
    result = determine_attribution_result(combined_score)
    score_str = f"{combined_score:.2f}"

    if result == "ai":
        return {
            "label_text": HIGH_CONFIDENCE_AI_TEMPLATE.format(score=score_str),
            "variant": "ai",
            "direction": None,
        }

    if result == "human":
        return {
            "label_text": HIGH_CONFIDENCE_HUMAN_TEMPLATE.format(score=score_str),
            "variant": "human",
            "direction": None,
        }

    direction = determine_direction(combined_score)
    if direction == "no clear lean":
        return {
            "label_text": UNCERTAIN_NO_LEAN_TEMPLATE.format(score=score_str),
            "variant": "uncertain",
            "direction": direction,
        }
    return {
        "label_text": UNCERTAIN_TEMPLATE.format(direction=direction, score=score_str),
        "variant": "uncertain",
        "direction": direction,
    }
