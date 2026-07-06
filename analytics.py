"""
analytics.py

Stretch Feature — Analytics Dashboard: computes detection patterns,
appeal rate, and signal agreement rate directly from the existing audit
log (no new storage). See planning.md Stretch 3 for design rationale.
"""

from datetime import datetime, timezone

from audit_log import get_log


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def compute_analytics() -> dict:
    entries = get_log()  # all entries, most recent first

    submissions = [e for e in entries if e.get("event_type") == "submission"]
    appeals = [e for e in entries if e.get("event_type") == "appeal"]

    total_submissions = len(submissions)
    total_appeals = len(appeals)

    # --- 1. Detection patterns ---
    counts = {"ai": 0, "human": 0, "uncertain": 0}
    for e in submissions:
        result = e.get("attribution_result")
        if result in counts:
            counts[result] += 1

    detection_patterns = {
        "counts": counts,
        "percentages": {
            k: round((v / total_submissions * 100), 1) if total_submissions else 0.0
            for k, v in counts.items()
        },
    }

    # --- 2. Appeal rate ---
    appeal_rate = (total_appeals / total_submissions * 100) if total_submissions else 0.0
    appeal_stats = {
        "total_appeals": total_appeals,
        "appeal_rate_pct": round(appeal_rate, 1),
    }

    # --- 3. Signal agreement rate (chosen additional metric) ---
    # Only submissions that actually have a `signals_agree` field are
    # counted (Milestone 3's earliest single-signal entries predate this
    # field entirely — "field absent" is not the same as "disagreed", so
    # those entries are excluded from this specific metric's denominator
    # rather than silently miscounted).
    entries_with_agreement_field = [e for e in submissions if "signals_agree" in e]
    agree_count = sum(1 for e in entries_with_agreement_field if e.get("signals_agree") is True)
    disagree_count = sum(1 for e in entries_with_agreement_field if e.get("signals_agree") is False)
    denom = len(entries_with_agreement_field)

    signal_agreement = {
        "agree_count": agree_count,
        "disagree_count": disagree_count,
        "excluded_no_field_count": total_submissions - denom,
        "agreement_rate_pct": round((agree_count / denom * 100), 1) if denom else 0.0,
    }

    return {
        "total_submissions": total_submissions,
        "detection_patterns": detection_patterns,
        "appeal_stats": appeal_stats,
        "signal_agreement": signal_agreement,
        "generated_at": _now_iso(),
    }
