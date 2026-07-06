"""
app.py

Provenance Guard — Flask API.

Full production scope (Milestone 5): POST /submit (both signals, real
agreement-band confidence scoring, real transparency label), POST
/appeal (status update + linked audit log entry), GET /log, and rate
limiting on /submit via Flask-Limiter.
"""

import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template_string, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from analytics import compute_analytics
from audit_log import find_latest_decision, get_log, log_entry, update_submission_status
from certification import get_certificate, issue_certificate, BADGE_TEXT
from confidence_scoring import determine_attribution_result
from dashboard_template import DASHBOARD_HTML
from ensemble_scoring import combine_ensemble_scores
from label_generator import generate_label
from signals.llm_signal import get_llm_score
from signals.metadata_signal import analyze_metadata_structure
from signals.stylometric_signal import get_stylometric_score
from signals.structural_signal import get_structural_score

load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

MAX_TEXT_LENGTH = 20_000  # generous cap; guards against pathological payloads


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    text = data.get("text")
    creator_id = data.get("creator_id")
    content_type = data.get("content_type", "prose")

    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "'text' field is required and must be a non-empty string."}), 400

    if not creator_id or not isinstance(creator_id, str):
        return jsonify({"error": "'creator_id' field is required and must be a string."}), 400

    if content_type not in ("prose", "metadata"):
        return jsonify({"error": "'content_type' must be 'prose' or 'metadata' if provided."}), 400

    if len(text) > MAX_TEXT_LENGTH:
        return jsonify({"error": f"'text' exceeds max length of {MAX_TEXT_LENGTH} characters."}), 400

    content_id = str(uuid.uuid4())
    timestamp = _now_iso()
    creator_verified = get_certificate(creator_id) is not None

    log_data = {
        "event_type": "submission",
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "text": text,
        "content_type": content_type,
        "creator_verified": creator_verified,
        "status": "classified",
    }
    response = {
        "content_id": content_id,
        "content_type": content_type,
        "creator_verified": creator_verified,
        "timestamp": timestamp,
    }

    if content_type == "metadata":
        # --- Stretch 4: Multi-modal Support (planning.md Stretch 4) ---
        metadata_result = analyze_metadata_structure(text)
        if not metadata_result["valid_json"]:
            return jsonify({"error": metadata_result["error"]}), 400

        structure_score = metadata_result["structure_score"]
        extracted_text = metadata_result["extracted_text"]
        has_text_component = len(extracted_text.split()) >= 5

        if has_text_component:
            signal_1_result = get_llm_score(extracted_text)
            signal_2_result = get_stylometric_score(extracted_text)
            signal_3_result = get_structural_score(extracted_text)
            text_ensemble = combine_ensemble_scores(
                signal_1_result["llm_score"], signal_2_result["stylo_score"], signal_3_result["structural_score"]
            )
            combined_score = 0.65 * text_ensemble["combined_score"] + 0.35 * structure_score
            log_data.update({
                "signal_1_score": signal_1_result["llm_score"],
                "signal_2_score": signal_2_result["stylo_score"],
                "signal_3_score": signal_3_result["structural_score"],
                "text_ensemble_score": text_ensemble["combined_score"],
            })
            response["signals"] = {
                "llm_score": signal_1_result["llm_score"],
                "stylometric_score": signal_2_result["stylo_score"],
                "structural_score": signal_3_result["structural_score"],
            }
        else:
            combined_score = structure_score

        attribution_result = determine_attribution_result(combined_score)
        label_result = generate_label(combined_score)

        log_data.update({
            "attribution_result": attribution_result,
            "confidence_score": combined_score,
            "label_text": label_result["label_text"],
            "structure_score": structure_score,
            "structure_metrics": metadata_result["metrics"],
            "uniformity_reliable": metadata_result.get("uniformity_reliable"),
            "has_text_component": has_text_component,
        })
        response.update({
            "attribution_result": attribution_result,
            "confidence_score": combined_score,
            "label_text": label_result["label_text"],
            "structure_metrics": metadata_result["metrics"],
        })

    else:
        # --- Prose pipeline (Milestones 3-5 + Stretch 1 ensemble) ---
        signal_1_result = get_llm_score(text)
        llm_score = signal_1_result["llm_score"]

        signal_2_result = get_stylometric_score(text)
        stylo_score = signal_2_result["stylo_score"]

        signal_3_result = get_structural_score(text)
        structural_score = signal_3_result["structural_score"]

        scoring_result = combine_ensemble_scores(llm_score, stylo_score, structural_score)
        combined_score = scoring_result["combined_score"]
        attribution_result = determine_attribution_result(combined_score)
        label_result = generate_label(combined_score)

        log_data.update({
            "attribution_result": attribution_result,
            "signal_1_score": llm_score,
            "signal_1_reasoning": signal_1_result["reasoning"],
            "signal_1_error": signal_1_result["error"],
            "signal_2_score": stylo_score,
            "signal_2_metrics": signal_2_result["metrics"],
            "signal_2_reliable": signal_2_result["reliable"],
            "signal_3_score": structural_score,
            "signal_3_metrics": signal_3_result["metrics"],
            "signal_3_reliable": signal_3_result["reliable"],
            "ensemble_weighted_score": scoring_result["weighted_score"],
            "ensemble_variance": scoring_result["variance"],
            "signals_agree": scoring_result["signals_agree"],
            "confidence_score": combined_score,
            "label_text": label_result["label_text"],
        })
        response.update({
            "attribution_result": attribution_result,
            "confidence_score": combined_score,
            "label_text": label_result["label_text"],
            "signals": {
                "llm_score": llm_score,
                "stylometric_score": stylo_score,
                "structural_score": structural_score,
            },
        })

    log_entry(log_data)

    if creator_verified:
        response["verified_badge_text"] = BADGE_TEXT

    return jsonify(response), 201


