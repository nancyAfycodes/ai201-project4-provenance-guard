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

## Milestone 2

*(To be added before Milestone 2 work begins.)*
