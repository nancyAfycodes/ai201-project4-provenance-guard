# Provenance Guard — Planning

This document captures architecture decisions, signal design, and diagrams
for Provenance Guard, made *before* implementation, and updated before each
new milestone/stretch feature as decisions evolve.

---

## Milestone 1: Understanding the System & Defining Architecture

### 1. Architecture Narrative

**Submission flow — what a single piece of text touches, in order:**

1. **API layer (Flask route — `POST /submit`)** — receives the raw text,
   validates it (non-empty, under max length), and checks the request
   against the **rate limiter** before any real work happens. If the caller
   has exceeded their quota, the request stops here and returns `429`.
2. **Signal 1 — LLM-based detector (Groq, `llama-3.3-70b-versatile`)** — the
   raw text is sent to Groq with a prompt asking it to assess the
   likelihood the text is AI-generated. Returns a probability-like score
   (`llm_score`, 0.0–1.0) and optionally brief reasoning.
3. **Signal 2 — Stylometric analyzer (pure Python)** — the same raw text is
   independently analyzed for statistical writing patterns (sentence length
   variance, vocabulary diversity, punctuation habits, sentence-opener
   repetition). Produces its own AI-likelihood score (`stylo_score`,
   0.0–1.0).
4. **Confidence Scoring Module** — combines `llm_score` and `stylo_score`
   into a single `P(AI)` value (0.0–1.0) using the **agreement-band
   approach** (see Decision Log #3 below) rather than a flat weighted
   average — signal *disagreement* itself lowers confidence.
5. **Label Generator** — maps the combined score to one of the **3
   transparency label variants** (high-confidence AI / high-confidence
   human / uncertain), filling in dynamic direction text where needed
   (e.g. "leans toward AI-generated").
6. **Audit Logger** — writes a structured record: content id, timestamp,
   both signal scores, combined score, label assigned, raw text (stored in
   full — see Decision Log #1), status (`classified`).
7. **API Response** — returns structured JSON: attribution result,
   confidence score, label text, signal breakdown, content id (needed
   later for appeals).

**Appeal flow — separately:**

1. **API layer (`POST /appeal`)** — creator submits a `content_id` + their
   reasoning.
2. **Validation / lookup** — confirms the `content_id` exists in the audit
   log (reuses internal lookup logic; no dedicated `GET /content/:id`
   endpoint is exposed — see Decision Log #2).
3. **Status Update** — content record's status flips from `classified` →
   `under_review`.
4. **Audit Logger** — appends a new entry linked to the original decision:
   appeal reasoning, timestamp, status change.
5. **API Response** — confirms appeal received and new status.

---

### 2. Detection Signals

**Signal 1 — LLM-based judgment (Groq `llama-3.3-70b-versatile`)**

- **What it measures:** Holistic semantic and stylistic judgment — the
  model's learned sense of what AI-generated text "feels like" (over-
  hedging, generic phrasing, structural predictability, lack of genuine
  specificity/idiosyncrasy).
- **Why it differs human vs. AI:** LLMs are trained on massive contrastive
  exposure to both kinds of text and can pick up subtle global patterns
  (tone consistency, cliché density, argument structure) that are hard to
  reduce to a simple statistical rule.
- **Blind spot:** It's a black box — hard to fully explain *why* it scored
  something a certain way. It can be confidently wrong, may carry bias
  against non-native-English writing styles or unusual-but-authentically-
  human styles (very formal, flowery, or repetitive prose can read as
  "AI-like"), and adds latency/external dependency (Groq API + its own
  rate limits).

**Signal 2 — Stylometric heuristics (pure Python, no ML)**

- **What it measures:** Concrete statistical fingerprints — sentence
  length variance, type-token ratio (vocabulary diversity), punctuation /
  "burstiness" patterns, average word length, repetition of sentence
  openers.
- **Why it differs human vs. AI:** Human writing tends toward *burstiness*
  — irregular rhythm, occasional very short or very long sentences,
  inconsistent vocabulary choices. AI text (especially under default
  sampling) tends toward smoother, more statistically uniform patterns.
- **Blind spot:** Easily fooled by short texts (not enough data for
  meaningful statistics), by human writers with naturally uniform styles
  (technical writers, non-native speakers using formulaic patterns), and
  by AI text that's been edited/paraphrased by a human afterward. Captures
  no semantic content — only shape, not meaning.

**Why two signals, and why these two:** They are meaningfully independent
— one semantic, one purely statistical — which is exactly why combining
them is more informative than either alone, and why *disagreement*
between them is itself a useful uncertainty signal (see Confidence Scoring
below).

---

### 3. False Positive Scenario Trace

A human writer submits a very formal, structurally clean personal essay
(naturally low burstiness, careful vocabulary).

- **Stylometric signal:** flags it AI-leaning (`stylo_score = 0.75`) —
  uniform sentence structure looks "AI-like" by the heuristic.
- **LLM signal:** also somewhat AI-leaning but less confident
  (`llm_score = 0.60`) — recognizes some genuine idiosyncratic detail, but
  the tone reads polished.
- **Combined score:** `spread = |0.75 - 0.60| = 0.15` → signals agree
  closely enough (< 0.2 threshold) that the midpoint (`0.675`) is used
  directly. Lands in the "uncertain, leans AI" zone rather than
  "high-confidence AI."
- **Label shown:** *"Uncertain — leans toward AI-generated (score: 0.68)"*
  — not a flat accusation.
- **Creator appeals:** submits reasoning ("I wrote this myself, I just
  write formally"). Status → `under_review`. Audit log gets an appeal
  entry tied to the original decision. A human moderator (out of scope to
  automate, per spec) can later review and override.

This scenario is why the uncertain band and honest confidence math matter:
it's the safety valve against a wrongly-confident binary verdict.

---

### 4. API Surface (contract, pre-implementation)

```
POST /submit
  body: { "text": string, "author_id"?: string }
  returns: {
    "content_id": string,
    "attribution_result": "ai" | "human" | "uncertain",
    "confidence_score": float,       // 0.0-1.0, P(AI)
    "label_text": string,
    "signals": { "llm_score": float, "stylometric_score": float },
    "timestamp": string
  }

POST /appeal
  body: { "content_id": string, "creator_reasoning": string }
  # (field named creator_reasoning, not reasoning, per Milestone 5 spec)
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

No dedicated `GET /content/:id` endpoint — see Decision Log #2.

---

### 5. Architecture Diagram

```
SUBMISSION FLOW
───────────────
                 raw text
   POST /submit ─────────────► [Rate Limiter]
                                     │ (pass)
                                     ▼
                              [Flask API Layer]
                                     │ raw text
                       ┌─────────────┴─────────────┐
                       ▼                            ▼
              [Signal 1: Groq LLM]         [Signal 2: Stylometric]
                 llm_score (0-1)              stylo_score (0-1)
                       │                            │
                       └─────────────┬──────────────┘
                                     ▼
                        [Confidence Scoring Module]
                      (agreement-band combine, see below)
                                     │ combined P(AI) score
                                     ▼
                          [Label Generator]
                    (maps score → one of 3 label variants
                     + dynamic direction text)
                                     │ label text + score
                                     ▼
                           [Audit Logger] ──► (SQLite/JSON store)
                                     │
                                     ▼
                        JSON Response to caller
              (content_id, result, score, label, signals)


APPEAL FLOW
───────────
                content_id + reasoning
  POST /appeal ─────────────────────► [Flask API Layer]
                                             │
                                             ▼
                                  [Lookup original decision]
                                             │ found
                                             ▼
                                  [Status Update: → under_review]
                                             │
                                             ▼
                                     [Audit Logger] ──► (append entry,
                                                          linked to original)
                                             │
                                             ▼
                                  JSON Response
                        (content_id, status: under_review, confirmed)
```

---

### Decision Log (Milestone 1)

1. **Raw text storage:** Store the **full raw text** in the audit log
   (minimum viable retention). Rationale: audit-trail integrity, and
   potential future value for fine-tuning (especially text tied to
   *resolved* appeals, which act as corrected ground-truth labels). In a
   real production system this would be paired with an explicit
   retention/consent policy — noted here as a deliberate tradeoff, not an
   oversight.
2. **`GET /content/:id`:** **Skipped.** The internal lookup-by-content_id
   logic needed for `/appeal` already covers this need. No public endpoint
   is exposed for it in this project. Purely additive if a later stretch
   feature (e.g. analytics dashboard) wants it.
3. **Confidence combination — Agreement-Band Approach** (chosen over a
   flat weighted average, which can hide disagreement between signals):
   - `spread = |llm_score - stylo_score|`
   - If `spread < 0.2` (signals agree): combined score = midpoint of the
     two scores.
   - If `spread >= 0.2` (signals disagree): pull the combined score toward
     `0.5` (the uncertain zone) rather than averaging — disagreement
     itself is treated as evidence of low confidence, regardless of what
     a plain average would produce.
   - This directly encodes the principle from the false-positive scenario
     above: disagreement → move toward uncertain, don't just split the
     difference.

---

## Milestone 2: Spec Before Code

### 1. Detection Signals

Two signals, each independently scoring the same submitted text on a
`0.0–1.0` scale representing `P(AI)` — probability the text is
AI-generated (not a binary flag; see rationale in Milestone 1 §2).

| Signal | Measures | Output |
|---|---|---|
| **Signal 1 — LLM judgment** (Groq `llama-3.3-70b-versatile`) | Holistic semantic/stylistic judgment of AI-likelihood (hedging, generic phrasing, structural predictability) | `llm_score` ∈ [0.0, 1.0] |
| **Signal 2 — Stylometric heuristics** (pure Python) | Statistical fingerprints: sentence length variance, type-token ratio, punctuation burstiness, avg. word length, sentence-opener repetition | `stylo_score` ∈ [0.0, 1.0] |

**Combination — Agreement-Band Approach** (chosen over flat weighted
average; see Milestone 1 Decision Log #3):

```
spread = |llm_score - stylo_score|

if spread < 0.2:
    combined_score = (llm_score + stylo_score) / 2   # signals agree — trust the midpoint
else:
    combined_score = 0.5 + (midpoint - 0.5) * (1 - spread)
    # signals disagree — pull the result toward 0.5 (uncertain),
    # proportional to how much they disagree
```

This means: agreeing signals produce a confident, extreme score;
disagreeing signals get automatically dampened toward uncertainty rather
than blindly averaged.

### 2. Uncertainty Representation

- `combined_score` is a **continuous value**, not a binary label. A score
  of `0.6` means: "weighted analysis places this text closer to the
  midpoint than to either extreme — genuine ambiguity, not a confident
  call in either direction." It is *not* "60% of the text is AI" — it's a
  single verdict-level probability estimate for the whole submission.
- **Calibration approach:** raw signal outputs (Groq's stated
  probability + the stylometric composite score) are used directly as
  inputs to the agreement-band formula above, rather than passed through
  a separate calibration model. This project does not implement formal
  statistical calibration (e.g. Platt scaling) — that would require a
  labeled validation dataset, which is out of scope. This is documented
  as a known limitation, not hidden.
- **Testing whether scores are meaningful:** verify with a small labeled
  test set (a handful of known-AI outputs, a handful of known-human
  excerpts, and a few ambiguous/edited samples) and confirm: (a) clearly
  AI text scores consistently high, (b) clearly human text scores
  consistently low, (c) ambiguous/edited text lands in the uncertain
  band rather than at either extreme. Documented in README with example
  inputs/outputs.
- **Thresholds:**

| Score range | Result | Label variant | Direction (if uncertain) |
|---|---|---|---|
| ≥ 0.80 | `ai` | High-confidence AI | — |
| 0.50 < score < 0.80 | `uncertain` | Uncertain | "AI-generated" |
| = 0.50 exactly | `uncertain` | Uncertain | "no clear lean" |
| 0.20 < score < 0.50 | `uncertain` | Uncertain | "human-written" |
| ≤ 0.20 | `human` | High-confidence human | — |

### 3. Transparency Label Design

Exact text for all three variants (`{score}` and `{direction}` are
runtime-filled):

| Variant | Exact text |
|---|---|
| **High-confidence AI** | "This content is likely **AI-generated**. Our analysis found strong, consistent signals of AI authorship (confidence: **{score}**)." |
| **High-confidence human** | "This content is likely **human-written**. Our analysis found strong, consistent signals of human authorship (confidence: **{score}**)." |
| **Uncertain** | "We're **not confident** in this content's origin. Our signals give mixed results, leaning slightly toward **{direction}** (confidence: **{score}**). Treat this result with caution." |

`{direction}` ∈ {"AI-generated", "human-written", "no clear lean"} per the
threshold table above.

### 4. Appeals Workflow

- **Who can appeal:** the original creator/submitter of the content (identified
  via `author_id` captured at submission time, or the `content_id` alone
  if no author system is in place — appeals are content-scoped, not
  identity-gated, for this project's scope).
- **What they provide:** `content_id` + free-text `reasoning` explaining
  why they believe the classification is wrong.
- **What the system does on receipt:**
  1. Looks up the original decision by `content_id` (must exist).
  2. Flips content status: `classified` → `under_review`.
  3. Appends a new audit log entry: appeal timestamp, reasoning text,
     status change, linked to the original decision's log entry (shared
     `content_id`).
  4. Does **not** trigger automated re-classification (explicitly out of
     scope per spec) — this is a queue for human review.
- **What a human reviewer would see in the appeal queue:** the original
  submission (raw text), the original signals + combined score + label
  shown, the creator's appeal reasoning, and the current status
  (`under_review`). This project does not build a reviewer UI — the
  `GET /log` endpoint filtered/read manually serves as the "queue" for
  this project's scope, with a note in the README on how a real reviewer
  UI would consume the same data.

### 5. Anticipated Edge Cases

1. **Very short submissions** (e.g. a haiku or 3-line poem). The
   stylometric signal needs enough text to compute meaningful
   sentence-length variance and vocabulary diversity; under roughly 50
   words these stats are noisy and can swing to a misleadingly extreme
   score in either direction. Mitigation: flag short submissions in the
   response (not blocking) so the label can note reduced reliability.
2. **Human writing with deliberate repetition and simple vocabulary**
   (a villanelle, a children's book manuscript, a chant-like protest
   poem). Low vocabulary diversity and repeated structure are stylometric
   hallmarks the heuristic associates with AI, but here they're an
   intentional literary device, not evidence of machine authorship. This
   is a known blind spot of Signal 2 (see Milestone 1 §2) that the
   agreement-band combination only partially offsets — if the LLM signal
   independently recognizes the human craftsmanship, disagreement pulls
   the result toward "uncertain" rather than a false "high-confidence AI."

---

## Architecture

*(Diagram and narrative from Milestone 1 — carried forward as the
reference for AI-assisted code generation in Milestones 3–5.)*

**Submission flow, briefly:** raw text passes through the rate limiter,
then both detection signals independently, then the confidence scoring
module (agreement-band combine), then the label generator, then the audit
logger, before the structured response is returned to the caller.

**Appeal flow, briefly:** an appeal looks up the original decision by
`content_id`, flips status to `under_review`, and appends a linked audit
log entry — no automated re-classification occurs.

```
SUBMISSION FLOW
───────────────
                 raw text
   POST /submit ─────────────► [Rate Limiter]
                                     │ (pass)
                                     ▼
                              [Flask API Layer]
                                     │ raw text
                       ┌─────────────┴─────────────┐
                       ▼                            ▼
              [Signal 1: Groq LLM]         [Signal 2: Stylometric]
                 llm_score (0-1)              stylo_score (0-1)
                       │                            │
                       └─────────────┬──────────────┘
                                     ▼
                        [Confidence Scoring Module]
                      (agreement-band combine, see below)
                                     │ combined P(AI) score
                                     ▼
                          [Label Generator]
                    (maps score → one of 3 label variants
                     + dynamic direction text)
                                     │ label text + score
                                     ▼
                           [Audit Logger] ──► (SQLite/JSON store)
                                     │
                                     ▼
                        JSON Response to caller
              (content_id, result, score, label, signals)


APPEAL FLOW
───────────
                content_id + reasoning
  POST /appeal ─────────────────────► [Flask API Layer]
                                             │
                                             ▼
                                  [Lookup original decision]
                                             │ found
                                             ▼
                                  [Status Update: → under_review]
                                             │
                                             ▼
                                     [Audit Logger] ──► (append entry,
                                                          linked to original)
                                             │
                                             ▼
                                  JSON Response
                        (content_id, status: under_review, confirmed)
```

---

## AI Tool Plan

**Milestone 3 — Submission endpoint + first signal**
- *Spec sections provided to AI tool:* Detection Signals (§1) + Architecture
  diagram.
- *What I'll ask for:* a Flask app skeleton (routing, request validation)
  plus the Signal 1 function (Groq API call wrapper that sends text and
  parses a `llm_score` from the response).
- *Verification:* test the Signal 1 function directly with a few known
  inputs (a clearly AI-generated paragraph, a clearly human excerpt)
  before wiring it into the `/submit` endpoint — confirm scores land in
  sane ranges and the function handles Groq API errors/timeouts
  gracefully.

**Milestone 4 — Second signal + confidence scoring**
- *Spec sections provided:* Detection Signals (§1) + Uncertainty
  Representation (§2) + Architecture diagram.
- *What I'll ask for:* the stylometric signal function (pure Python, no
  external libraries) and the confidence scoring module implementing the
  agreement-band formula.
- *Verification:* run both signals + combination on a small labeled test
  set (clearly AI, clearly human, ambiguous/edited samples) and confirm
  scores vary meaningfully in the expected direction, and that
  disagreement between signals visibly pulls the combined score toward
  0.5 rather than averaging blindly.

**Milestone 5 — Production layer**
- *Spec sections provided:* Transparency Label Design (§3) + Appeals
  Workflow (§4) + Architecture diagram.
- *What I'll ask for:* the label generation logic (mapping combined score
  → one of the 3 label variants + dynamic direction/score text) and the
  `/appeal` endpoint (lookup, status update, linked audit log entry).
- *Verification:* construct test inputs that deliberately land in each of
  the 5 threshold bands to confirm all three label variants (and both
  uncertain directions + the exact-0.5 edge case) are reachable and
  render the exact documented text; submit a test appeal and confirm
  status flips to `under_review` and a linked log entry appears in
  `GET /log`.

---

## Stretch Features

### Stretch 1: Ensemble Detection (design — pre-implementation)

**Goal:** incorporate 3+ detection signals with a documented weighting or
voting approach, per the stretch feature requirement.

**New Signal 3 — Punctuation & structural formatting patterns** (pure
Python, no external libraries):
- *What it measures:* em-dash/en-dash usage rate, paragraph-length
  uniformity, list/bullet formatting artifacts, and density of
  transitional connector words beyond Signal 2's hedge-phrase list
  (e.g. "however," "consequently," "additionally" used as sentence
  openers).
- *Why independent of Signal 2:* Signal 2 already covers sentence-length
  variance, vocabulary diversity, and a fixed hedge-phrase list.
  Signal 3 targets a genuinely different axis — punctuation/formatting
  *habits* rather than sentence-level statistics or vocabulary — so a
  text could score differently on this axis even with identical
  vocabulary diversity or sentence variance.
- *Blind spot:* like Signal 2, sensitive to short texts (not enough
  punctuation/structure to sample); also sensitive to genre — some human
  writing genres (technical docs, legal writing) legitimately use heavy
  connector-word density and uniform structure.

**Combination — Weighted Voting** (extends, rather than replaces, the
Milestone 1 agreement-band approach — pairwise spread doesn't generalize
cleanly to 3+ inputs, so this uses variance across all signals instead):

```
weights = { llm: 0.5, stylometric: 0.3, structural: 0.2 }
# Weights reflect empirical trust established in Milestone 4 testing:
# the LLM signal showed the clearest directional accuracy; stylometric
# was reweighted once already after a calibration finding; structural
# is the newest/least-tested signal, so it gets the smallest voice.

weighted_score = sum(score_i * weight_i for each signal i)

variance = population variance of the 3 raw signal scores
if variance is high (signals spread out):
    combined_score = dampened toward 0.5, proportional to variance
    (same philosophy as the pairwise agreement-band: broad disagreement
    across signals should reduce confidence, not just average through it)
else:
    combined_score = weighted_score
```

This is implemented as a separate `combine_ensemble_scores()` function
(in a new `ensemble_scoring.py`) rather than modifying the existing
Milestone 4 `confidence_scoring.py`, so the original 2-signal pairwise
agreement-band approach — already built, tested, and documented as the
core project requirement — remains intact and independently inspectable.
`app.py` is updated to call the ensemble path as the live pipeline once
this stretch feature is complete.

**Status: ✅ Complete.** Verified against real submissions — see README
Stretch 1 section for actual results and the independence finding for
Signal 3.

### Stretch 2: Provenance Certificate (design — pre-implementation)

**Goal:** a "verified human" credential a creator can earn through an
additional verification step, including how it's displayed on content.

**Verification method chosen: Writing sample analysis.** A creator
submits 3+ past writing samples via a new `POST /verify` endpoint. The
system checks two things:

1. **Each sample individually scores human-leaning** — runs the full
   3-signal ensemble pipeline (same as `/submit`) on every sample and
   requires the **average** combined score across all samples to be
   ≤ 0.45 (a bit below the true midpoint of 0.5, chosen leniently since
   Stretch 1 testing showed genuinely human text often scores in the
   0.24–0.41 range with the current signals, not near 0.0 — see
   Confidence Scoring §2 calibration findings).
2. **Stylistic consistency across samples** — computes the population
   variance of the Signal 2 (stylometric) score across all submitted
   samples. Low variance suggests a single, consistent personal writing
   voice across different pieces (a weak but real proxy — actual identity
   verification is out of scope for this project). Threshold: variance
   ≤ 0.03 (deliberately looser than the single-submission ensemble
   agreement threshold of 0.02, since comparing *across different pieces
   of writing on different topics* naturally introduces more variation
   than 3 signals agreeing on one piece).

Both checks must pass to issue a certificate. This is an intentionally
simple, explainable proxy — not real identity verification — and is
documented as such rather than oversold.

**Certificate record:** `{certificate_id, creator_id, issued_at,
sample_count, avg_confidence_score, consistency_variance}`, stored in
`certificates.json` (same JSON-file-store pattern as the audit log) and
logged as an audit entry (`event_type: "certificate_issued"`).

**Endpoints:**
```
POST /verify
  body: { "creator_id": string, "samples": [string, string, string, ...] }
  returns: { "verified": bool, "certificate_id"?: string, "reason"?: string, ... }

GET /certificate/<creator_id>
  returns: certificate record, or 404 if not verified
```

**Display on content:** rather than altering the 3 core transparency
label variants (which must remain verbatim per the project's format
requirement), the verified badge is a **separate additive field** on the
`/submit` response: `"creator_verified": true/false`, and when true, a
fixed badge text:

> "✓ Verified Human Creator — this creator has completed Provenance
> Guard's writing-sample verification process."

This keeps the label contract intact while still surfacing the
credential wherever content is shown.