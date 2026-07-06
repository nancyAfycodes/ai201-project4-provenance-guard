"""
certification.py

Stretch Feature — Provenance Certificate: a "verified human" credential
earned by submitting 3+ past writing samples, per planning.md Stretch 2.

Verification method: writing sample analysis (not real identity
verification — an intentionally simple, explainable proxy, documented as
such). Two checks, both must pass:

  1. Average combined ensemble score across all samples <= AVG_SCORE_THRESHOLD
     (samples collectively read as human-leaning).
  2. Population variance of the Signal 2 (stylometric) score across
     samples <= CONSISTENCY_VARIANCE_THRESHOLD (a consistent personal
     writing voice across different pieces).
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone

from confidence_scoring import determine_attribution_result
from ensemble_scoring import combine_ensemble_scores
from signals.llm_signal import get_llm_score
from signals.stylometric_signal import get_stylometric_score
from signals.structural_signal import get_structural_score

CERT_PATH = os.path.join(os.path.dirname(__file__), "certificates.json")
_lock = threading.Lock()

MIN_SAMPLES = 3
AVG_SCORE_THRESHOLD = 0.45
CONSISTENCY_VARIANCE_THRESHOLD = 0.03

BADGE_TEXT = (
    "✓ Verified Human Creator — this creator has completed Provenance "
    "Guard's writing-sample verification process."
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _read_all():
    if not os.path.exists(CERT_PATH):
        return {}
    with open(CERT_PATH, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _write_all(certs):
    with open(CERT_PATH, "w") as f:
        json.dump(certs, f, indent=2)


def evaluate_samples(samples: list):
    """
    Runs the full 3-signal ensemble pipeline on each sample and computes
    both the average combined score and the stylometric consistency
    variance across samples. Does not issue a certificate — pure
    evaluation, independently testable.

    Returns:
        {
            "per_sample": [ { "combined_score": ..., "stylo_score": ..., ... }, ... ],
            "avg_combined_score": float,
            "stylo_consistency_variance": float,
            "passes_avg_threshold": bool,
            "passes_consistency_threshold": bool,
            "eligible": bool,
        }
    """
    per_sample = []
    for sample_text in samples:
        llm_result = get_llm_score(sample_text)
        stylo_result = get_stylometric_score(sample_text)
        structural_result = get_structural_score(sample_text)

        scoring = combine_ensemble_scores(
            llm_result["llm_score"],
            stylo_result["stylo_score"],
            structural_result["structural_score"],
        )

        per_sample.append({
            "combined_score": scoring["combined_score"],
            "attribution_result": determine_attribution_result(scoring["combined_score"]),
            "llm_score": llm_result["llm_score"],
            "stylo_score": stylo_result["stylo_score"],
            "structural_score": structural_result["structural_score"],
        })

    avg_combined_score = sum(s["combined_score"] for s in per_sample) / len(per_sample)

    stylo_scores = [s["stylo_score"] for s in per_sample]
    mean_stylo = sum(stylo_scores) / len(stylo_scores)
    stylo_consistency_variance = sum((s - mean_stylo) ** 2 for s in stylo_scores) / len(stylo_scores)

    passes_avg_threshold = avg_combined_score <= AVG_SCORE_THRESHOLD
    passes_consistency_threshold = stylo_consistency_variance <= CONSISTENCY_VARIANCE_THRESHOLD

    return {
        "per_sample": per_sample,
        "avg_combined_score": avg_combined_score,
        "stylo_consistency_variance": stylo_consistency_variance,
        "passes_avg_threshold": passes_avg_threshold,
        "passes_consistency_threshold": passes_consistency_threshold,
        "eligible": passes_avg_threshold and passes_consistency_threshold,
    }


def issue_certificate(creator_id: str, samples: list):
    """
    Full verification flow: validates sample count, evaluates samples,
    and if eligible, issues + persists a certificate record.

    Returns a dict describing the outcome (see docstring of
    evaluate_samples for the evaluation fields, plus verified/reason/
    certificate on top).
    """
    if len(samples) < MIN_SAMPLES:
        return {
            "verified": False,
            "reason": f"At least {MIN_SAMPLES} writing samples are required (received {len(samples)}).",
        }

    evaluation = evaluate_samples(samples)

    if not evaluation["eligible"]:
        reasons = []
        if not evaluation["passes_avg_threshold"]:
            reasons.append(
                f"average confidence score across samples ({evaluation['avg_combined_score']:.3f}) "
                f"exceeds the human-leaning threshold ({AVG_SCORE_THRESHOLD})"
            )
        if not evaluation["passes_consistency_threshold"]:
            reasons.append(
                f"writing style varied too much across samples (variance="
                f"{evaluation['stylo_consistency_variance']:.4f}, threshold={CONSISTENCY_VARIANCE_THRESHOLD})"
            )
        return {
            "verified": False,
            "reason": "; ".join(reasons),
            "evaluation": evaluation,
        }

    certificate_id = str(uuid.uuid4())
    issued_at = _now_iso()
    record = {
        "certificate_id": certificate_id,
        "creator_id": creator_id,
        "issued_at": issued_at,
        "sample_count": len(samples),
        "avg_confidence_score": evaluation["avg_combined_score"],
        "consistency_variance": evaluation["stylo_consistency_variance"],
    }

    with _lock:
        certs = _read_all()
        certs[creator_id] = record
        _write_all(certs)

    return {
        "verified": True,
        "certificate": record,
        "evaluation": evaluation,
    }


def get_certificate(creator_id: str):
    with _lock:
        certs = _read_all()
    return certs.get(creator_id)


def is_verified(creator_id: str) -> bool:
    return get_certificate(creator_id) is not None
