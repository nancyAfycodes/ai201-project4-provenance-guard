# Provenance Guard

A backend system that classifies submitted text content for AI vs. human
authorship, scores confidence in that classification, surfaces a
transparency label to end users, and handles appeals from creators who
believe they've been misclassified.

Full design rationale, architecture diagrams, and decision log live in
[`planning.md`](./planning.md). This README documents the *evidence* of
what was built: exact label text, rate-limit configuration, audit log
samples, and appeal handling, as required by the project spec.

---

## Table of Contents

- [Detection Signals](#detection-signals)
- [Confidence Scoring](#confidence-scoring)
- [Transparency Labels](#transparency-labels)
- [Appeals Workflow](#appeals-workflow)
- [Rate Limiting](#rate-limiting)
- [Audit Log](#audit-log)
- [API Reference](#api-reference)
- [Stretch Features](#stretch-features)

---

## Detection Signals

Provenance Guard uses **two independent signals** to classify content
(single-signal detection is not used):

1. **LLM-based judgment** (Groq, `llama-3.3-70b-versatile`) — assesses
   holistic semantic/stylistic AI-likelihood (hedging, generic phrasing,
   structural predictability). Blind spot: black-box reasoning, possible
   bias against unusual-but-authentic human styles.
2. **Stylometric heuristics** (pure Python) — measures statistical
   fingerprints: sentence length variance, vocabulary diversity,
   punctuation burstiness, sentence-opener repetition. Blind spot: easily
   thrown off by very short texts or intentionally repetitive/simple
   human writing (e.g. poetry forms, children's writing).

Both signals output a score from `0.0` to `1.0` representing `P(AI)`. Full
rationale for signal choice is in `planning.md` → Milestone 1 §2 and
Milestone 2 §1.

*(Implementation details and code walkthrough to be added in Milestone 3.)*

---

## Confidence Scoring

Signals are combined using an **agreement-band approach**, not a flat
weighted average — chosen because averaging can mask disagreement between
signals (see `planning.md` Milestone 1 Decision Log #3 for the reasoning).

```
spread = |llm_score - stylo_score|

if spread < 0.2:
    combined_score = (llm_score + stylo_score) / 2
else:
    combined_score = 0.5 + (midpoint - 0.5) * (1 - spread)
```

Agreeing signals produce a confident, extreme score. Disagreeing signals
are automatically dampened toward `0.5` (uncertain), proportional to how
much they disagree — this is what makes a `0.51` meaningfully different
from a `0.95`: the former reflects either a genuinely ambiguous middle
score, or strong signal disagreement; the latter reflects two signals
independently agreeing on a confident verdict.

*(How scores were tested for meaningfulness — example inputs/outputs
against a small labeled test set — to be added in Milestone 4.)*

---

## Transparency Labels

The exact text shown to a non-technical reader, for all three label
variants:

| Variant | Exact text |
|---|---|
| **High-confidence AI** | "This content is likely **AI-generated**. Our analysis found strong, consistent signals of AI authorship (confidence: **{score}**)." |
| **High-confidence human** | "This content is likely **human-written**. Our analysis found strong, consistent signals of human authorship (confidence: **{score}**)." |
| **Uncertain** | "We're **not confident** in this content's origin. Our signals give mixed results, leaning slightly toward **{direction}** (confidence: **{score}**). Treat this result with caution." |

`{direction}` is filled at runtime as "AI-generated", "human-written", or
"no clear lean" (exact-tie edge case), based on which side of `0.5` the
combined score falls on.

**Thresholds:**

| Score range | Label shown |
|---|---|
| ≥ 0.80 | High-confidence AI |
| 0.50 < score < 0.80 | Uncertain (leaning AI-generated) |
| = 0.50 | Uncertain (no clear lean) |
| 0.20 < score < 0.50 | Uncertain (leaning human-written) |
| ≤ 0.20 | High-confidence human |

---

## Appeals Workflow

Any creator who submitted content (identified by `content_id`, optionally
tied to `author_id`) can appeal a classification via `POST /appeal`.

On receipt, the system:
1. Looks up the original decision by `content_id`.
2. Updates content status: `classified` → `under_review`.
3. Appends a linked audit log entry capturing the appeal reasoning and
   status change.

Automated re-classification is **not** triggered — this is a queue for
human review, consistent with project scope.

*(Worked example — a real appeal request/response and its resulting audit
log entries — to be added in Milestone 5.)*

---

## Rate Limiting

*(Chosen limits and reasoning to be documented here in Milestone 5, once
Flask-Limiter is wired into the `/submit` endpoint.)*

---

## Audit Log

Every attribution decision and appeal is captured in a structured log,
including confidence score, both signal scores, and label assigned.

*(At least 3 real log entries — from `GET /log` output — to be added here
in Milestone 5.)*

---

## API Reference

```
POST /submit
  body: { "text": string, "author_id"?: string }
  returns: {
    "content_id": string,
    "attribution_result": "ai" | "human" | "uncertain",
    "confidence_score": float,
    "label_text": string,
    "signals": { "llm_score": float, "stylometric_score": float },
    "timestamp": string
  }

POST /appeal
  body: { "content_id": string, "reasoning": string }
  returns: {
    "content_id": string,
    "status": "under_review",
    "appeal_logged": true,
    "timestamp": string
  }

GET /log
  query params: ?content_id= (optional filter)
  returns: [ { ...audit log entries... } ]
```

---

## Stretch Features

Planned: ensemble detection, provenance certificate, analytics dashboard,
multi-modal support. Each will be documented here (what was built and how
it works) as completed — see `planning.md` for pre-implementation design
notes on each.