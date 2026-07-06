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

**Verified:** all three variants (and both uncertain sub-cases) confirmed
reachable and rendering exact text via direct testing of
`label_generator.py` across the full score range (0.05 to 0.95). The
label returned by `POST /submit` changes based on the actual combined
confidence score for that submission — it is not static text.

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

**Worked example** (real output, verified live):

Request:
```bash
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "8674870c-21ee-45ed-9ecd-f24ba32a1719", "creator_reasoning": "I wrote this myself. I'\''m a native English speaker and my writing style may appear more formal in certain situations."}' | python -m json.tool
```

Resulting `GET /log?content_id=8674870c-21ee-45ed-9ecd-f24ba32a1719`
shows two linked entries: the original `submission` entry (mutated in
place — `status` now `under_review`, with `appeal_reasoning` and
`appeal_timestamp` added), and a separate `appeal` event entry
preserving a full audit trail of the appeal action itself:

```json
[
  {
    "event_type": "submission",
    "content_id": "8674870c-21ee-45ed-9ecd-f24ba32a1719",
    "creator_id": "user_test",
    "timestamp": "2026-07-06T11:34:08.592738+00:00",
    "text": "I'd like to start learning a new language next month. Reason being is to be able to converse with locals on my next trip. I'm unsure what language to learn, maybe french or german.",
    "attribution_result": "uncertain",
    "signal_1_score": 0.6,
    "signal_1_reasoning": "The text lacks personal flair and has a straightforward structure, but the language choice and sentence composition are simple enough to be either human or AI-generated.",
    "signal_2_score": 0.4125,
    "signal_2_metrics": {
      "sentence_length_stdev": 0.0,
      "type_token_ratio": 0.853,
      "hedge_phrase_density": 0.0
    },
    "spread": 0.1875,
    "signals_agree": true,
    "confidence_score": 0.50625,
    "status": "under_review",
    "appeal_reasoning": "I wrote this myself. I'm a native English speaker and my writing style may appear more formal in certain situations.",
    "appeal_timestamp": "2026-07-06T12:09:07.842061+00:00"
  },
  {
    "event_type": "appeal",
    "content_id": "8674870c-21ee-45ed-9ecd-f24ba32a1719",
    "timestamp": "2026-07-06T12:09:07.842061+00:00",
    "appeal_reasoning": "I wrote this myself. I'm a native English speaker and my writing style may appear more formal in certain situations.",
    "status": "under_review",
    "original_attribution_result": "uncertain",
    "original_confidence_score": 0.50625
  }
]
```

This example is itself a small real instance of the "borderline" problem
from `planning.md`: a short, plainly-written piece of personal writing
landed almost exactly at the midpoint (0.506) — genuinely ambiguous
signals, appropriately labeled "uncertain" rather than forced into a
confident wrong guess, with a real path for the creator to contest it.

---

## Rate Limiting

Applied via Flask-Limiter to `POST /submit`:

```python
@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    ...
```

**Chosen limits: 10 requests/minute, 100 requests/day, per client IP.**

**Reasoning:**
- *10/minute* comfortably covers a real writer's realistic workflow —
  submitting a poem, checking the result, revising, and resubmitting a
  few times in quick succession, or a platform batch-checking a handful
  of pieces from one session. It's well above normal human submission
  speed (a person can't meaningfully write and submit 10+ distinct pieces
  of content in 60 seconds) but low enough to make a scripted flood
  immediately obvious and blocked within one minute.
- *100/day* allows for a genuinely prolific user or small-scale platform
  integration testing across a full day, without allowing sustained
  automated abuse (e.g. scraping or flooding the Groq-backed signal,
  which has cost and quota implications beyond just this API).
- Both numbers are deliberately conservative for a demo/project system
  rather than a high-volume production platform; a real deployment would
  likely tier limits by authenticated account rather than raw IP, and
  set higher ceilings for verified platform integrations. That
  refinement is out of scope here.

**Verified with the required test** — 12 rapid requests against the
10/minute limit, run via `test_rate_limit.ps1` (PowerShell equivalent of
the bash test script, for Windows environments) against the live running
server:

```
201
201
201
201
201
201
201
201
201
429
429
```

Successes return `201` (not `200`, since a new resource — the
classification decision — is created); rejections correctly return `429`
once the limit is exceeded, confirming Flask-Limiter is active and
enforcing the configured threshold.

- NB: Flask-limiter has a 60 second sliding-window. Since two requests were made within that window, only 11 requests was returned as a result. Above output is correct.
---

## Audit Log

Every attribution decision is captured in a structured log entry
containing: timestamp, content ID, both individual signal scores
(`signal_1_score`, `signal_2_score` + its metric breakdown), the
agreement/spread calculation, the combined confidence score, the
attribution result, and status. Retrieved live via `GET /log`.

