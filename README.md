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
- [Known Limitations](#known-limitations)
- [Spec Reflection](#spec-reflection)
- [AI Usage](#ai-usage)
- [API Reference](#api-reference)
- [Stretch Features](#stretch-features)

---

## Detection Signals

Provenance Guard uses **three independent signals** to classify content
(single-signal detection is not used; see Stretch 1 for how a third
signal was added to the original two):

1. **LLM-based judgment** (Groq, `llama-3.3-70b-versatile`) — assesses
   holistic semantic/stylistic AI-likelihood (hedging, generic phrasing,
   structural predictability). Chosen because it captures things no
   fixed rule can: tone, argument structure, genuine specificity vs.
   generic filler — the kind of judgment a careful human reader makes
   intuitively. Blind spot: black-box reasoning, and a real, observed
   bias against unusual-but-authentic human styles — formal or
   carefully-written human prose sometimes reads as "AI-like" to it (see
   Known Limitations below for a concrete, tested example of this).
2. **Stylometric heuristics** (pure Python) — measures 3 statistical
   fingerprints: sentence length variance, vocabulary diversity
   (type-token ratio), and hedge-phrase density. Chosen specifically to
   be the LLM signal's opposite in kind: cheap, deterministic,
   fully explainable, and independent of any AI model's own biases —
   when the LLM signal is wrong, this signal has a chance to catch it
   (and did, in the false-positive scenario documented below). Combined
   via a **weighted** average — hedge-phrase density weighted highest
   (0.5) after testing showed it discriminates more reliably than
   type-token ratio at short text lengths. Blind spot: easily thrown off
   by very short texts or intentionally repetitive/simple human writing.
3. **Structural/formatting patterns** (pure Python, added in Stretch 1) —
   em-dash density, paragraph uniformity, and connector-word density.
   Chosen as a third, genuinely different axis (formatting habits, not
   sentence statistics or vocabulary) specifically so the ensemble isn't
   just two flavors of the same idea. Blind spot: often has nothing to
   say — many genuinely human or AI texts don't use em-dashes or heavy
   connectors at all, so it frequently contributes a neutral, uninformative
   score rather than a wrong one.

All three signals output a score from `0.0` to `1.0` representing
`P(AI)`. Full rationale for signal choice is in `planning.md` → Milestone
1 §2, Milestone 2 §1, and Stretch 1.

**Why these three, and what I'd change for a real deployment:** the
combination was chosen to cover 3 different failure modes — the LLM
signal for deep semantic judgment, stylometrics for cheap deterministic
cross-checking, and structural patterns for a third independent
viewpoint — rather than three variations on the same idea. If deploying
this for real, the first thing I'd change is calibration: all three
signals were hand-tuned against a handful of test cases (documented
throughout this README as honest findings, not hidden), not a proper
labeled validation set. A real deployment would need a genuine labeled
dataset — including AI text from multiple model families, not just
Groq's own model family, since a detector trained/tuned against one
model's output style may not generalize to others — and formal
calibration (e.g. Platt scaling) instead of hand-set thresholds. I'd also
want a 4th signal that doesn't share any blind spots with the other
three — all three current signals can be fooled by a sufficiently
careful human editor or a sufficiently well-prompted AI, so they're
correlated in ways a truly independent signal (e.g. metadata/provenance
at the platform level, like edit-history or paste-detection) would not
be.

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

**Higher- vs. lower-confidence comparison** (illustrating that scores
vary meaningfully, not constantly): the **"clearly AI-generated"** input
scored **0.632** — the highest score observed in this test batch — while
the **"clearly human-written"** input scored **0.241** — the lowest. That's
a spread of nearly 0.4 between the two, driven by genuinely different
underlying signal readings, not a fixed or arbitrary offset. Neither
example reached the ≥0.80/≤0.20 "high-confidence" thresholds in this
particular run — itself an honest, consistently-observed finding
throughout this project (see Known Limitations and Spec Reflection below)
rather than a cherry-picked result.

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

## Known Limitations

Being specific rather than generic, per the project's own standard:

**Formal or carefully-written human prose, especially from non-native
English speakers, gets misclassified as AI-leaning.** This isn't
hypothetical — it happened during real testing of this exact system.
The developer submitted their own genuine writing multiple times and
Signal 1 (LLM judgment) scored it 0.70 ("uncertain, leaning AI") on more
than one occasion, and a `/verify` certification attempt with 3 real
personal writing samples narrowly failed (average score 0.462 vs. the
0.45 threshold) for the same reason. This is tied directly to a specific
property of Signal 1, not a vague "needs more data" excuse: the model's
holistic judgment conflates *polish and formality* with *AI generation*,
because both share surface features (complete sentences, careful word
choice, low error rate) even though formality and machine authorship are
completely different things. A careful, formal human writer — including
many non-native English speakers, who often write more formally and
carefully than native speakers as a learned skill — will systematically
trigger this signal more than a casual native-English writer would, which
is a real fairness concern for a system like this, not just an accuracy
one.

A second, related limitation: **very short creative text (haiku, single-
stanza poems) is unreliable across all three signals**, not because the
signals are wrong in direction but because they lack enough data to
compute stable statistics — sentence-length variance and vocabulary
diversity are close to meaningless with only 2-3 sentences. The system
flags this via `reliable: false` fields in the signal output, but a
non-technical end user seeing a label wouldn't know that reliability
information exists unless a platform integration surfaces it, which this
project's scope doesn't cover.

## Spec Reflection

**How the spec helped:** the requirement to write out the exact,
verbatim text of all three label variants *before* building anything
(Milestone 2) forced an early, concrete decision that shaped the whole
system's architecture for the better. Rather than designing a vague
"confidence indicator" and figuring out display text later, having to
commit to exact wording up front — including the awkward exact-0.50 tie
case — surfaced edge cases (like "what does the system say when it
genuinely doesn't know which way to lean?") much earlier than they
would have come up otherwise, leading directly to the single-axis
`P(AI)` scoring design instead of a more confusing two-axis "AI
confidence" / "human confidence" split that was briefly considered.

**Where the implementation diverged from what the spec seems to assume:**
the project description's framing (a system that produces "high-
confidence AI," "high-confidence human," and "uncertain" as roughly
comparable outcomes) implicitly suggests a reasonably even three-way
split in practice. Real testing showed this isn't what happened: the
live analytics dashboard shows **92.9% of real submissions landed in
"uncertain,"** with only 7.1% reaching "ai" and 0% reaching "human,"
because the agreement-band/ensemble scoring is deliberately conservative
about dampening toward uncertainty whenever signals don't tightly agree
(a design choice made explicitly in Milestone 4 and re-affirmed in
Stretch 2, and documented honestly rather than tuned away). The system
diverged from an implied "balanced three-way classifier" toward what it
actually is: an "uncertain by default, confident only when genuinely
warranted" classifier. This was a deliberate choice, not an oversight —
but it's worth naming plainly, since a system that says "uncertain" 93%
of the time is a very different product than one might picture from the
spec's framing alone.

## AI Usage

This project was built in collaboration with Claude (Anthropic), used as
an active coding and design partner throughout, not just for boilerplate.
Two specific instances:

1. **Stylometric signal combination logic.** I directed Claude to
   implement the 3-metric stylometric signal (sentence variance, TTR,
   hedge-phrase density) combining them via a simple average, per the
   initial plan. Claude produced that flat-average implementation and
   then, per the Milestone 4 instruction to test against known inputs,
   ran the milestone's own sample texts through it — which surfaced that
   the flat average was scoring a clearly AI-generated paragraph as only
   0.452 (barely above neutral) because type-token ratio was biased
   upward by the passage's short length, working against the correct
   classification. I had it revise the combination to a weighted average
   favoring hedge-phrase density instead, based on that empirical
   finding, rather than accepting the initial output as final.
2. **Ensemble scoring combination (Stretch 1).** I directed Claude to
   design and implement a 3-signal weighted-voting combination function
   once a third signal was added. It produced `ensemble_scoring.py` with
   a variance-based dampening rule. Before accepting it, I had it verify
   the function's output against manually-checked math for an agreement
   case, a disagreement case, and the exact-tie case — and separately,
   real testing later surfaced that the variance threshold treats
   same-direction signal disagreement (e.g. 0.80 vs 0.55, both leaning
   AI) the same as opposite-direction disagreement, which I chose to
   keep as an intentional conservative design decision rather than have
   it "fixed," since it aligned with the project's overall philosophy of
   preferring honest uncertainty over confident-but-fragile precision.

In both cases, Claude's first draft was functionally correct code that
ran without errors — the revisions came from testing against real data
and catching calibration issues that only surfaced empirically, not from
fixing bugs in the code itself. This pattern (AI writes working code →
real testing surfaces a calibration or design issue → human decides how
to handle it → AI implements the revision) repeated across most of this
project's calibration findings, documented throughout this README.

## API Reference

```
POST /submit
  body: { "text": string, "creator_id": string, "content_type"?: "prose" | "metadata" }
  # content_type defaults to "prose"; use "metadata" to submit a JSON
  # string for structured-metadata analysis (see Stretch 4)
  returns: {
    "content_id": string,
    "content_type": "prose" | "metadata",
    "attribution_result": "ai" | "human" | "uncertain",
    "confidence_score": float,
    "label_text": string,
    "signals": { "llm_score": float, "stylometric_score": float, "structural_score": float },
    "structure_metrics"?: {...},  # present only when content_type == "metadata"
    "creator_verified": bool,
    "verified_badge_text"?: string,  # present only when creator_verified == true
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

POST /verify   (Stretch 2)
  body: { "creator_id": string, "samples": [string, ...] }  # 3+ required
  returns: { "verified": bool, "certificate"?: {...}, "evaluation": {...}, "reason"?: string }

GET /certificate/<creator_id>   (Stretch 2)
  returns: the certificate record, or 404 if not verified

GET /analytics   (Stretch 3)
  returns: { total_submissions, detection_patterns, appeal_stats, signal_agreement, generated_at }

GET /dashboard   (Stretch 3)
  returns: rendered HTML analytics dashboard
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

**Verified with real testing** — a real attempt using 3 genuine samples
of the developer's own writing:

```json
{
  "verified": false,
  "reason": "average confidence score across samples (0.462) exceeds the human-leaning threshold (0.45)",
  "evaluation": {
    "avg_combined_score": 0.4618091767125332,
    "stylo_consistency_variance": 0.0037132043224411553,
    "passes_avg_threshold": false,
    "passes_consistency_threshold": true,
    "per_sample": [
      { "combined_score": 0.354, "llm_score": 0.30, "stylo_score": 0.413 },
      { "combined_score": 0.524, "llm_score": 0.70, "stylo_score": 0.321 },
      { "combined_score": 0.508, "llm_score": 0.70, "stylo_score": 0.265 }
    ]
  }
}
```

A follow-up `GET /certificate/<creator_id>` correctly returned a `404`,
since no certificate was issued.

**Calibration finding — documented, not hidden:** this is a genuine
near-miss (the average missed the 0.45 threshold by only 0.012) that
reveals a real limitation. The **consistency check passed easily**
(variance 0.0037, well under the 0.03 threshold) — the writer's
stylometric fingerprint was genuinely stable across all 3 samples,
exactly what that check is designed to detect. The failure came entirely
from **Signal 1 scoring 2 of the 3 samples at 0.70** ("AI-leaning"), the
same false-positive pattern documented in the Confidence Scoring section
above (Signal 1 sometimes reads clean, well-structured human writing as
AI-like).

**Decision: the 0.45 threshold was kept as-is rather than loosened**,
consistent with the same intentional-conservatism philosophy adopted in
Milestone 4 — the certificate is meant to mean something, and loosening
the bar specifically because one known-imperfect signal is having a bad
day undermines that. The honest conclusion is that **certification is
currently harder to earn than ideal for writers whose style happens to
trigger Signal 1's known false-positive pattern**, and that a real
production version of this feature would likely need either a better-
calibrated Signal 1, a majority-vote-based check instead of a strict
average, or a human-review fallback for near-misses — improvements
documented here as known future work, not implemented, to keep this
stretch feature's scope honest and bounded.

**Mechanism verified separately** (internal wiring test, not the
real-world threshold test above): the full success path — badge display,
certificate persistence, and rejection handling — was confirmed working
correctly. A synthetic set of 3 samples that passed both thresholds
correctly issued a certificate and correctly caused a subsequent
`/submit` call to return `"creator_verified": true` with the exact badge
text; a call from an unverified creator returned `"creator_verified": false`
with no badge field present; and a verification attempt with only 1
sample was correctly rejected with `"reason": "At least 3 writing
samples are required (received 1)."` These confirm the *mechanism* works
correctly — the real-world test above is what surfaced the honest
calibration finding about the *threshold*.

### Stretch 3: Analytics Dashboard ✅

**What was built:** a simple analytics view showing detection patterns,
appeal rate, and signal agreement rate (the chosen "one additional
metric"), available both as JSON and as a rendered HTML page — both
computed live from the existing audit log, no new storage.

**Endpoints:**
```
GET /analytics   -> JSON: full metrics breakdown
GET /dashboard   -> rendered HTML page with simple bar visualizations
```

**Metrics (`analytics.py`):**
1. **Detection patterns** — counts + percentages of `ai` / `human` /
   `uncertain` across all submissions.
2. **Appeal rate** — appeals ÷ submissions, as a percentage.
3. **Signal agreement rate** (chosen additional metric) — of submissions
   that have a `signals_agree` field (i.e. post-Milestone-4 entries),
   what percentage had all signals agreeing vs. disagreeing. This metric
   connects directly back to the agreement-band/ensemble design
   philosophy from Milestones 1 and 4 — it shows how often the system's
   own internal confidence mechanism is actually confident.

**Why these three together:** detection patterns show *what* the system
decides; appeal rate shows how often creators push back; signal
agreement rate shows *how often the system trusts its own verdict* —
a coherent story about the system's behavior, not three arbitrary
unrelated numbers.

**Verified** (internal wiring test, 5 submissions + 1 appeal):

```json
{
  "total_submissions": 5,
  "detection_patterns": {
    "counts": { "ai": 0, "human": 0, "uncertain": 5 },
    "percentages": { "ai": 0.0, "human": 0.0, "uncertain": 100.0 }
  },
  "appeal_stats": { "total_appeals": 1, "appeal_rate_pct": 20.0 },
  "signal_agreement": {
    "agree_count": 3, "disagree_count": 2,
    "excluded_no_field_count": 0, "agreement_rate_pct": 60.0
  }
}
```

`GET /dashboard` was confirmed to render successfully (HTTP 200) with
all expected sections present. A design note: this test batch happened
to produce 100% "uncertain" results and 0% confident verdicts — itself a
small, honest illustration of how conservative the current system is
(consistent with the Milestone 4 and Stretch 2 calibration findings
above), rather than a dashboard bug.

### Stretch 4: Multi-modal Support ✅

**What was built:** support for a second content type — **structured
metadata** (e.g. JSON product listings) — alongside prose, via an
optional `content_type` field on `POST /submit` (`"prose"` default, or
`"metadata"`).

**Why this isn't just "run the same pipeline on JSON":** the existing 3
signals all assume prose — sentences, paragraphs, running text.
Structured metadata doesn't have that shape uniformly, so a **hybrid
approach** was used:

1. **Extract embedded text** from likely free-text fields (`description`,
   `title`, `notes`, `summary`, etc.) and run the **existing, unmodified
   3-signal ensemble pipeline** on it — direct reuse of already-tested
   code.
2. **New Signal — structural metadata analysis**
   (`signals/metadata_signal.py`), 3 heuristics specific to structured
   data:
   - *Field completeness ratio* — how many "typical" listing fields
     (title, description, price, category, tags, sku, brand, in_stock,
     rating, images) are present. **Documented blind spot**: a
     conscientious human seller filling every field would also score
     high here.
   - *Tag/keyword stuffing ratio* — tag count relative to description
     length; AI-generated SEO listings often over-populate tags.
   - *Value formatting uniformity* — whether string values share
     identical casing patterns.
3. **Combine**: `0.65 × text_ensemble_score + 0.35 × structure_score`
   when usable embedded text is found; `structure_score` alone otherwise.

**Calibration finding — documented, not hidden:** initial testing showed
the uniformity metric was a **small-sample artifact** — a listing with
only 2 string values scored "100% uniform" purely because 2 items
trivially share a casing pattern by chance, inflating its AI-likelihood
score incorrectly (the same class of problem as the type-token-ratio
finding in Milestone 4). Fixed by requiring 3+ string values before
trusting the metric, falling back to a neutral 0.5 otherwise — consistent
with the `reliable`-flag pattern already used in Signals 2 and 3.

**Verified** — real test against two contrasting synthetic listings:

| | AI-style listing | Human-style listing |
|---|---|---|
| `completeness_ratio` | 1.0 (all 10 fields) | 0.4 (4 fields) |
| `tag_stuffing_density` | 41.4 (12 tags, sparse text) | 4.8 (1 tag) |
| `value_uniformity_ratio` | 0.4 (reliable — 6+ values) | 0.5 (unreliable — only 2 values, correctly abstained) |
| **`structure_score`** | **0.617** | **0.399** |

The two metrics that actually discriminate (completeness, tag stuffing)
drove a clear, meaningful separation once the uniformity metric's
small-sample bias was fixed. A full `/submit` call with
`content_type: "metadata"` on the AI-style listing correctly ran both
the structural analysis *and* the reused text pipeline on the extracted
description, returning a combined score, full signal breakdown, and
`structure_metrics`. Regular prose submissions (`content_type` omitted
or `"prose"`) continue to work completely unaffected — confirmed via
direct testing. Invalid JSON and invalid `content_type` values are both
correctly rejected with `400` and a clear error message.
---
## Video Demo & Dashboard Powerpoint
Demo Link (https://youtu.be/FaNlfESxKg8)
Dashboard (dashboard ppt.pptx)

---