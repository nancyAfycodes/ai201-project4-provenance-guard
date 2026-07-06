"""
app.py

Provenance Guard — Flask API.

Milestone 3 scope: POST /submit (wired to Signal 1 only, placeholder
confidence score + label) and GET /log. Signal 2, real confidence
scoring, real label generation, rate limiting, and /appeal are added in
later milestones per planning.md's AI Tool Plan.
"""

import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from audit_log import get_log, log_entry
from confidence_scoring import combine_scores, determine_attribution_result, determine_direction
from signals.llm_signal import get_llm_score
from signals.stylometric_signal import get_stylometric_score

load_dotenv()

app = Flask(__name__)

MAX_TEXT_LENGTH = 20_000  # generous cap; guards against pathological payloads


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@app.route("/submit", methods=["POST"])
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

    # Label text finalized in Milestone 5 — for now, surface enough
    # structured info (result, score, direction) to verify scoring logic
    # end-to-end without committing to exact label wording yet.
    direction = determine_direction(combined_score) if attribution_result == "uncertain" else None
    placeholder_label = (
        f"[Placeholder label — exact wording finalized in Milestone 5] "
        f"attribution={attribution_result}, confidence={combined_score:.2f}"
        + (f", direction={direction}" if direction else "")
    )

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
        "status": "classified",
    })

    return jsonify({
        "content_id": content_id,
        "attribution_result": attribution_result,
        "confidence_score": combined_score,
        "label_text": placeholder_label,
        "signals": {
            "llm_score": llm_score,
            "stylometric_score": stylo_score,
        },
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