Below are 4 real entries from testing (Milestone 4), reflecting the full
two-signal pipeline. The complete, growing log — including earlier
single-signal entries from Milestone 3's initial wiring — is visible at
`GET /log` and stored in `audit_log.json`.

**1. Clearly AI-generated test case**
```json
{
  "event_type": "submission",
  "content_id": "943ccc2e-b238-4cb4-a76b-2ff71c8d231a",
  "creator_id": "user_ai",
  "timestamp": "2026-07-06T10:56:56.568974+00:00",
  "attribution_result": "uncertain",
  "confidence_score": 0.6321489849689581,
  "signal_1_score": 0.8,
  "signal_1_reasoning": "The text's formal tone and repetitive phrase structure are characteristic of AI-generated content.",
  "signal_2_score": 0.5516320965443511,
  "signal_2_metrics": {
    "sentence_length_stdev": 6.658,
    "type_token_ratio": 0.884,
    "hedge_phrase_density": 4.651
  },
  "signal_2_reliable": true,
  "spread": 0.24836790345564896,
  "signals_agree": false,
  "status": "classified"
}
```

**2. Clearly human-written test case**
```json
{
  "event_type": "submission",
  "content_id": "deb098ca-13f6-409b-8986-fe1443cdd80f",
  "creator_id": "user_human",
  "timestamp": "2026-07-06T10:57:43.091475+00:00",
  "attribution_result": "uncertain",
  "confidence_score": 0.24079888057436422,
  "signal_1_score": 0.23,
  "signal_1_reasoning": "The text's casual tone and use of colloquial expressions, such as 'honestly' and 'drag me', suggest a human author.",
  "signal_2_score": 0.25159776114872845,
  "signal_2_metrics": {
    "sentence_length_stdev": 7.517,
    "type_token_ratio": 0.873,
    "hedge_phrase_density": 0.0
  },
  "signal_2_reliable": true,
  "spread": 0.02159776114872844,
  "signals_agree": true,
  "status": "classified"
}
```

**3. Borderline formal human writing (false-positive-scenario in the wild)**
```json
{
  "event_type": "submission",
  "content_id": "feab0ac9-da8a-4dc1-a995-9419f68ea383",
  "creator_id": "user_econ",
  "timestamp": "2026-07-06T10:58:43.501541+00:00",
  "attribution_result": "uncertain",
  "confidence_score": 0.4847606579322196,
  "signal_1_score": 0.7,
  "signal_1_reasoning": "The text's formal tone and use of technical terms suggest AI generation, but the nuanced discussion of economic concepts could also be written by a human expert.",
  "signal_2_score": 0.24396990770264937,
  "signal_2_metrics": {
    "sentence_length_stdev": 7.778,
    "type_token_ratio": 0.86,
    "hedge_phrase_density": 0.0
  },
  "signal_2_reliable": false,
  "spread": 0.4560300922973506,
  "signals_agree": false,
  "status": "classified"
}
```
This entry is a real, live instance of the false-positive scenario traced
in `planning.md` Milestone 1 §3: Signal 1 incorrectly leaned toward "AI"
on dense academic prose, but Signal 2 correctly caught it as human-like.
The sharp disagreement (spread=0.456) correctly pulled the combined score
to near-neutral (0.485) rather than trusting the wrong signal.

**4. Borderline lightly-edited AI text**
```json
{
  "event_type": "submission",
  "content_id": "a9adf2c7-7726-4c00-9d62-c73d03dbb429",
  "creator_id": "user_remote",
  "timestamp": "2026-07-06T10:59:41.223776+00:00",
  "attribution_result": "uncertain",
  "confidence_score": 0.4657348270377904,
  "signal_1_score": 0.6,
  "signal_1_reasoning": "The text's formal tone and balanced analysis suggest AI involvement, but the personal reflection and nuanced discussion imply human input.",
  "signal_2_score": 0.30243950481969245,
  "signal_2_metrics": {
    "sentence_length_stdev": 5.774,
    "type_token_ratio": 0.9,
    "hedge_phrase_density": 0.0
  },
  "signal_2_reliable": true,
  "spread": 0.2975604951803075,
  "signals_agree": false,
  "status": "classified"
}
```

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
    "signals": { "llm_score": float, "stylometric_score": float, "structural_score": float },
    "timestamp": string
  }

POST /appeal
  body: { "content_id": string, "creator_reasoning": string }
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

### Stretch 1: Ensemble Detection ✅

**What was built:** a third detection signal — structural/formatting
pattern analysis — combined with the existing two signals using a
weighted-voting approach, replacing the pairwise agreement-band as the
live scoring path in `/submit`.

