# Design notes: Milestone 1 (written before building)

## 1. The path a submission takes

1. **Arrives at `POST /submit` (`app.py::submit`).** Adds validation. Checks the body is a JSON object, `text` is a non-empty string under the length cap, and `creator_id` is a non-empty string. Bad input gets a 400 and a logged rejection, never a 500.
2. **Signal one (`detector.py::model_signal`).** Adds a model score. It runs gpt2 over the first 400 tokens, computes perplexity, and squashes it to 0-1 (higher = more AI-like). Text too short to score is rejected here.
3. **Signal two (`stylometry.py::style_signal`).** Adds a score from the shape of the text (sentence-length spread, type-token ratio, punctuation density), 0-1, higher = more AI-like.
4. **Combining rule (`scoring.py::combine_signals`).** Adds a single score, `combined_score`. This is where the false-positive trade-off is decided.
5. **Labelling (`scoring.py::score_to_label`).** Adds a guess (`ai` / `human` / `unsure`) and the full sentence a reader sees, using the thresholds in `config.py`.
6. **Audit (`audit.py::log_decision`).** Adds a record, one JSON line in `logs/audit.jsonl` with the content_id, both signal scores, the combined score, the label, and status `decided`.
7. **Response.** Returns the content_id, guess, confidence, label, and both signal scores to the caller.

Appeal path: `POST /appeal` -> `audit.entries_for(content_id)` (does it exist?) -> `audit.log_appeal` (status `under_review`, reasoning stored next to the original decision) -> confirmation.

## 2. The two signals

### Signal one: model signal (`detector.py`)
- **Measures:** how predictable the text is to gpt2 (perplexity), mapped to 0-1.
- **Why it might differ:** language models write by picking likely next words, so their output tends to be low-perplexity. Human writing wanders more: odd word choices and specific details.
- **Blind spot:** predictability is not authorship. It rewards common words in short, ordinary sentences. Second-language writers, younger writers, and anyone writing plainly on purpose score as machine-like. Ornate human prose with rare words scores as human, and so does AI text that has been made to sound unusual. The people it is most likely to wrongly accuse are the ones with the least room to argue back.

### Signal two: style signal (`stylometry.py`)
- **Measures:** the text as text. It uses sentence-length spread, vocabulary repetition, and punctuation density, with no understanding of meaning.
- **Why it might differ:** generated prose tends toward even, mid-length sentences and a narrower vocabulary. Human writing is lumpier.
- **Blind spot:** it cannot read. Anything that changes the shape without changing the substance moves it: typos, chopped-up sentences, a pasted quotation. That is cheap to do on purpose. Type-token ratio also falls with length, so length alone can look like a signal. Short, plain human writing looks uniform to it, which is the same writer signal one is likely to wrongly accuse.

## 3. Tracing the false positive

**Who:** Amara writes in English as a second language. She submits a plain 120-word paragraph she wrote herself.

**What happens (predicted, to be checked in Milestone 4):**
1. Signal one: common vocabulary, short sentences, so low perplexity and a model score of about 0.80.
2. Signal two: even sentence lengths, a small vocabulary, and little punctuation variety, so a style score of about 0.70.
3. Combining rule: about 0.75, above `AI_THRESHOLD` (0.65). Guess: `ai`.
4. Label: the high-confidence AI text.
5. Log: a `decided` entry with all three scores.

**What the label says to her:** "We think this was probably written by an AI." She reads it as an accusation that she cheated, with no score and no explanation of what it was based on.

**What she can do:** send `POST /appeal` with her `content_id` and her reasoning. Her item moves to `under_review` and the appeal is stored beside the original decision. Nothing re-checks it automatically, so the label text itself must tell her an appeal exists and how to use it, which means the response must carry the `content_id`.

**What this decides:**
- The AI threshold must be high, and the "unsure" band wide, because a wrong accusation costs her far more than a missed detection costs the site.
- The high-confidence AI label should require both signals to agree, not just their average.
- The AI label must be worded as an estimate, and must mention appeals.
- Every appeal must always change status and always be logged.

## 4. Endpoint contracts

### `POST /submit`

Request (JSON object):

| Field | Type | Required | Rules |
|---|---|---|---|
| `text` | string | yes | non-empty after trimming; max 20,000 characters |
| `creator_id` | string | yes | non-empty after trimming |

Success, 200:

| Field | Type | Meaning |
|---|---|---|
| `content_id` | string | unique id for this submission (UUID); needed to appeal |
| `guess` | string | `ai`, `human`, or `unsure` |
| `confidence` | float 0-1 | the combined score; **higher = more likely AI**, not confidence in the guess |
| `label` | string | the full sentence a reader sees |
| `model_score` | float 0-1 | signal one, higher = more AI-like |
| `style_score` | float 0-1 | signal two, higher = more AI-like |
| `status` | string | `decided` |

Errors, always JSON `{"error": "<code>", "message": "<plain sentence>"}`:

| Situation | HTTP | `error` |
|---|---|---|
| body missing, not JSON, or not an object | 400 | `invalid_body` |
| `text` missing, empty, or not a string | 400 | `invalid_text` |
| `text` over 20,000 characters | 413 | `text_too_long` |
| `text` too short to score | 422 | `text_too_short` |
| `creator_id` missing or empty | 400 | `invalid_creator_id` |
| detector model unavailable | 503 | `detector_unavailable` |

Every rejection is also written to the log via `audit.log_rejection`.

### `POST /appeal`

Request:

| Field | Type | Required | Rules |
|---|---|---|---|
| `content_id` | string | yes | must exist in the log |
| `reasoning` | string | yes | non-empty after trimming |

Success, 200: `{"content_id": "...", "status": "under_review", "message": "..."}`. The message is a plain sentence saying the appeal was received.

Errors, same shape as above:

| Situation | HTTP | `error` |
|---|---|---|
| body invalid, or a field missing or empty | 400 | `invalid_body` |
| `content_id` not in the log | 404 | `unknown_content_id` |

An appeal on something already `under_review` is accepted and logged again (200).

### Read-back test
Someone building against this needs to know three things without asking me: that `confidence` is the AI-likelihood score, that errors always use the same JSON shape, and that `content_id` from `/submit` is the key for `/appeal`. Each is written above.