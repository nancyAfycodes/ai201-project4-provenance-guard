"""
signals/metadata_signal.py

Stretch Feature — Multi-modal Support: structural analysis for structured
metadata (e.g. JSON product listings), distinct from the prose-oriented
signals used elsewhere. See planning.md Stretch 4 for design rationale.

Three heuristics, specific to structured data rather than running text:

  1. Field completeness ratio
     How many fields from a reference set of "typical" listing fields
     are present. Unusually complete listings can indicate templated/
     generated content. DOCUMENTED BLIND SPOT: a conscientious human
     seller filling out every field would also score high here — this
     heuristic is intentionally weak and flagged as such.

  2. Tag/keyword stuffing ratio
     Number of tags/keywords relative to description length. AI-
     generated SEO listings often over-populate tag arrays
     disproportionately to actual content.

  3. Value formatting uniformity
     Whether string field values follow identically consistent casing
     (e.g. 100% Title Case across every field) — humans are typically
     more inconsistent in real-world data entry.
"""

import json
import re

REFERENCE_FIELDS = [
    "title", "description", "price", "category", "tags",
    "sku", "brand", "in_stock", "rating", "images",
]

FREE_TEXT_FIELD_NAMES = ["description", "title", "notes", "summary", "details", "about"]


def _tokenize(text: str):
    return re.findall(r"[a-zA-Z']+", text.lower())


def _score_completeness(ratio: float) -> float:
    # High completeness -> higher AI-likelihood (weak heuristic, see docstring)
    if ratio >= 0.9:
        return 0.70
    if ratio <= 0.3:
        return 0.35
    return 0.35 + (ratio - 0.3) * (0.35 / 0.6)


def _score_tag_stuffing(tags_per_100_words: float) -> float:
    if tags_per_100_words <= 2.0:
        return 0.30
    if tags_per_100_words >= 10.0:
        return 0.85
    return 0.30 + (tags_per_100_words - 2.0) * (0.55 / 8.0)


def _score_uniformity(uniformity_ratio: float) -> float:
    # uniformity_ratio: fraction of string values sharing the same casing pattern
    if uniformity_ratio >= 0.95:
        return 0.75
    if uniformity_ratio <= 0.5:
        return 0.30
    return 0.30 + (uniformity_ratio - 0.5) * (0.45 / 0.45)


def _casing_pattern(s: str) -> str:
    if not s:
        return "empty"
    if s.isupper():
        return "upper"
    if s.istitle():
        return "title"
    if s.islower():
        return "lower"
    return "mixed"


def extract_text_fields(data: dict) -> str:
    """
    Pulls string values from likely free-text fields; falls back to all
    string values in the object if none of the named fields are present.
    """
    texts = []
    for key in FREE_TEXT_FIELD_NAMES:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)

    if not texts:
        for value in data.values():
            if isinstance(value, str) and len(value.split()) > 3:
                texts.append(value)

    return " ".join(texts)


def analyze_metadata_structure(raw_json_text: str) -> dict:
    """
    Returns:
        {
            "valid_json": bool,
            "structure_score": float in [0.0, 1.0],  # only meaningful if valid_json
            "metrics": {
                "completeness_ratio": float | None,
                "tag_stuffing_density": float | None,
                "value_uniformity_ratio": float | None,
            },
            "extracted_text": str,
            "error": str | None,
        }
    """
    try:
        data = json.loads(raw_json_text)
    except (json.JSONDecodeError, TypeError) as e:
        return {
            "valid_json": False,
            "structure_score": 0.5,
            "metrics": {"completeness_ratio": None, "tag_stuffing_density": None, "value_uniformity_ratio": None},
            "extracted_text": "",
            "error": f"Invalid JSON: {e}",
        }

    if not isinstance(data, dict):
        return {
            "valid_json": False,
            "structure_score": 0.5,
            "metrics": {"completeness_ratio": None, "tag_stuffing_density": None, "value_uniformity_ratio": None},
            "extracted_text": "",
            "error": "JSON root must be an object (dict) to analyze as structured metadata.",
        }

    extracted_text = extract_text_fields(data)

    # --- Metric 1: field completeness ---
    present = sum(1 for f in REFERENCE_FIELDS if f in data and data[f] not in (None, "", []))
    completeness_ratio = present / len(REFERENCE_FIELDS)
    completeness_score = _score_completeness(completeness_ratio)

    # --- Metric 2: tag/keyword stuffing ---
    tags = data.get("tags") or data.get("keywords") or []
    tag_count = len(tags) if isinstance(tags, list) else 0
    word_count = max(len(_tokenize(extracted_text)), 1)
    tag_density = (tag_count / word_count) * 100
    tag_score = _score_tag_stuffing(tag_density)

    # --- Metric 3: value formatting uniformity ---
    # NOTE (calibration finding, documented in README): with only 2
    # string values, "uniformity" is trivially 100% whenever they happen
    # to share a casing pattern by chance — a small-sample artifact, not
    # a real signal (same class of issue as the type-token-ratio finding
    # in signals/stylometric_signal.py). Requiring 3+ values before
    # trusting this metric, consistent with the `reliable` pattern used
    # elsewhere in the pipeline.
    MIN_STRING_VALUES_FOR_UNIFORMITY = 3
    string_values = [v for v in data.values() if isinstance(v, str) and v.strip()]
    uniformity_reliable = len(string_values) >= MIN_STRING_VALUES_FOR_UNIFORMITY
    if uniformity_reliable:
        patterns = [_casing_pattern(v) for v in string_values]
        most_common_count = max(patterns.count(p) for p in set(patterns))
        uniformity_ratio = most_common_count / len(patterns)
    else:
        uniformity_ratio = 0.5  # not enough values to judge — neutral, not trivially "uniform"
    uniformity_score = _score_uniformity(uniformity_ratio)

    structure_score = (completeness_score + tag_score + uniformity_score) / 3
    structure_score = max(0.0, min(1.0, structure_score))

    return {
        "valid_json": True,
        "structure_score": structure_score,
        "metrics": {
            "completeness_ratio": round(completeness_ratio, 3),
            "tag_stuffing_density": round(tag_density, 3),
            "value_uniformity_ratio": round(uniformity_ratio, 3),
        },
        "uniformity_reliable": uniformity_reliable,
        "extracted_text": extracted_text,
        "error": None,
    }