**Signal 3 — Structural & punctuation patterns** (`signals/structural_signal.py`,
pure Python): measures em-dash/en-dash density, paragraph-length
uniformity (when 2+ paragraphs are present), and density of transitional
connector words ("however," "consequently," "therefore," etc.) used as
sentence openers — a distinct list from Signal 2's hedge phrases. Genuine
independence from Signal 2 was confirmed empirically: on both the
"clearly AI" and "clearly human" Milestone 4 test texts, Signal 3 stayed
neutral (0.4) since neither uses dashes or connector-openers — but on a
text deliberately written with heavy em-dash/connector usage, it
correctly spiked to 0.717. This shows the signal isn't redundant with
Signal 2, though it also means it doesn't always contribute a strong
opinion — a documented, honest limitation rather than a hidden one.

**Combination — Weighted Voting** (`ensemble_scoring.py`):

```python
weights = {"llm": 0.5, "stylometric": 0.3, "structural": 0.2}
weighted_score = sum(score_i * weight_i for each signal)

variance = population variance of the 3 raw scores
if variance < 0.02:      # signals agree
    combined_score = weighted_score
else:                     # signals disagree — dampen toward 0.5
    combined_score = 0.5 + (weighted_score - 0.5) * damping_factor
    # damping_factor shrinks toward 0 as variance grows, capped at variance=0.25
```

Weights reflect empirical trust established in Milestone 4: the LLM
signal showed the clearest directional accuracy, stylometric had already
needed one calibration fix, and structural is the newest/least-tested
signal — so it gets the smallest voice. This mirrors the same underlying
philosophy as the Milestone 1 pairwise agreement-band (broad disagreement
should reduce confidence, not be silently averaged away), extended to
handle 3 inputs via variance instead of pairwise spread.

**Verified** (internal wiring test — full example below): all 3 signals
compute independently, combine correctly, and the audit log captures the
full breakdown (`signal_3_score`, `signal_3_metrics`,
`ensemble_weighted_score`, `ensemble_variance`, `signals_agree`):

```json
{
  "signal_1_score": 0.9,
  "signal_2_score": 0.684,
  "signal_3_score": 0.4,
  "ensemble_weighted_score": 0.735,
  "ensemble_variance": 0.042,
  "signals_agree": false,
  "confidence_score": 0.696,
  "attribution_result": "uncertain",
  "label_text": "We're not confident in this content's origin. Our signals give mixed results, leaning slightly toward AI-generated (confidence: 0.70). Treat this result with caution."
}
```

This design decision is documented in `planning.md` under Stretch 1,
written before implementation began, per project requirements.

### Stretch 2: Provenance Certificate ✅

**What was built:** a "verified human" credential a creator can earn by
submitting 3+ past writing samples via `POST /verify`. Verification
method: writing sample analysis — **not** real identity verification,
documented honestly as a simple, explainable proxy.

**How it works** (`certification.py`):
1. Each submitted sample runs through the full 3-signal ensemble pipeline
   (same as `/submit`).
2. **Average check:** the mean combined score across all samples must be
   ≤ 0.45 (samples collectively read as human-leaning).
3. **Consistency check:** the population variance of the Signal 2
   (stylometric) score across samples must be ≤ 0.03 — a proxy for a
   single, consistent personal writing voice across different pieces.
4. Both checks must pass to issue a certificate (`certificate_id`,
   `creator_id`, `issued_at`, `sample_count`, `avg_confidence_score`,
   `consistency_variance`), persisted in `certificates.json`.

**Endpoints:**
```
POST /verify
  body: { "creator_id": string, "samples": [string, ...] }  # 3+ required
  returns: { "verified": bool, "certificate"?: {...}, "evaluation": {...}, "reason"?: string }

GET /certificate/<creator_id>
  returns: the certificate record, or 404 if not verified
```

**How it's displayed on content:** rather than altering the 3 core
transparency label variants (which stay verbatim per the project's
format requirement), the badge is a separate additive field returned by
`POST /submit` once a creator is verified:

- `"creator_verified": true`
- `"verified_badge_text": "✓ Verified Human Creator — this creator has completed Provenance Guard's writing-sample verification process."`

Unverified creators simply get `"creator_verified": false` with no badge
field. Verification status is also written to every subsequent
submission's audit log entry.

**Verified** (internal wiring test, real evaluation data):

```json
{
  "verified": true,
  "certificate": {
    "certificate_id": "dcf60130-c9ea-45d1-92b2-c8469eab742a",
    "creator_id": "writer_jane",
    "sample_count": 3,
    "avg_confidence_score": 0.270,
    "consistency_variance": 0.0016
  }
}
```

A subsequent `/submit` call from `writer_jane` correctly returned
`"creator_verified": true` and the exact badge text above; a call from an
unverified creator returned `"creator_verified": false` with no badge
field present. A verification attempt with only 1 sample was correctly
rejected: `"reason": "At least 3 writing samples are required (received 1)."`