@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    creator_id = data.get("creator_id")
    samples = data.get("samples")

    if not creator_id or not isinstance(creator_id, str):
        return jsonify({"error": "'creator_id' field is required and must be a string."}), 400

    if not samples or not isinstance(samples, list) or not all(isinstance(s, str) and s.strip() for s in samples):
        return jsonify({"error": "'samples' field is required and must be a non-empty list of non-empty strings."}), 400

    result = issue_certificate(creator_id, samples)

    if result["verified"]:
        log_entry({
            "event_type": "certificate_issued",
            "content_id": None,
            "creator_id": creator_id,
            "timestamp": result["certificate"]["issued_at"],
            "certificate_id": result["certificate"]["certificate_id"],
            "sample_count": result["certificate"]["sample_count"],
            "avg_confidence_score": result["certificate"]["avg_confidence_score"],
            "consistency_variance": result["certificate"]["consistency_variance"],
            "status": "verified",
        })
        return jsonify(result), 201

    return jsonify(result), 200


@app.route("/certificate/<creator_id>", methods=["GET"])
def view_certificate(creator_id):
    cert = get_certificate(creator_id)
    if not cert:
        return jsonify({"error": f"No certificate found for creator_id '{creator_id}'."}), 404
    return jsonify(cert)


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON."}), 400

    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")

    if not content_id or not isinstance(content_id, str):
        return jsonify({"error": "'content_id' field is required and must be a string."}), 400

    if not creator_reasoning or not isinstance(creator_reasoning, str) or not creator_reasoning.strip():
        return jsonify({"error": "'creator_reasoning' field is required and must be a non-empty string."}), 400

    original = find_latest_decision(content_id)
    if not original:
        return jsonify({"error": f"No submission found with content_id '{content_id}'."}), 404

    timestamp = _now_iso()

    # Mutate the original submission entry so a single GET /log?content_id=...
    # call shows the current status + reasoning at a glance...
    update_submission_status(
        content_id,
        new_status="under_review",
        extra_fields={
            "appeal_reasoning": creator_reasoning,
            "appeal_timestamp": timestamp,
        },
    )

    # ...and also append a distinct 'appeal' event entry, preserving a
    # full, append-only audit trail of the appeal action itself
    # (planning.md Milestone 2 §4: "log the appeal alongside the
    # original decision").
    log_entry({
        "event_type": "appeal",
        "content_id": content_id,
        "timestamp": timestamp,
        "appeal_reasoning": creator_reasoning,
        "status": "under_review",
        "original_attribution_result": original.get("attribution_result"),
        "original_confidence_score": original.get("confidence_score"),
    })

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "appeal_logged": True,
        "timestamp": timestamp,
    }), 201


@app.route("/log", methods=["GET"])
def view_log():
    content_id = request.args.get("content_id")
    limit = request.args.get("limit", type=int)
    return jsonify({"entries": get_log(content_id=content_id, limit=limit)})


@app.route("/analytics", methods=["GET"])
def analytics():
    return jsonify(compute_analytics())


@app.route("/dashboard", methods=["GET"])
def dashboard():
    data = compute_analytics()
    dp = data["detection_patterns"]
    sa = data["signal_agreement"]
    return render_template_string(
        DASHBOARD_HTML,
        generated_at=data["generated_at"],
        total_submissions=data["total_submissions"],
        pct_ai=dp["percentages"]["ai"],
        pct_human=dp["percentages"]["human"],
        pct_uncertain=dp["percentages"]["uncertain"],
        count_ai=dp["counts"]["ai"],
        count_human=dp["counts"]["human"],
        count_uncertain=dp["counts"]["uncertain"],
        appeal_rate_pct=data["appeal_stats"]["appeal_rate_pct"],
        total_appeals=data["appeal_stats"]["total_appeals"],
        agreement_rate_pct=sa["agreement_rate_pct"],
        disagreement_rate_pct=round(100 - sa["agreement_rate_pct"], 1) if (sa["agree_count"] + sa["disagree_count"]) else 0.0,
        agree_count=sa["agree_count"],
        disagree_count=sa["disagree_count"],
        excluded_no_field_count=sa["excluded_no_field_count"],
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)