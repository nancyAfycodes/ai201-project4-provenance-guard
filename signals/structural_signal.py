"""
signals/structural_signal.py

Signal 3 (Stretch Feature — Ensemble Detection): Structural & punctuation
formatting patterns (pure Python, no external libraries).

Per planning.md Stretch 1 design:
  - Measures: em-dash/en-dash usage rate, paragraph-length uniformity,
    list/bullet formatting artifacts, and density of transitional
    connector words distinct from Signal 2's hedge-phrase list.
  - Output: a single float score in [0.0, 1.0] representing P(AI).
  - Independent of Signal 2: targets punctuation/formatting *habits*
    rather than sentence-level statistics or vocabulary diversity.
  - Blind spot: sensitive to short texts, and to genres (technical/legal
    writing) that legitimately use heavy connector density and uniform
    structure regardless of authorship.

Three metrics, weighted-averaged into a single structural_score:

  1. Em-dash / en-dash density (per 100 words)
     LLMs are notably heavy users of em-dashes as a stylistic tic.
     HIGH density -> HIGHER AI-likelihood.

  2. Paragraph-length uniformity
     AI-generated multi-paragraph text tends toward evenly-sized
     paragraphs; human writing is more irregular. Computed only when the
     text has 2+ paragraphs (blank-line-separated); otherwise neutral.
     LOW variance in paragraph length -> HIGHER AI-likelihood.

  3. Transitional connector density (distinct word list from Signal 2)
     Frequency of connector words like "however," "consequently,"
     "additionally," "therefore" used as sentence openers, per 100 words.
     HIGH density -> HIGHER AI-likelihood.
"""

import re

CONNECTOR_OPENERS = [
    "however", "consequently", "additionally", "therefore", "thus",
    "similarly", "nevertheless", "meanwhile", "subsequently", "conversely",
]

MIN_WORDS_FOR_RELIABLE_STATS = 40


def _tokenize(text: str):
    return re.findall(r"[a-zA-Z']+", text.lower())


def _split_sentences(text: str):
    raw = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [s.strip() for s in raw if s.strip()]


def _split_paragraphs(text: str):
    raw = re.split(r"\n\s*\n", text.strip())
    return [p.strip() for p in raw if p.strip()]


def _score_dash_density(density_per_100_words: float) -> float:
    if density_per_100_words <= 0:
        return 0.35  # absence doesn't strongly imply human
    if density_per_100_words >= 2.0:
        return 0.85
    return 0.35 + density_per_100_words * (0.50 / 2.0)


def _score_paragraph_uniformity(stdev: float, applicable: bool) -> float:
    if not applicable:
        return 0.5  # neutral — not enough paragraphs to judge
    if stdev <= 5.0:
        return 0.75
    if stdev >= 20.0:
        return 0.25
    return 0.75 - (stdev - 5.0) * (0.50 / 15.0)


def _score_connector_density(density_per_100_words: float) -> float:
    if density_per_100_words <= 0:
        return 0.35
    if density_per_100_words >= 2.0:
        return 0.80
    return 0.35 + density_per_100_words * (0.45 / 2.0)


def get_structural_score(text: str) -> dict:
    """
    Returns:
        {
            "structural_score": float in [0.0, 1.0],
            "metrics": {
                "dash_density": float,
                "paragraph_length_stdev": float | None,
                "connector_density": float,
            },
            "reliable": bool,
        }
    """
    words = _tokenize(text)
    sentences = _split_sentences(text)
    paragraphs = _split_paragraphs(text)

    reliable = len(words) >= MIN_WORDS_FOR_RELIABLE_STATS

    if not words:
        return {
            "structural_score": 0.5,
            "metrics": {
                "dash_density": 0.0,
                "paragraph_length_stdev": None,
                "connector_density": 0.0,
            },
            "reliable": False,
        }

    # --- Metric 1: em-dash / en-dash density ---
    dash_count = text.count("—") + text.count("–")
    dash_density = (dash_count / len(words)) * 100

    # --- Metric 2: paragraph-length uniformity ---
    paragraph_applicable = len(paragraphs) >= 2
    paragraph_stdev = None
    if paragraph_applicable:
        lengths = [len(_tokenize(p)) for p in paragraphs]
        mean_len = sum(lengths) / len(lengths)
        variance = sum((l - mean_len) ** 2 for l in lengths) / len(lengths)
        paragraph_stdev = variance ** 0.5

    # --- Metric 3: transitional connector density (opener-position) ---
    connector_count = 0
    for s in sentences:
        first_word = _tokenize(s)[0] if _tokenize(s) else ""
        if first_word in CONNECTOR_OPENERS:
            connector_count += 1
    connector_density = (connector_count / len(words)) * 100

    dash_score = _score_dash_density(dash_density)
    paragraph_score = _score_paragraph_uniformity(paragraph_stdev or 0.0, paragraph_applicable)
    connector_score = _score_connector_density(connector_density)

    # Equal-weighted average of the 3 structural metrics (unlike Signal 2,
    # no empirical reason yet to weight these unevenly — flagged as a
    # possible future refinement once tested against more inputs).
    structural_score = (dash_score + paragraph_score + connector_score) / 3
    structural_score = max(0.0, min(1.0, structural_score))

    return {
        "structural_score": structural_score,
        "metrics": {
            "dash_density": round(dash_density, 3),
            "paragraph_length_stdev": round(paragraph_stdev, 3) if paragraph_stdev is not None else None,
            "connector_density": round(connector_density, 3),
        },
        "reliable": reliable,
    }
