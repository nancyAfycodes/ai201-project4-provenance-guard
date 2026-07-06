"""
signals/stylometric_signal.py

Signal 2: Stylometric heuristics (pure Python, no external libraries).

Per planning.md (Milestone 1 §2 / Milestone 2 §1):
  - Measures: statistical fingerprints of writing style — sentence length
    variance ("burstiness"), vocabulary diversity, and formulaic
    hedge-phrase density.
  - Output: a single float score in [0.0, 1.0] representing P(AI).
  - Known blind spots: unreliable on very short texts (not enough data
    for meaningful statistics — see planning.md Edge Case #1), and can
    mistake intentional human repetition/simplicity (e.g. poetry forms,
    children's writing — Edge Case #2) for AI-style uniformity.

Three metrics are computed and averaged into a single stylo_score:

  1. Sentence-length variance ("burstiness")
     Human writing tends to mix short and long sentences irregularly.
     AI text (especially under default sampling) tends toward more
     uniform sentence lengths. LOW variance -> HIGHER AI-likelihood.

  2. Type-token ratio (vocabulary diversity)
     Ratio of unique words to total words. Repetitive, narrow vocabulary
     can indicate formulaic generation. LOW ttr -> HIGHER AI-likelihood.
     (Caution: also true of intentionally repetitive human writing —
     see Edge Case #2 in planning.md.)

  3. Hedge-phrase density
     Frequency of common AI stock transition/hedge phrases ("it is
     important to note", "furthermore", "in conclusion", etc.) per 100
     words. HIGH density -> HIGHER AI-likelihood. This is the most
     "designed" of the three metrics — it directly targets a known
     tell of default LLM output style, at the cost of being easy to
     defeat by prompting an LLM to avoid such phrases.

This function is deliberately independent and directly testable (see
test_stylometric_signal.py) before integration into confidence scoring
or the Flask endpoint.
"""

import re
import statistics

HEDGE_PHRASES = [
    "it is important to note",
    "it is worth noting",
    "it's important to note",
    "furthermore",
    "moreover",
    "in conclusion",
    "additionally",
    "on the other hand",
    "as a result",
    "in today's",
    "in summary",
    "overall,",
    "ultimately,",
    "holistic",
    "landscape",
    "delve into",
    "underscore",
    "multitude of",
]

MIN_WORDS_FOR_RELIABLE_STATS = 40
MIN_SENTENCES_FOR_RELIABLE_STATS = 3


def _split_sentences(text: str):
    # Simple sentence splitter on ./!/?/newline boundaries — sufficient
    # for short creative/blog-style submissions without pulling in NLP
    # libraries per the "pure Python" design choice.
    raw = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [s.strip() for s in raw if s.strip()]


def _tokenize(text: str):
    return re.findall(r"[a-zA-Z']+", text.lower())


def _score_sentence_variance(stdev: float) -> float:
    # Low variance (uniform sentence length) -> higher AI-likelihood.
    # Anchors chosen from informal inspection of test cases, not a
    # formally calibrated model (documented limitation — see
    # planning.md Milestone 2 §2 on calibration approach).
    if stdev <= 2.0:
        return 0.85
    if stdev >= 8.0:
        return 0.15
    # linear interpolation between anchors
    return 0.85 - (stdev - 2.0) * (0.70 / 6.0)


def _score_ttr(ttr: float) -> float:
    # Low type-token ratio (repetitive vocabulary) -> higher AI-likelihood.
    if ttr <= 0.35:
        return 0.80
    if ttr >= 0.75:
        return 0.20
    return 0.80 - (ttr - 0.35) * (0.60 / 0.40)


def _score_hedge_density(density_per_100_words: float) -> float:
    # Higher hedge-phrase density -> higher AI-likelihood. A density of
    # 0 doesn't strongly imply human (baseline 0.30, not 0.0) since
    # plenty of AI text avoids these phrases too.
    if density_per_100_words <= 0:
        return 0.30
    if density_per_100_words >= 3.0:
        return 0.85
    return 0.30 + density_per_100_words * (0.55 / 3.0)


def get_stylometric_score(text: str) -> dict:
    """
    Returns:
        {
            "stylo_score": float in [0.0, 1.0],   # P(AI)
            "metrics": {
                "sentence_length_stdev": float,
                "type_token_ratio": float,
                "hedge_phrase_density": float,
            },
            "reliable": bool,   # False if text is too short for
                                 # meaningful statistics (see Edge Case #1)
        }
    """
    words = _tokenize(text)
    sentences = _split_sentences(text)

    reliable = (
        len(words) >= MIN_WORDS_FOR_RELIABLE_STATS
        and len(sentences) >= MIN_SENTENCES_FOR_RELIABLE_STATS
    )

    if not words:
        return {
            "stylo_score": 0.5,
            "metrics": {
                "sentence_length_stdev": 0.0,
                "type_token_ratio": 0.0,
                "hedge_phrase_density": 0.0,
            },
            "reliable": False,
        }

    # --- Metric 1: sentence length variance ---
    sentence_lengths = [len(_tokenize(s)) for s in sentences if _tokenize(s)]
    if len(sentence_lengths) >= 2:
        stdev = statistics.stdev(sentence_lengths)
    else:
        stdev = 0.0  # can't compute variance from <2 sentences

    # --- Metric 2: type-token ratio ---
    ttr = len(set(words)) / len(words)

    # --- Metric 3: hedge phrase density (per 100 words) ---
    lowered = text.lower()
    hedge_count = sum(lowered.count(phrase) for phrase in HEDGE_PHRASES)
    density = (hedge_count / len(words)) * 100

    # --- Weighted combination ---
    # NOTE (calibration finding, documented in README): an earlier flat
    # average of these three metrics was tested against the milestone's
    # sample inputs and found to under-weight hedge-phrase density, the
    # most reliable of the three at short text lengths. Type-token ratio
    # in particular is known to be biased upward by short passages
    # (less text = less chance to repeat words, regardless of authorship),
    # which was confirmed empirically: a clearly AI-generated paragraph
    # scored a "human-like" 0.884 TTR purely as a length artifact. Hedge
    # phrase density correctly identified the same passage as AI-like.
    # Weights below reflect that empirical reliability ordering, not a
    # theoretical ideal — a documented, inspectable tradeoff rather than
    # a hidden one.
    variance_score = _score_sentence_variance(stdev)
    ttr_score = _score_ttr(ttr)
    hedge_score = _score_hedge_density(density)

    WEIGHT_HEDGE = 0.5
    WEIGHT_VARIANCE = 0.25
    WEIGHT_TTR = 0.25

    stylo_score = (
        WEIGHT_HEDGE * hedge_score
        + WEIGHT_VARIANCE * variance_score
        + WEIGHT_TTR * ttr_score
    )
    stylo_score = max(0.0, min(1.0, stylo_score))

    return {
        "stylo_score": stylo_score,
        "metrics": {
            "sentence_length_stdev": round(stdev, 3),
            "type_token_ratio": round(ttr, 3),
            "hedge_phrase_density": round(density, 3),
        },
        "reliable": reliable,
    }
