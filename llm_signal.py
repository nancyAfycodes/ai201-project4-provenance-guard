"""
signals/llm_signal.py

Signal 1: LLM-based judgment (Groq, llama-3.3-70b-versatile).

Per planning.md (Milestone 1 §2 / Milestone 2 §1):
  - Measures: holistic semantic/stylistic AI-likelihood (hedging, generic
    phrasing, structural predictability, lack of idiosyncratic detail).
  - Output: a single float score in [0.0, 1.0] representing P(AI) —
    probability the submitted text is AI-generated.
  - Known blind spot: black-box reasoning; may carry bias against
    unusual-but-authentic human styles (very formal, flowery, or
    repetitive prose can read as "AI-like" to the model).

This function is deliberately independent and directly testable — call
get_llm_score(text) on its own (see test_llm_signal.py) before wiring it
into the Flask endpoint, per the Milestone 3 checklist.
"""

import json
import os
import re
from groq import Groq

MODEL = "llama-3.3-70b-versatile"

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY environment variable is not set. "
                "Make sure it's defined in your .env and loaded (e.g. via "
                "python-dotenv) before calling get_llm_score()."
            )
        _client = Groq(api_key=api_key)
    return _client


SYSTEM_PROMPT = (
    "You are an assistant that evaluates whether a piece of text was "
    "written by an AI language model or by a human. You will be given a "
    "passage of creative or written content (a poem, story excerpt, blog "
    "post, etc.). Respond with ONLY a JSON object, no other text, in "
    "exactly this format:\n"
    '{"ai_probability": <float between 0.0 and 1.0>, "reasoning": '
    '"<one short sentence>"}\n'
    "ai_probability should reflect your genuine confidence — use values "
    "near 0.5 when the text is genuinely ambiguous, not just 0 or 1."
)


def get_llm_score(text: str) -> dict:
    """
    Send `text` to Groq and return a dict:
        {
            "llm_score": float in [0.0, 1.0],   # P(AI)
            "reasoning": str,                    # brief model explanation
            "error": str | None                  # populated on failure
        }

    On any API failure or malformed response, returns a safe fallback
    score of 0.5 (maximally uncertain) with the error recorded, rather
    than raising — a single flaky signal call should not take down the
    whole /submit request in Milestone 3's scope. (Retry/backoff policy
    can be added later if needed.)
    """
    if not text or not text.strip():
        return {"llm_score": 0.5, "reasoning": "Empty input.", "error": "empty_text"}

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0.0,
            max_tokens=150,
        )
        raw = response.choices[0].message.content.strip()

        # Models sometimes wrap JSON in markdown fences despite instructions —
        # strip those defensively before parsing.
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()

        parsed = json.loads(raw)
        score = float(parsed["ai_probability"])
        score = max(0.0, min(1.0, score))  # clamp defensively

        return {
            "llm_score": score,
            "reasoning": parsed.get("reasoning", ""),
            "error": None,
        }

    except Exception as e:
        # Fail safe: maximally uncertain score rather than crashing the
        # request. Logged so it's visible in the audit trail / server logs.
        return {
            "llm_score": 0.5,
            "reasoning": "Signal unavailable due to an error.",
            "error": str(e),
        }
