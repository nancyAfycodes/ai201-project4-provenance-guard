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
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from audit_log import find_latest_decision, get_log, log_entry, update_submission_status
from confidence_scoring import combine_scores, determine_attribution_result
from label_generator import generate_label
from signals.llm_signal import get_llm_score
from signals.stylometric_signal import get_stylometric_score

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

    if not text or not isinstance(text, str) or not text.strip():
        return jsonify({"error": "'text' field is required and must be a non-empty string."}), 400

    if not creator_id or not isinstance(creator_id, str):
        return jsonify({"error": "'creator_id' field is required and must be a string."}), 400

    if len(text) > MAX_TEXT_LENGTH:
        return jsonify({"error": f"'text' exceeds max length of {MAX_TEXT_LENGTH} characters."}), 400

    content_id = str(uuid.uuid4())
    timestamp = _now_iso()

    # --- Signal 1: LLM judgment ---
    signal_1_result = get_llm_score(text)
    llm_score = signal_1_result["llm_score"]

    # --- Signal 2: Stylometric heuristics ---
    signal_2_result = get_stylometric_score(text)
    stylo_score = signal_2_result["stylo_score"]

    # --- Confidence scoring: agreement-band combination (planning.md M1/M2) ---
    scoring_result = combine_scores(llm_score, stylo_score)
    combined_score = scoring_result["combined_score"]
    attribution_result = determine_attribution_result(combined_score)

    # --- Transparency label (planning.md Milestone 2 §3) ---
    label_result = generate_label(combined_score)

    # --- Audit log entry ---
    log_entry({
        "event_type": "submission",
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": timestamp,
        "text": text,  # stored in full — see audit_log.py docstring / planning.md
        "attribution_result": attribution_result,
        "signal_1_score": llm_score,
        "signal_1_reasoning": signal_1_result["reasoning"],
        "signal_1_error": signal_1_result["error"],
        "signal_2_score": stylo_score,
        "signal_2_metrics": signal_2_result["metrics"],
        "signal_2_reliable": signal_2_result["reliable"],
        "spread": scoring_result["spread"],
        "signals_agree": scoring_result["signals_agree"],
        "confidence_score": combined_score,
        "label_text": label_result["label_text"],
        "status": "classified",
    })

    return jsonify({
        "content_id": content_id,
        "attribution_result": attribution_result,
        "confidence_score": combined_score,
        "label_text": label_result["label_text"],
        "signals": {
            "llm_score": llm_score,
            "stylometric_score": stylo_score,
        },
        "timestamp": timestamp,
    }), 201


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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)