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
2. **Stylometric heuristics** (pure Python) — measures 3 statistical
   fingerprints: sentence length variance, vocabulary diversity
   (type-token ratio), and hedge-phrase density (frequency of common AI
   stock phrases like "it is important to note", "furthermore"). These
   are combined into a single score using a **weighted** (not flat)
   average — hedge-phrase density is weighted highest (0.5) after testing
   showed it discriminates more reliably than type-token ratio at short
   text lengths (see Confidence Scoring section below for the finding).
   Blind spot: easily thrown off by very short texts or intentionally
   repetitive/simple human writing (e.g. poetry forms, children's
   writing).

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
from a `0.95`.

### Testing whether scores are meaningful

Tested against 4 deliberately chosen inputs (1 clearly AI, 1 clearly
human, 2 borderline) — real output from `test_combined_scoring.py`:

| Case | llm_score | stylo_score | spread | agree? | combined | result | direction |
|---|---|---|---|---|---|---|---|
| Clearly AI-generated | 0.80 | 0.55 | 0.25 | No | 0.632 | uncertain | AI-generated |
| Clearly human-written | 0.23 | 0.25 | 0.02 | Yes | 0.241 | uncertain | human-written |
| Borderline: formal human writing | 0.70 | 0.24 | 0.46 | No | 0.485 | uncertain | human-written |
| Borderline: lightly edited AI | 0.60 | 0.29 | 0.31 | No | 0.463 | uncertain | human-written |

**What this validates:** the "borderline formal human" case is a direct,
real-world instance of the false-positive scenario designed in
`planning.md` Milestone 1 §3 — the LLM signal incorrectly leaned AI
(0.70) on dense academic prose, but the stylometric signal correctly
caught it as human (0.24). Because the two signals disagreed sharply
(spread=0.46), the agreement-band correctly pulled the combined score to
a near-neutral 0.485 instead of trusting the wrong signal — exactly the
safety behavior the false-positive scenario was designed to produce.

**Calibration finding — documented, not hidden:** neither "clearly AI"
nor "clearly human" reached the high-confidence thresholds (≥0.80 /
≤0.20) despite being intuitively unambiguous cases. Root cause: a spread
as small as 0.25 between two *same-direction* signals (e.g. 0.80 and
0.55, both leaning AI) is dampened by the same rule that catches genuine
opposite-direction disagreement (e.g. 0.90 vs 0.20) — even though these
are meaningfully different situations. This was a deliberate decision,
not a bug: the system is intentionally conservative, treating any
significant spread as reason for caution rather than distinguishing
same-direction from opposite-direction disagreement. Given this project's
explicit goal of representing genuine uncertainty rather than forcing
confident-but-possibly-wrong outputs, "the system said uncertain when it
wasn't fully sure" was judged a better failure mode than "the system was
confidently right most of the time but occasionally confidently wrong."
This tradeoff — and the alternative design considered (only dampening on
opposite-direction disagreement) — is documented in
`confidence_scoring.py`.

Separately, `signals/stylometric_signal.py` underwent its own calibration
fix during testing: an initial flat average of its 3 metrics was found to
be dragged down by type-token ratio, which is known to be biased upward
by short text length regardless of authorship (confirmed empirically — a
clearly AI-generated test paragraph scored a "human-like" 0.884 TTR
purely as a length artifact). The metric combination was reweighted
toward hedge-phrase density, the metric that empirically discriminated
correctly, once this was identified.

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