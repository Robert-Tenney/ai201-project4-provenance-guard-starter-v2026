# Provenance Guard

> ### 👋 Start here
>
> **New to this repo? Read [RUNNING.md](RUNNING.md) first.**
>
> Once `python test.py` passes, in one terminal:
>
> ```bash
> python app.py
> ```
>
> and in another:
>
> ```bash
> curl -X POST http://127.0.0.1:5000/ping \
>   -H "Content-Type: application/json" \
>   -d '{"message": "hello"}'
> ```
>
> That's a web service. Something sent it text and it sent something back.
> Everything this unit is filling in the middle.

---

## What This Does

A submission takes this path:

1. **Arrives at `POST /submit`** (`app.py::submit`). It is rate limited per `creator_id`, then validated: a JSON object, non-empty `text` under 20,000 characters, non-empty `creator_id`. Bad input gets a clear 4xx JSON error and a logged rejection, never a crash.
2. **Signal one** (`detector.py::model_signal`) runs gpt2 locally and adds a 0-1 score for how predictable the text is.
3. **Signal two** (`stylometry.py::style_signal`) adds a 0-1 score for the shape of the text: sentence-length spread and vocabulary repetition. (Punctuation counted until Milestone 5; see The Improvement.)
4. **The combining rule** (`scoring.py::combine_signals`) turns the two scores into one combined score.
5. **Labelling** (`scoring.py::score_to_label`) maps that score to a guess (`ai`, `human` or `unsure`) and a plain-language sentence for the reader.
6. **The audit log** (`audit.py::log_decision`) appends a JSON line to `logs/audit.jsonl` with the content id, creator id, both signal scores, the combined score, the guess, the label and status `decided`.
7. **The response** returns the `content_id`, guess, confidence, label and both signal scores. If the writer disagrees, `POST /appeal` (`app.py::appeal`) checks the `content_id` exists, appends an appeal entry with status `under_review` and the writer's reasoning beside the original decision, and confirms. The log is append-only, so the original decision is never overwritten.

---

## Signals and Scoring

### Signal one — the model signal

**What it measures:** How predictable the text is to a language model (gpt2) running on my own machine. The measure is perplexity (how surprised the model is by the text), squashed onto 0-1 so that higher means more likely AI. Implemented in `detector.py::model_signal`.

**Why that might differ between human and AI writing:** A language model writes by repeatedly picking a likely next word, so text it produces tends to be text a model finds easy to predict. Human writing wanders more, with odd word choices, abrupt turns, and specific details instead of typical ones.

**What it can't see:** Predictability is not authorship. It rewards common words in short, ordinary sentences, and plenty of real people write that way: second-language writers sticking to vocabulary they're sure of, younger writers, anyone writing plainly on purpose. That writing scores as machine-like. The reverse also happens: dense, ornate human prose full of rare words scores as human, and so can AI text that has been made to sound unusual. The writers this signal is most likely to wrongly accuse are the ones with the least room to argue back.

**What I saw** (`python detector.py`, higher = more AI-like):

| Test text | model_score |
|---|---|
| Human (mine): "What in the name of 'Sweet Home Alabama' was I forced to hear?" | 0.6892 |
| Human (mine): "What in the name of 'Sweet Home Alabama' did I just witness" | 0.7047 |
| AI: the "implementation of the proposed framework" paragraph | 0.8577 |

Signal one alone scored both of my own short sentences above the 0.65 `AI_THRESHOLD`, only about 0.15 to 0.17 below the AI paragraph. On its own it would have accused me of being a machine. A longer, more personal paragraph of mine ("Grandfather's kitchen...") scored 0.2282, clearly human. So signal one separates well on distinctive, specific prose and badly on short, plain sentences.

### Signal two — the style signal

**What it measures:** The shape of the text without reading it, from three measures in `stylometry.py`:
- `sentence_length_spread`: standard deviation of sentence length divided by the mean. Higher = more varied.
- `type_token_ratio`: unique words over total words, on the first 100 words only so length alone can't move it. Higher = more varied vocabulary.
- `punctuation_density`: commas, semicolons, colons, dashes, brackets, quotes, `!` and `?` per word (full stops excluded). Higher = more punctuation.

All three run "higher = more human", so `style_signal` flips each onto a 0-1 "AI-ness" scale (linear between a fully-AI value and a fully-human value) and takes a weighted average: spread 0.5, type-token ratio 0.3, punctuation 0.2. Higher = more likely AI, the same direction as signal one. Under 3 words returns a neutral 0.5, and a one-sentence text drops the spread measure and re-weights the other two.

> **Changed in Milestone 5:** punctuation no longer counts (`W_PUNCT` is now 0.0), so the weights are spread 0.5 and type-token ratio 0.3, scaled to fill the score (0.625 and 0.375). A text with fewer than two sentences is pinned at 0.4, which is what the old formula gave plain one-sentence text. Everything above, and every score recorded before The Improvement section, was produced by the old formula. Why I changed it is under **The Improvement**.

**Why that might differ between human and AI writing:** Generated prose tends toward even, mid-length sentences and a narrower band of vocabulary. Human writing is lumpier, with a three-word sentence next to a forty-word one.

**What it can't see:** It cannot read. Anything that changes the shape without changing the substance moves it: typos, chopped-up sentences, a pasted quotation. That is very cheap to do on purpose. Short, plain human writing also looks uniform to it, which is the same writer signal one is likely to accuse.

**What I saw** (`python stylometry.py`, raw measure values, old formula):

| Text | spread | type-token | punctuation | style_signal |
|---|---|---|---|---|
| AI paragraph (85 words, 5 sentences) | 0.239 | 0.753 | 0.047 | 0.6160 |
| "...did I just witness" (12 words, 1 sentence) | 0.000 | 1.000 | 0.000 | 0.4000 |
| "...was I forced to hear?" (13 words, 1 sentence) | 0.000 | 1.000 | 0.077 | 0.2103 |

- Direction is right: the AI paragraph scored higher than both of my sentences.
- **Type-token ratio did almost nothing.** The AI paragraph's 0.753 sits at the human end of my scale (0.75), so it added no AI-ness, and very short texts score 1.000 because there is little room to repeat a word.
- **One-sentence texts have no spread measure**, so the difference between 0.40 and 0.21 for two near-identical sentences came entirely from one question mark. That is fragile, and it is the short-text blind spot in practice.

*Scale endpoints and weights: unchanged from the starting guesses I set before measuring, until Milestone 5.*

### The combining rule

**The rule:** Take a weighted average of the two signals. If they differ by more than 0.35, clamp the result into the unsure band (never below 0.36 or above 0.64), so a disagreement can never produce a confident label of either kind.

**Why:** Signal one over-scores plain human writing and signal two is easy to fool with typos. When they point opposite ways that is uncertainty, not a verdict. The cost is that AI text caught by only one signal lands in "unsure" instead of "ai". I chose that trade because a wrong accusation costs a writer far more than a missed detection costs the site.

**The numbers:** `WEIGHT_MODEL_SIGNAL = 0.5`, `WEIGHT_STYLE_SIGNAL = 0.5`, `DISAGREEMENT_GAP = 0.35`, `HUMAN_THRESHOLD = 0.35`, `AI_THRESHOLD = 0.65`, all in `config.py`. Equal weights because neither signal has earned more trust.

**Where it lives:** `scoring.py::combine_signals`

**Both signals on the same texts** (rows 1 and 2 calculated from the rule; the rest are real `/submit` output):

| Input | model_score | style_score | combined | gap | guess |
|---|---|---|---|---|---|
| AI paragraph (5-sentence version) | 0.8577 | 0.6160 | 0.7369 | 0.24 | ai |
| Human: "...was I forced to hear?" | 0.6892 | 0.2103 | 0.4498 | 0.48 | unsure (split) |
| AI text (3-sentence version) | 0.8864 | 0.6208 | 0.7536 | 0.27 | ai |
| Human: "...did I just witness" | 0.7047 | 0.4000 | 0.5524 | 0.30 | unsure |
| Human: "Grandfather's kitchen" paragraph | 0.2282 | 0.2472 | 0.2377 | 0.02 | human |
| Human sentence + one added sentence | 0.7905 | 0.5173 | 0.6539 | 0.27 | ai (false positive) |

**A case where my two signals split, and what my rule does with it:** "What in the name of 'Sweet Home Alabama' was I forced to hear?" is a short sentence I wrote. Signal one scored it 0.6892 (AI-like), because it is short and made of common words. Signal two scored it 0.2103 (human-like), because the question mark gave it some punctuation and it has no repeated words. The gap is 0.48, above 0.35, so the clamp applies. The average of 0.4498 is already inside the unsure band, so the guess is "unsure". Signal one alone would have given a confident AI accusation. The rule prevented a false positive on my own writing.

**Where the rule failed:** "What in the name of 'Sweet Home Alabama' was I forced to hear? I honestly do not know what that was." scored model 0.7905 and style 0.5173, combined 0.6539, and got the `ai` label. The signals differed by only 0.27, so the clamp never applied. (The second sentence was written by an AI assistant for testing, so this is a near-miss rather than a clean sample.) The rule only protects writers when the signals disagree; it does nothing when both lean AI-like on short, plain text. I have not moved the thresholds. A candidate fix for unit 8 is requiring both signals to be at least 0.5 before the `ai` label can appear. (I replayed that rule in Milestone 5. It changes nothing on my data; see The Improvement.)

**Against my criteria:** Criterion 2 asks for AI text to score at least 0.30 above human text. Against my two short human sentences the gap is about 0.24 to 0.25; against the longer human paragraph it is about 0.5. That early check used only a handful of inputs; the full 5-and-5 run is in the Run Log below.

---

## Label Variants

| Label | Score range | The exact text a reader sees |
|---|---|---|
| high-confidence human | 0.00 – 0.35 | We think this was probably written by a person. This is an estimate, not a certainty. |
| unsure | 0.35 – 0.65 | We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal. |
| high-confidence AI | 0.65 – 1.00 | Our tools think this may have been written with AI, but they can get this wrong, especially with short or plainly written text. If you wrote it yourself, you can appeal and we will mark it for review. |

The labels are defined in `scoring.py` (`LABEL_HUMAN`, `LABEL_UNSURE`, `LABEL_AI`) and chosen by `scoring.py::score_to_label`. The ranges match `scoring.label_ranges()`. When the signals disagree by more than 0.35 the combining rule clamps the score into 0.36 to 0.64, so a disagreement can only produce "unsure".

**Why I worded the "unsure" one this way:** The person reading it may well have written every word, so it opens with what we can't know rather than what we suspect, and says directly that it is not a judgment about their work. "Plenty of people who write plainly get this result" is the honest reason it happens (signal one scores plain writing as machine-like), and it tells the reader the result isn't about them. It ends by pointing to the appeal. The AI label is hedged too, because my own short human sentences scored above the AI threshold on signal one alone.

**All three labels reached through `/submit`:**

| Label | Reached by | combined |
|---|---|---|
| ai | "implementation of the proposed framework" paragraph | 0.7536 |
| human | "Grandfather's kitchen" paragraph | 0.2377 |
| unsure | "What in the name of 'Sweet Home Alabama' did I just witness" | 0.5524 |

Plain short writing did not reach "human" on its own. I did not move the thresholds to change that.

---

## Sample Run

*These samples were recorded before Milestone 5, with the old style formula.*

**A submission that returns the unsure label**

```powershell
PS> $unsure2 = @{ text = "What in the name of 'Sweet Home Alabama' did I just witness"; creator_id = "me" } | ConvertTo-Json
PS> Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/submit -ContentType "application/json" -Body $unsure2 | ConvertTo-Json
```

```json
{
    "confidence":  0.5524,
    "content_id":  "b4661c2f-ae4d-49a7-a5d2-960d14afd295",
    "guess":  "unsure",
    "label":  "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
    "model_score":  0.7047,
    "status":  "decided",
    "style_score":  0.4
}
```

**A submission that returns the AI label**

```json
{
    "confidence":  0.7536,
    "content_id":  "1dad6013-fca1-4668-b8f5-49823334032a",
    "guess":  "ai",
    "label":  "Our tools think this may have been written with AI, but they can get this wrong, especially with short or plainly written text. If you wrote it yourself, you can appeal and we will mark it for review.",
    "model_score":  0.8864,
    "status":  "decided",
    "style_score":  0.6208
}
```

**A submission that returns the human label**

```json
{
    "confidence":  0.2377,
    "content_id":  "4921a6dd-495c-4372-ab14-12dcbd81cd6c",
    "guess":  "human",
    "label":  "We think this was probably written by a person. This is an estimate, not a certainty.",
    "model_score":  0.2282,
    "status":  "decided",
    "style_score":  0.2472
}
```

**An appeal**

```powershell
PS> $appeal = @{ content_id = $r.content_id; reasoning = "I wrote this myself." } | ConvertTo-Json
PS> Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/appeal -ContentType "application/json" -Body $appeal | ConvertTo-Json
```

```json
{
    "content_id":  "cba375b1-ad9c-40f9-a3f2-17c05ca869d7",
    "message":  "We received your appeal and marked this submission for review.",
    "status":  "under_review"
}
```

**What the log shows afterwards** (the original decision is unchanged and the appeal sits after it):

```powershell
PS> curl.exe "http://127.0.0.1:5000/log?limit=2"
```

```json
{
  "count": 2,
  "entries": [
    {
      "combined_score": 0.5524,
      "content_id": "cba375b1-ad9c-40f9-a3f2-17c05ca869d7",
      "creator_id": "me",
      "guess": "unsure",
      "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
      "model_score": 0.7047,
      "status": "decided",
      "style_score": 0.4,
      "timestamp": "2026-10-29T20:05:45+00:00"
    },
    {
      "content_id": "cba375b1-ad9c-40f9-a3f2-17c05ca869d7",
      "creator_id": "me",
      "event": "appeal",
      "reasoning": "I wrote this myself.",
      "status": "under_review",
      "timestamp": "2026-10-29T20:05:45+00:00"
    }
  ]
}
```

**An appeal against an id that doesn't exist** (HTTP 404):

```json
{
  "error": "unknown_content_id",
  "message": "We couldn't find a submission with that content_id. Check it matches the one you were given."
}
```

**A rejected request.** My first attempts sent the body through `curl.exe` from PowerShell, and the quoting mangled it, so Flask never received a JSON object. The service refused each one with `invalid_body` and logged it, which is the bad-input path working:

```jsonl
{"timestamp": "2026-10-29T18:37:18+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T18:37:38+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T18:38:09+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
```

---

## How I Used AI



**Moment 1: the style signal and the false positive it let through**

- *What I asked for:* An implementation of `stylometry.py` and the combining rule, with the disagreement clamp.
- *What came back:* Three measures with starting-guess scale endpoints, and a rule that clamps to "unsure" when the signals split.
- *What I changed:* Nothing in the code, but running it showed problems the starting guesses hid. Type-token ratio did almost nothing (the AI paragraph scored 0.753, at the human end of my scale), and a human sentence plus one extra sentence scored 0.6539 and was labelled `ai`, because the clamp only fires when the signals disagree. I recorded both as findings instead of moving the thresholds, and noted a "both signals at least 0.5" rule as a fix to try in unit 8.

**Moment 2 (unit 8, Milestone 1): rate limiting and the README write-up**

- *What I asked for:* The completed `config.py` and `app.py` for Milestone 1, and help writing up the Rate Limiting section from my real output.
- *What came back:* Limiter settings of 5 per minute and 50 per day with reasoning, the activated `@limiter.limit(...)` decorator, a small change to the 429 handler so it logs `"unknown"` instead of a non-string `creator_id`, and a draft of the README section.
- *What I checked:* I ran the flood test myself and used its real output. When my first log read-back showed only the `flood_tester` rejections and no decisions, I re-ran the three-text check until `m1_check` decisions appeared, rather than pasting entries that didn't show what the section claimed. *(Edit this to say what you actually changed or decided about the limits yourself.)*



---

## Rate Limiting

**Where:** `config.py` (`RATE_LIMITING_ENABLED = True`, `RATE_LIMIT_PER_MINUTE = 5`, `RATE_LIMIT_PER_DAY = 50`) and the `@limiter.limit(...)` decorator on `app.py::submit`. Counts are kept per `creator_id`, falling back to the caller's address when there is no usable one (`app.py::rate_limit_key`). `GET /health` reports `"rate_limiting": true`.

**Why these numbers:** A real writer submits a piece, reads the label, and maybe edits and resubmits. Five in a minute is already a fast human, and 50 a day is far more than anyone submitting their own work needs. A script mapping my thresholds needs volume. At 50 a day, one creator_id gets 50 probes instead of a thousand, and the per-minute cap stops a burst outright. The cost is that a writer pasting in a dozen drafts in a row gets a 429 and waits a minute. The gap is that the limit follows `creator_id`, so a script that rotates creator_ids is not slowed by it.

**Flood test** (`python run_attacks.py --flood 15`, output saved to `results/flood_2026-11-05_1301.md`):

```
200 200 200 200 200 429 429 429 429 429 429 429 429 429 429
```

Five requests got through, then the per-minute limit of 5 cut in. 10 of 15 were rate limited.

**The rejections are in the log** (`GET /log?limit=20`). Each 429 left an entry. A rejection carries fewer fields than a decision because no decision was made, but it still records who, when, why and which limit:

```json
{
  "creator_id": "flood_tester",
  "event": "rejected",
  "limit": "5 per 1 minute",
  "reason": "rate_limited",
  "status": "rejected",
  "timestamp": "2026-11-05T18:01:30+00:00"
}
```

All ten 429s appear in the log this way, matching the ten 429 status codes above. Without them the flood would have shown up only as five decisions and a gap.

**A decision entry from the same run**, with all nine fields (`timestamp`, `content_id`, `creator_id`, `guess`, `model_score`, `style_score`, `combined_score`, `label`, `status`):

```json
{
  "combined_score": 0.2152,
  "content_id": "5ff5c9b8-67a6-4e99-a47b-fa6ee9f2a90c",
  "creator_id": "flood_tester",
  "guess": "human",
  "label": "We think this was probably written by a person. This is an estimate, not a certainty.",
  "model_score": 0.2588,
  "status": "decided",
  "style_score": 0.1716,
  "timestamp": "2026-11-05T18:01:30+00:00"
}
```

All five `flood_tester` decisions scored identically (0.2588 / 0.1716 / 0.2152), which fits the same text being sent each time and nothing in either signal being random.

**Log check: three different texts read back with `GET /log`:**

I submitted three different pieces of text as `m1_check` and read them back. Each entry has all nine fields: `timestamp`, `content_id`, `creator_id`, `guess`, `model_score`, `style_score`, `combined_score`, `label`, `status`.

```json
{
  "count": 3,
  "entries": [
    {
      "combined_score": 0.4384,
      "content_id": "6fba1788-2d18-4db3-b21b-cf86e82d4495",
      "creator_id": "m1_check",
      "guess": "unsure",
      "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
      "model_score": 0.4767,
      "status": "decided",
      "style_score": 0.4,
      "timestamp": "2026-11-05T18:07:32+00:00"
    },
    {
      "combined_score": 0.6371,
      "content_id": "64445260-a25b-4535-b4ea-7ab9efc37c4b",
      "creator_id": "m1_check",
      "guess": "unsure",
      "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
      "model_score": 0.6132,
      "status": "decided",
      "style_score": 0.6609,
      "timestamp": "2026-11-05T18:07:32+00:00"
    },
    {
      "combined_score": 0.4191,
      "content_id": "b3c6e458-2327-4761-b0db-70c894174c16",
      "creator_id": "m1_check",
      "guess": "unsure",
      "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
      "model_score": 0.705,
      "status": "decided",
      "style_score": 0.1333,
      "timestamp": "2026-11-05T18:07:32+00:00"
    }
  ]
}
```

**Can a decision be reconstructed from an entry without my code?** Yes. Each decision entry holds both signal scores, the combined score, the guess and the exact label the writer saw, plus a `content_id` they can use to appeal. For example, the third entry shows signal one at 0.705 and signal two at 0.1333. Those differ by 0.57, more than the 0.35 gap, so the combining rule clamped the average into the unsure band. A reader can see why the result was "unsure" from the entry alone. It is the same split pattern described under the combining rule: signal one reads plain text as AI-like and signal two pulls it back.

**A rejection is different.** A rate-limit entry has no `content_id`, scores or label, because no decision was made. From it you can reconstruct who was blocked, when, and by which limit (`5 per 1 minute`), but not what they submitted.

---

## Attack Run — Before

**How I ran it:** `python run_attacks.py --set attack_set --label before` against a restarted service with rate limiting on (5 per minute, 50 per day per creator). All 56 rows loaded (32 in `attacks.csv`, 24 in `attacks_malformed_requests.csv`), none were skipped, and the harness auto-marked 0 as BROKE because the service never returned a 5xx or dropped a connection. Full table: `results/attack_run_2026-11-05_1325_before.md`. Log pulled with `GET /log?limit=500` into `results/audit_extract.json`.

**How I marked held or broke.** I used each attack's `targets` column as its goal, and a strict rule:

- **Evasion:** BROKE if AI text got any label other than `ai`. I did not count `unsure` as a hold, because the attack succeeded in avoiding the `ai` label. `unsure` is the cost I chose in my combining rule, but it is still AI text getting through.
- **False positive:** BROKE if real writing got `ai`.
- **Prompt injection:** BROKE if the label was `human`.
- **Malformed:** HELD if the service answered without a 5xx.
- **Flood:** HELD, because the limiter capped it.

### Result

| Family | Attacks | BROKE | HELD |
|---|---|---|---|
| evasion | 10 | 9 | 1 |
| false_positive | 8 | 4 | 4 |
| prompt_injection | 6 | 2 | 4 |
| malformed (text, MF) | 8 | 0 | 8 |
| malformed (requests, RQ) | 12 | 0 | 12 |
| flood_same_creator | 12 | 0 | 12 |
| **Total** | **56** | **15** | **41** |

### Every attack that became a decision (34)

Scores come from `logs/audit.jsonl`. Gap is the absolute difference between the two signals.

| ID | guess | model | style | combined | gap | Mark |
|---|---|---|---|---|---|---|
| EV01 | unsure | 0.1635 | 0.6549 | 0.4092 | 0.49 | BROKE |
| EV02 | unsure | 0.5943 | 0.1601 | 0.3772 | 0.43 | BROKE |
| EV03 | unsure | 0.6662 | 0.3642 | 0.5152 | 0.30 | BROKE |
| EV04 | unsure | 0.6114 | 0.6364 | 0.6239 | 0.03 | BROKE |
| EV05 | human | 0.3212 | 0.1158 | 0.2185 | 0.21 | BROKE |
| EV06 | unsure | 0.5727 | 0.1790 | 0.3759 | 0.39 | BROKE |
| EV07 | unsure | 0.5862 | 0.0000 | 0.3600 | 0.59 | BROKE |
| EV08 | unsure | 0.2452 | 0.6172 | 0.4312 | 0.37 | BROKE |
| EV09 | unsure | 0.7042 | 0.3757 | 0.5400 | 0.33 | BROKE |
| EV10 | ai | 0.9792 | 0.8061 | 0.8926 | 0.17 | HELD |
| FP01 | unsure | 0.6175 | 0.5025 | 0.5600 | 0.12 | HELD |
| FP02 | ai | 0.7387 | 0.7070 | 0.7228 | 0.03 | BROKE |
| FP03 | ai | 0.8051 | 0.6043 | 0.7047 | 0.20 | BROKE |
| FP04 | ai | 0.8329 | 0.5333 | 0.6831 | 0.30 | BROKE |
| FP05 | ai | 0.8005 | 0.8060 | 0.8033 | 0.01 | BROKE |
| FP06 | unsure | 0.8814 | 0.3996 | 0.6400 | 0.48 | HELD |
| FP07 | unsure | 0.8816 | 0.4768 | 0.6400 | 0.40 | HELD |
| FP08 | unsure | 0.3223 | 0.4670 | 0.3947 | 0.14 | HELD |
| PI01 | human | 0.4625 | 0.2373 | 0.3499 | 0.23 | BROKE |
| PI02 | human | 0.2044 | 0.2074 | 0.2059 | 0.00 | BROKE |
| PI03 | unsure | 0.5943 | 0.4994 | 0.5469 | 0.09 | HELD |
| PI04 | unsure | 0.4998 | 0.4059 | 0.4528 | 0.09 | HELD |
| PI05 | unsure | 0.6401 | 0.2286 | 0.4344 | 0.41 | HELD |
| PI06 | unsure | 0.5583 | 0.4786 | 0.5185 | 0.08 | HELD |
| MF03 | unsure | 0.0000 | 0.4000 | 0.3600 | 0.40 | HELD |
| MF04 | unsure | 0.0112 | 0.5000 | 0.3600 | 0.49 | HELD |
| MF05 | unsure | 0.9976 | 0.5000 | 0.6400 | 0.50 | HELD |
| MF06 | unsure | 0.9969 | 0.5000 | 0.6400 | 0.50 | HELD |
| MF07 | unsure | 0.9064 | 0.5000 | 0.6400 | 0.41 | HELD |
| FL01_01 to FL01_05 | unsure | 0.0287 | 0.4000 | 0.3600 | 0.37 | HELD |

**The combining rule changed twelve of these labels.** When the signals differ by more than 0.35, the result is clamped into 0.36 to 0.64. That moved FP07 (average 0.6792), MF05 (0.7488), MF06 (0.7485) and MF07 (0.7032) from `ai` to `unsure`, and EV07 (0.2931), MF03 (0.2000), MF04 (0.2556) and all five flood decisions (0.2144) from `human` to `unsure`. Those rows are HELD, or BROKE only by a smaller margin, because of that rule and not because the signals agreed. The five junk inputs in the malformed family (MF03 to MF07) are HELD on the crash test, but each was scored instead of refused, and MF05 to MF07 would have been labelled `ai` without the clamp. That includes MF07, a real Arabic sentence that scored 0.9064 on signal one.

### Every attack that was refused (22)

| ID | HTTP | Logged reason |
|---|---|---|
| MF01 | 400 | `invalid_text` |
| MF02 | 422 | `text_too_short` |
| MF08 | 413 | `text_too_long` |
| RQ01, RQ03, RQ04, RQ05, RQ12 | 400 | `invalid_text` |
| RQ02, RQ06 | 400 | `invalid_creator_id` |
| RQ07, RQ08, RQ09 | 400 | `invalid_body` |
| RQ10, RQ11 | 429 | `rate_limited` |
| FL01_06 to FL01_12 | 429 | `rate_limited` |

All of these are HELD. Every refusal left a log entry.

**Rows that never reached my route.** RQ10 and RQ11 have no usable `creator_id`, so they count against my own address (`127.0.0.1`). RQ02, RQ06, RQ07, RQ08 and RQ09 used up the limit of 5 a minute first, and RQ10 and RQ11 came back 429 before the route ran. They are logged as `rate_limited` with `creator_id: "unknown"`, not as `invalid_body`. I marked them HELD because the request was refused, but this says nothing about my validation. I re-ran both after a minute: *<TO FILL: result of `--id RQ10` and `--id RQ11`, expected 400 `invalid_body`>*.

**Flood family.** FL01_01 to FL01_05 got through and FL01_06 to FL01_12 got 429: five passed, seven were limited, matching the limit of 5 a minute.

**Prompt injection.** PI01 and PI02 were labelled `human`, so they BROKE, but no instruction was obeyed. Nothing in my pipeline reads the text as instructions. PI01 scored 0.3499, one ten-thousandth under the 0.35 human threshold. PI04 (HTML) and PI05 (log injection) were held by design: the service never echoes the submitted text, and the log writes it through `json.dumps`, so the PI05 entry is one clean decision.

### Eleven log entries

Chosen to show the interesting outcomes, not the easiest ones: an evasion by typos, a near miss, a heavy human edit, a padding attack the clamp rescued, two false accusations, a clamp that saved a real writer, a razor-thin injection result, a non-Latin text, and two rejections.

**1. EV01 (unsure, combined 0.4092): AI text with typos. Signal one read it as human-like, signal two did not**

```json
{
  "combined_score": 0.4092,
  "content_id": "cdac2010-0558-424b-b477-32738931a9cf",
  "creator_id": "attacker_EV01",
  "guess": "unsure",
  "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
  "model_score": 0.1635,
  "status": "decided",
  "style_score": 0.6549,
  "timestamp": "2026-11-05T18:24:59+00:00"
}
```

**2. EV04 (unsure, combined 0.6239): rare words, a near miss 0.026 under the `ai` threshold**

```json
{
  "combined_score": 0.6239,
  "content_id": "3d768848-fa83-48d8-a78a-9aae5efe15ba",
  "creator_id": "attacker_EV04",
  "guess": "unsure",
  "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
  "model_score": 0.6114,
  "status": "decided",
  "style_score": 0.6364,
  "timestamp": "2026-11-05T18:24:59+00:00"
}
```

**3. EV05 (human, combined 0.2185): heavy human editing pass, the only evasion labelled human**

```json
{
  "combined_score": 0.2185,
  "content_id": "fc5cb153-d751-45b9-9a94-4e53d1937d32",
  "creator_id": "attacker_EV05",
  "guess": "human",
  "label": "We think this was probably written by a person. This is an estimate, not a certainty.",
  "model_score": 0.3212,
  "status": "decided",
  "style_score": 0.1158,
  "timestamp": "2026-11-05T18:24:59+00:00"
}
```

**4. EV07 (unsure, combined 0.36): punctuation padding. The average was 0.2931 and the clamp lifted it out of `human`**

```json
{
  "combined_score": 0.36,
  "content_id": "90fc5a11-6767-4da8-8854-eb2256794fd6",
  "creator_id": "attacker_EV07",
  "guess": "unsure",
  "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
  "model_score": 0.5862,
  "status": "decided",
  "style_score": 0.0,
  "timestamp": "2026-11-05T18:24:59+00:00"
}
```

**5. FP03 (ai, combined 0.7047): a non-native English speaker writing carefully, accused**

```json
{
  "combined_score": 0.7047,
  "content_id": "b9ce64b4-9c74-43ab-baa7-a2fafe3c96e7",
  "creator_id": "attacker_FP03",
  "guess": "ai",
  "label": "Our tools think this may have been written with AI, but they can get this wrong, especially with short or plainly written text. If you wrote it yourself, you can appeal and we will mark it for review.",
  "model_score": 0.8051,
  "status": "decided",
  "style_score": 0.6043,
  "timestamp": "2026-11-05T18:25:00+00:00"
}
```

**6. FP05 (ai, combined 0.8033): technical documentation written by a person, the highest score in the set**

```json
{
  "combined_score": 0.8033,
  "content_id": "955383a1-1092-4166-876a-fb8271e6cae9",
  "creator_id": "attacker_FP05",
  "guess": "ai",
  "label": "Our tools think this may have been written with AI, but they can get this wrong, especially with short or plainly written text. If you wrote it yourself, you can appeal and we will mark it for review.",
  "model_score": 0.8005,
  "status": "decided",
  "style_score": 0.806,
  "timestamp": "2026-11-05T18:25:00+00:00"
}
```

**7. FP07 (unsure, combined 0.64): real writing that repeats a phrase. The average was 0.6792, and the clamp is what kept it from `ai`**

```json
{
  "combined_score": 0.64,
  "content_id": "d31804aa-36a4-48f2-9f5c-25d7cdca355c",
  "creator_id": "attacker_FP07",
  "guess": "unsure",
  "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
  "model_score": 0.8816,
  "status": "decided",
  "style_score": 0.4768,
  "timestamp": "2026-11-05T18:25:00+00:00"
}
```

**8. PI01 (human, combined 0.3499): injected instruction prefix. It cleared the 0.35 human threshold by 0.0001**

```json
{
  "combined_score": 0.3499,
  "content_id": "d0d69e18-0f65-4721-8d7a-80b913e2bf38",
  "creator_id": "attacker_PI01",
  "guess": "human",
  "label": "We think this was probably written by a person. This is an estimate, not a certainty.",
  "model_score": 0.4625,
  "status": "decided",
  "style_score": 0.2373,
  "timestamp": "2026-11-05T18:25:00+00:00"
}
```

**9. MF07 (unsure, combined 0.64): Arabic text. Signal one scored it 0.9064, and only the clamp stopped an `ai` label**

```json
{
  "combined_score": 0.64,
  "content_id": "1c8f9a6d-5419-4c63-b3e0-72f75469e1a6",
  "creator_id": "attacker_MF07",
  "guess": "unsure",
  "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
  "model_score": 0.9064,
  "status": "decided",
  "style_score": 0.5,
  "timestamp": "2026-11-05T18:25:01+00:00"
}
```

**10. Rejection, FL01 flood from one creator: rate limited**

```json
{
  "creator_id": "attacker_flood",
  "event": "rejected",
  "limit": "5 per 1 minute",
  "reason": "rate_limited",
  "status": "rejected",
  "timestamp": "2026-11-05T18:25:01+00:00"
}
```

**11. Rejection, RQ10: rate limited before the route ran. The creator is `unknown` because the body had no usable `creator_id`**

```json
{
  "creator_id": "unknown",
  "event": "rejected",
  "limit": "5 per 1 minute",
  "reason": "rate_limited",
  "status": "rejected",
  "timestamp": "2026-11-05T18:25:01+00:00"
}
```

A rejection has no `content_id`, scores or label, because no decision was made.

### Worst outcome

I chose FP05, a piece of technical documentation written by a person. I chose it because it received the most confident accusation in the whole set.

**Input** (FP05, `false_positive`, "technical documentation written by a person"):

> To configure the service, edit the settings file in the installation directory. Set the host value to the address the service should bind to. Set the port value to an unused port above 1024. Save the file and restart the service. The service will log its startup configuration. If the port is already in use, the service will exit with an error. Check the log file for details before retrying.

**What my system returned** (HTTP 200):

```json
{
  "content_id": "955383a1-1092-4166-876a-fb8271e6cae9",
  "guess": "ai",
  "confidence": 0.8033,
  "label": "Our tools think this may have been written with AI, but they can get this wrong, especially with short or plainly written text. If you wrote it yourself, you can appeal and we will mark it for review.",
  "model_score": 0.8005,
  "status": "decided",
  "style_score": 0.806
}
```

**Why it's the worst:** A person who wrote their own setup instructions would be told their work "may have been written with AI". Both signals gave it about 0.80, so the system had no uncertainty to show the writer, and no rule in my pipeline made it less confident. This is the one I would least want to explain, because it is exactly the writer my `design.md` said the system must not accuse.

**Which stage let it through:** Both signals, and then nothing downstream. The combining rule cannot help here because the signals agree: the gap is 0.01, far under the 0.35 that triggers the clamp. The full diagnosis is under **Diagnosis** below.

---

## Run Log and Verdicts

**How I ran it:** `scenarios.py` holds what each of my five criteria needs. `run_eval.py` runs criteria 1, 2 and 3, plus five of the six bad inputs for criterion 5, three times each. `run_checks.py` runs criteria 4 and 5 in full, three times, because `run_eval.py` only sends `/submit`: the appeal path needs a submission and then an appeal against its real `content_id`, and bad input needs a JSON list body and a log check, which `run_eval.py` can't express. I wrote `run_checks.py` as an extension for that reason. Both ran against the same service as the attack run:

```
python run_eval.py --label before      ->  results/run_2026-11-05_1407_before.md
python run_checks.py --label before    ->  results/checks_2026-11-05_1412_before.md
```

**What each scenario contained.** Criterion 1: ten pieces I wrote myself. Criterion 2: five AI texts and my pieces 1, 3, 5, 7, 9. Criterion 3: four AI texts, my pieces 2, 4, 6, 8, and four borderline texts. I fixed those splits before running so I couldn't choose pieces after seeing scores. The AI texts and the borderline texts were written by an AI assistant for this test; the borderline ones are AI-style drafts with human-style edits.

**A limit of my own test set, stated up front:** my ten pieces are single short sentences (about 6 to 12 words each), not the paragraphs I described when I planned the test. A one-sentence text has no sentence-length spread, so `style_score` came out at exactly `0.4` for all ten. In criterion 1 only the model signal varied. It means this run says little about how paragraph-length human writing is labelled.

### Run log

Every trial gave the same numbers. This is the **before** run.

| Criterion | Target | Run 1 | Run 2 | Run 3 | Verdict |
|---|---|---|---|---|---|
| 1. False positives | At most 1 of 10 of my pieces labelled `ai`, in each trial | 0 `ai` (2 `human`, 8 `unsure`) | 0 `ai` (2 `human`, 8 `unsure`) | 0 `ai` (2 `human`, 8 `unsure`) | **MET** (with a caveat, below) |
| 2. Score spread | AI mean at least 0.30 above human mean, and at least 4 of 5 AI texts above 0.50 | AI mean 0.6543, human mean 0.4117, gap 0.2427; 5 of 5 AI above 0.50 | same | same | **MISSED** (gap 0.2427 < 0.30) |
| 3. Label coverage | `ai`, `human` and `unsure` each appear across 12 inputs; at least 3 of 4 borderline get `unsure` | `ai` 2, `human` 0, `unsure` 10; borderline 4 of 4 `unsure` | same | same | **MISSED** (`human` never appeared) |
| 4. Appeal path | 10 of 10 appeals return 200 `under_review`, add exactly one `appeal` line, leave the original line unchanged; unknown id returns 404 and no line | PASS | PASS | PASS | **MET** |
| 5. Bad input | 6 of 6 bad requests return a 4xx JSON `error`, no 500, one rejection line each, `/ping` still answers | PASS | PASS | PASS | **MET** |

**Criterion 1 is MET, and I don't think that settles much.** All ten pieces were single sentences, which the style signal scores at a constant 0.4, and in the attack run 4 of 8 real-writing attacks (FP02 to FP05, all paragraph-length) were accused. The target stays at 1 of 10 and I did not change it. What it needs is a harder test set, not an easier target.

**I did not revise any criterion.** Criteria 2 and 3 were missed, but both were measurable, which is the only reason to revise one. Lowering 0.30 because I missed it would be the wrong move.

### Criterion 1: my own writing

Scores from trial 1 (trials 2 and 3 matched to four decimals).

| # | Piece (first words) | model | style | combined | guess |
|---|---|---|---|---|---|
| 1 | Hello my name is Robert Tenney | 0.2220 | 0.4 | 0.3110 | human |
| 2 | My favorite thing to do is play video games | 0.8159 | 0.4 | 0.6079 | unsure |
| 3 | I like collecting plushies of video game characters | 0.1100 | 0.4 | 0.2550 | human |
| 4 | Today will be a great day. | 0.8549 | 0.4 | 0.6275 | unsure |
| 5 | I will get all of my work done today. | 0.6488 | 0.4 | 0.5244 | unsure |
| 6 | Depending on my mood I will take a break after completing this Milesto... | 0.3935 | 0.4 | 0.3968 | unsure |
| 7 | I am not sure what I will do after I submit this assignment | 0.6133 | 0.4 | 0.5067 | unsure |
| 8 | When I work I tend to listen to music. | 0.6372 | 0.4 | 0.5186 | unsure |
| 9 | I usually listen to video game soundtracks and remixes of said soundtr... | 0.5224 | 0.4 | 0.4612 | unsure |
| 10 | I am not sure what I should do for the final sentence. | 0.6688 | 0.4 | 0.5344 | unsure |

Two real entries from the run (the full set is in `results/run_2026-11-05_1407_before.md`):

```json
{
  "status": 200,
  "guess": "human",
  "confidence": 0.311,
  "model_score": 0.222,
  "style_score": 0.4,
  "label": "We think this was probably written by a person. This is an estimate, not a certainty.",
  "sent": "Hello my name is Robert Tenney"
}
```

```json
{
  "status": 200,
  "guess": "unsure",
  "confidence": 0.6275,
  "model_score": 0.8549,
  "style_score": 0.4,
  "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
  "sent": "Today will be a great day."
}
```

### Criterion 2: score spread

| Group | Text (first words) | model | style | combined | guess |
|---|---|---|---|---|---|
| AI | The implementation of the proposed framework... | 0.7617 | 0.5761 | 0.6689 | ai |
| AI | Regular physical activity plays a crucial role... | 0.9040 | 0.4883 | 0.6400 | unsure |
| AI | Climate change represents one of the most pressing... | 0.8793 | 0.4758 | 0.6400 | unsure |
| AI | Effective communication is a fundamental skill... | 0.8280 | 0.5578 | 0.6929 | ai |
| AI | Artificial intelligence has the potential to transform... | 0.7422 | 0.5173 | 0.6298 | unsure |
| Human | Hello my name is Robert Tenney (piece 1) | 0.2220 | 0.4 | 0.3110 | human |
| Human | I like collecting plushies... (piece 3) | 0.1100 | 0.4 | 0.2550 | human |
| Human | I will get all of my work done today. (piece 5) | 0.6488 | 0.4 | 0.5244 | unsure |
| Human | I am not sure what I will do... (piece 7) | 0.6133 | 0.4 | 0.5067 | unsure |
| Human | I usually listen to video game soundtracks... (piece 9) | 0.5224 | 0.4 | 0.4612 | unsure |

- **AI mean:** (0.6689 + 0.6400 + 0.6400 + 0.6929 + 0.6298) / 5 = **0.6543**
- **Human mean:** (0.3110 + 0.2550 + 0.5244 + 0.5067 + 0.4612) / 5 = **0.4117**
- **Gap: 0.2427** against a target of 0.30.
- **AI texts above 0.50:** 5 of 5, against a target of at least 4.

Two real entries from trial 1 (the clamped one and the `ai` one):

```json
{
  "status": 200,
  "guess": "unsure",
  "confidence": 0.64,
  "model_score": 0.904,
  "style_score": 0.4883,
  "label": "We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal.",
  "sent": "Regular physical activity plays a crucial role in maintaining both phy"
}
```

```json
{
  "status": 200,
  "guess": "ai",
  "confidence": 0.6929,
  "model_score": 0.828,
  "style_score": 0.5578,
  "label": "Our tools think this may have been written with AI, but they can get this wrong, especially with short or plainly written text. If you wrote it yourself, you can appeal and we will mark it for review.",
  "sent": "Effective communication is a fundamental skill that contributes to suc"
}
```

### Criterion 3: label coverage

12 inputs: 4 AI, 4 mine, 4 borderline.

| Group | Text (first words) | model | style | combined | guess |
|---|---|---|---|---|---|
| AI | The implementation of the proposed framework... | 0.7617 | 0.5761 | 0.6689 | ai |
| AI | Regular physical activity plays a crucial role... | 0.9040 | 0.4883 | 0.6400 | unsure |
| AI | Climate change represents one of the most pressing... | 0.8793 | 0.4758 | 0.6400 | unsure |
| AI | Effective communication is a fundamental skill... | 0.8280 | 0.5578 | 0.6929 | ai |
| Human | My favorite thing to do is play video games (piece 2) | 0.8159 | 0.4 | 0.6079 | unsure |
| Human | Today will be a great day. (piece 4) | 0.8549 | 0.4 | 0.6275 | unsure |
| Human | Depending on my mood I will take a break... (piece 6) | 0.3935 | 0.4 | 0.3968 | unsure |
| Human | When I work I tend to listen to music. (piece 8) | 0.6372 | 0.4 | 0.5186 | unsure |
| Borderline | I've been working from home for about two years now... | 0.5398 | 0.6040 | 0.5719 | unsure |
| Borderline | Learning a second language takes time... | 0.3922 | 0.5415 | 0.4668 | unsure |
| Borderline | Public libraries remain an important community resource... | 0.6248 | 0.1990 | 0.4119 | unsure |
| Borderline | Budgeting does not have to be complicated... | 0.5503 | 0.1501 | 0.3600 | unsure |

Label counts across the 12: `ai` 2, `unsure` 10, `human` **0**. All four borderline texts got `unsure`.

### Criterion 5, part one: five bad bodies through `run_eval.py`

Every trial returned the same five statuses, in order: **400, 400, 400, 400, 413**. The `—` in the console is not a failure. A rejection has no `guess`, so `run_eval.py` has nothing to print there.

```json
{
  "status": 413,
  "guess": null,
  "confidence": null,
  "model_score": null,
  "style_score": null,
  "label": null,
  "sent": "word word word word word word word word word word word word word word "
}
```

### Criteria 4 and 5 in full, from `run_checks.py`

Trial 1 appeal path. In trials 2 and 3 the shape is identical, with new `content_id`s. All ten appeals returned 200 `under_review` and the unknown id returned 404:

```json
[
  {"appeal": 1,  "content_id": "a7c33b03-4238-4650-ac9d-d92feef606d1", "http": 200, "status": "under_review", "ok": true},
  {"appeal": 2,  "content_id": "5b18bb10-1485-4e88-ba97-7adce0b12f74", "http": 200, "status": "under_review", "ok": true},
  {"appeal": 3,  "content_id": "54d26a6e-fd96-401e-93ed-26d8e055ec72", "http": 200, "status": "under_review", "ok": true},
  {"appeal": 4,  "content_id": "f46a2d8d-f794-417a-befa-c12b2298e074", "http": 200, "status": "under_review", "ok": true},
  {"appeal": 5,  "content_id": "fc57988c-51e1-4843-9f5a-cc7cd6913c7b", "http": 200, "status": "under_review", "ok": true},
  {"appeal": 6,  "content_id": "ca8312a1-7e41-459f-8b43-2e4e00701a30", "http": 200, "status": "under_review", "ok": true},
  {"appeal": 7,  "content_id": "fdd2a9d5-e130-4ab4-b802-0589ef213d01", "http": 200, "status": "under_review", "ok": true},
  {"appeal": 8,  "content_id": "522a57c8-4f99-4324-8211-68eeb7c08257", "http": 200, "status": "under_review", "ok": true},
  {"appeal": 9,  "content_id": "3eeecbea-8d5a-4ab7-81e8-9f4fd6cca213", "http": 200, "status": "under_review", "ok": true},
  {"appeal": 10, "content_id": "18316b41-2256-441a-a86a-6758d4e3c0af", "http": 200, "status": "under_review", "ok": true},
  {"appeal": "unknown id", "content_id": "dd6a4e52-e08d-48cd-8060-7780560616d2", "http": 404, "ok": true}
]
```

Besides the status codes, `run_checks.py` also confirmed, for each trial, that the log gained exactly ten `appeal` lines carrying the writer's reasoning, that every original `decided` line was unchanged, and that the unknown id added no appeal line.

Trial 1 bad input. Trials 2 and 3 are identical:

```json
[
  {"request": "text missing",      "http": 400, "error": "invalid_text", "ok": true},
  {"request": "text empty",        "http": 400, "error": "invalid_text", "ok": true},
  {"request": "text whitespace",   "http": 400, "error": "invalid_text", "ok": true},
  {"request": "text null",         "http": 400, "error": "invalid_text", "ok": true},
  {"request": "text 50,000 words", "http": 413, "error": "text_too_long", "ok": true},
  {"request": "body is a list",    "http": 400, "error": "invalid_body", "ok": true},
  {"request": "/ping afterwards",  "http": 200, "ok": true}
]
```

The check also confirmed exactly six new rejection lines in the log per trial.

---

## Diagnosis

**How I did this, and what it can't tell me.** I used `diagnose_style.py`, which splits the style score into what each of its three measures contributed (output in `results/style_breakdown_before.md`). It uses the service's own `stylometry.py`, and for every attack and test text I checked, its score matched the `style_score` in my log exactly. So the **signal two** claims below are measured. I did **not** re-run gpt2, so every claim about **signal one** is an inference from the `model_score` the service returned, marked *(inferred)*.

Stage names: **S1** = signal one (`detector.py`), **S2** = signal two (`stylometry.py`), **CR** = combining rule (`scoring.py::combine_signals`), **TH** = thresholds (`config.py`).

*All numbers in this section use the style formula as it was before Milestone 5.*

### The two misses against my criteria

**Criterion 2 (gap 0.2427, target 0.30). Stage: S2, then CR.**

| | model (mean) | style (mean) | combined (mean) |
|---|---|---|---|
| 5 AI texts | 0.8230 | 0.5231 | 0.6543 |
| 5 human pieces | 0.4233 | 0.4000 | 0.4117 |
| gap | 0.3997 | 0.1231 | 0.2427 |

- **Most of the gap is S1.** Each signal counts half, so the model signal adds 0.1998 to the combined gap and the style signal only 0.0616. Together that is 0.2614, before the clamp.
- **Why S2 separates so little.**
  - My AI texts are about 50 words, so the type-token ratio is 0.85 to 0.93. That is above the human end of its scale (0.75), so it adds **0 points**, and 30% of the style weight does nothing on short text.
  - The same AI texts have commas (punctuation 0.094 to 0.130), which the style signal reads as human-like.
  - So AI texts only reach style scores of 0.48 to 0.58.
  - Every human piece is one sentence with no punctuation, which scores exactly 0.4. The floor sits only 0.12 below the AI texts.
- **Then CR takes another 0.0187.** Two AI texts had model scores of 0.904 and 0.879 against style scores of 0.488 and 0.476. The gaps of 0.416 and 0.403 triggered the clamp, so their plain averages (0.696 and 0.678, both `ai`) became 0.64. Without the clamp the mean would be 0.6731, not 0.6543.
- **Even with no clamp the target is missed** (0.2614 < 0.30), so the clamp is not the main cause. The short-text behaviour of S2 is.

**Criterion 3 (`human` never appeared in the 12 inputs). Stage: S2 and TH, with S1 doing the rest.**

- **The 0.4 floor.** For a one-sentence text the style score is built from only two measures. The type-token ratio of a short text is 1.0, which scores 0, and with no punctuation the punctuation measure scores 1 (0.2 of the 0.5 weight), so the style score comes out at exactly **0.4**.
- **What that does to `human`.** Combined = 0.5 × model + 0.5 × 0.4 = 0.5 × model + 0.2. The `human` label needs a combined score of 0.35 or less, so `model_score` must be **0.30 or lower**. Only my pieces 1 and 3 (0.222 and 0.110) got there. Piece 6 (model 0.3935) landed at 0.3968, 0.047 too high.
- **Where S1 comes in.** My other three human pieces scored 0.82, 0.85 and 0.64 on plain, common-word sentences *(inferred: short, predictable wording is easy for gpt2)*. That is the blind spot I wrote about in the signals section.
- **This is not "human is unreachable".** A longer personal paragraph of mine ("Grandfather's kitchen") scored 0.2377 and got `human`. The miss comes from my human test pieces being one sentence each. The criterion as written is still missed.
- **The borderline clause held:** 4 of 4 `unsure`. It held partly because the clamp and the wide band catch everything near the middle.

### The 15 attacks that broke

| ID | Stage | Mechanism and evidence |
|---|---|---|
| EV01 typos | S1 | Misspellings (`poistioned`, `thsi`, `cosnistently`) pushed the model score to 0.1635 against 0.74 to 0.90 for my AI test texts *(inferred: misspelled tokens surprise gpt2)*. S2 was not moved: spread 0.161 is under the AI end, so it scored 0.6549. The attack aimed at S2 and hit S1. The average 0.4092 was already in the unsure band, so the clamp changed nothing. |
| EV02 chopped sentences | S2 | Six one-word `Really.` sentences pushed the spread to 0.849, past the human end of 0.70, so that measure gave **0 points**. Style fell to 0.1601. Model 0.5943 was not enough to hold the average up (0.3772). |
| EV03 pasted quotation | S2 | The quoted line adds sentences and punctuation. Spread 0.443 (0.257 points) and punctuation 0.075 (0.108 points) gave style 0.3642. The gap with the model (0.6662) was 0.30, just under the 0.35 clamp trigger. Average 0.5152. |
| EV04 rare words | S1 and TH | Model 0.6114, lower than all five AI test texts *(inferred: `efficacious`, `desiderata`, `opacity` surprise gpt2)*. S2 stayed at 0.6364. The average 0.6239 missed `ai` by **0.026**. This is the nearest of the 15 to holding. |
| EV05 heavy human edit | S1 and S2 | Both signals read human: model 0.3212, style 0.1158 (spread 0.675, punctuation 0.085). I don't count this as a flaw in the pipeline. The text was rewritten by a person, and both signals measure surface features that editing changes. |
| EV06 one long and one short sentence | S2 | Spread 1.186, far past 0.70, gave 0 points. Style 0.1790. Same mechanism as EV02. |
| EV07 punctuation padding | S2, rescued by CR | `; and, moreover,` chains join everything into one sentence, so the spread measure is dropped. Punctuation 0.227 is past the human end, giving 0 points. The type-token ratio also gave 0. Style = **0.0000**. The average (0.2931) would have been `human`. The clamp (gap 0.59) lifted it to 0.36, `unsure`. |
| EV08 short text | S1 | Ten words. Model 0.2452 *(inferred: very little context, so each word is surprising)*. Style 0.6172 (spread 0.283 gave 0.417 points). The signals differ by 0.37, but the average 0.4312 was already in the band. |
| EV09 human opening bolted on | S2 and CR | The casual opening lengthens the spread to 0.468 (0.232 points) and adds punctuation 0.054 (0.143), so style fell to 0.3757. Model stayed at 0.7042. Gap **0.33**, just under the 0.35 trigger. The equal-weight average (0.5400) let the style signal cancel a model signal that still said AI. |
| FP02 formal report | S1 and S2 agree | Model 0.7387. Spread 0.240 is near the AI end (0.460 points), punctuation 0.013 is under the AI end (0.200, the maximum). Formal prose is uniform and has few commas. Gap 0.03, so the clamp was inert. |
| FP03 careful non-native writer | S1 and S2 agree | Model 0.8051 *(inferred: simple common words)*. Style 0.6043: spread 0.328 (0.372 points), punctuation **0.000** (0.200, the maximum). This is the case `design.md` predicted (model about 0.80, style about 0.70, combined about 0.75). Actual: 0.8051, 0.6043, 0.7047. |
| FP04 student essay | S1 and S2 agree | Model 0.8329. Style 0.5333 (spread 0.367 gave 0.333 points, punctuation 0.200). Gap 0.30, **0.05 under** the 0.35 clamp trigger. Average 0.6831. |
| FP05 technical documentation | S1 and S2 agree | See "Worst outcome" below. |
| PI01 "ignore all previous instructions" | S1 and S2, not obedience | No instruction was followed. The prefix is short sentences with no punctuation: spread 0.663 (0.037 points), punctuation 0.000 (0.200). Model 0.4625 *(inferred: odd text raises perplexity)*. Average 0.3499, **0.0001** under the `human` line. |
| PI02 fake `SYSTEM:` message | S1 and S2, not obedience | Model 0.2044. Spread 0.652 (0.048 points), type-token ratio 0.956 (0), punctuation 0.044 (0.159). Both signals read human, average 0.2059. Same effect as PI01: the prefix changes the shape of the text. |

### Worst outcome: why FP05 was accused

**Diagnosis (stage: both signals; nothing downstream reduces it).**

- **S2 is measured.** Style 0.806 breaks down as:
  - spread 0.277: 0.423 points
  - type-token ratio 0.586: **0.197** points
  - punctuation 0.029: 0.186 points
  - Setup instructions have short imperative sentences of similar length. They repeat their own terms (`the`, `service`, `set`, `port`), which drags the type-token ratio down. And they have almost no commas. All three measures read that as AI-like. FP05 is the only attack where the type-token ratio added real points; the other attacks got about 0 from it.
- **S1 is inferred.** Model 0.8005 *(inferred: plain imperative sentences made of common words are easy for gpt2 to predict)*.
- **CR is inert.** The signals differ by 0.01, so the clamp never applies. An average of 0.8033 clears the 0.65 line by 0.15, so the threshold isn't close either.
- **Why this matters.** `design.md` proposed requiring both signals to agree before an `ai` label. Here both agree, so that rule would have changed nothing.

### Patterns across the misses

1. **Short and plain text breaks both signals the same way.** Signal one scores plain common-word writing 0.74 to 0.88 (FP02 to FP05, my own pieces 2 and 4). Signal two is flat or blind on short text: a one-sentence text is stuck at 0.4, and the type-token ratio is dead below about 100 words. That one blind spot explains the criterion 2 and criterion 3 misses and the four false accusations (FP02 to FP05).
2. **A plain average lets one fooled signal cancel the other.** In all 11 evasion and injection breaks, the combined score stayed below 0.65 because a cheap edit pulled at least one signal down: S2 in EV02, EV03, EV06, EV07 and EV09; S1 in EV01, EV04 and EV08; both in EV05, PI01 and PI02. Fooling one signal is enough.
3. **The style measures cap at the human end, so small edits are enough.** Spread past 0.70 (EV02 0.849, EV06 1.186) or punctuation past 0.14 (EV07 0.227) each zero out their measure. Adding more of the same buys nothing, but one short repeated sentence is already enough.
4. **The clamp cuts both ways.** It kept FP07 and MF05 to MF07 from `ai`, and it kept EV07 from `human`. It also moved two of my five AI texts from `ai` to `unsure` and cost criterion 2 about 0.019. It never changed the outcome of any of the 15 broken attacks to `ai`.
5. **Agreement is not accuracy.** In FP02 to FP05 both signals agree, so a "require both" rule would not help. Both measure how uniform and predictable the text is, and plain formal, technical or second-language writing is uniform and predictable. The two signals are not independent evidence about exactly the writers I most want to protect.

**What I did not change in Milestone 4.** No code, no thresholds, no criteria.

---

## The Improvement

### What I changed (one change)

In `stylometry.py`, **punctuation density no longer counts toward the style score** (`W_PUNCT` 0.2 → 0.0). Two things follow from that one edit, and I list them so nobody mistakes them for extra changes:

- The two measures left (spread 0.5, type-token ratio 0.3) are scaled to fill the score, so they now count 0.625 and 0.375.
- A one-sentence text used to get its 0.4 from the type-token ratio and the punctuation measure together. With punctuation gone it would have fallen to 0.0, so I pinned it at `ONE_SENTENCE_STYLE = 0.4`, its old value, so that nothing else moves.

No thresholds, weights between the two signals, or the combining rule were touched.

### Why this one

My diagnosis (pattern 1) put the blame on short and plain writing, and the style signal's part of that is the punctuation measure. It scores **less** punctuation as AI-like, but in my own data plain human writing has the least punctuation:

| Texts | Punctuation per word |
|---|---|
| Plain human writing, FP01 to FP08 | 0.000 to 0.034 |
| The five AI texts from my criteria set | 0.094 to 0.130 |
| AI paragraph with typos or rare words (EV01, EV04) | 0.047, 0.058 |
| Human-edited text (EV05) | 0.085 |

It gave the full 0.2 points of AI-ness to 6 of the 8 false-positive attacks (FP01 to FP04, FP06, FP08), including FP03, a careful non-native writer with 0.000 punctuation. It is the measure `stylometry.py` already called "ambiguous" in a comment, and the plainest case in the diagnosis of a measure pointing the wrong way for the people the system should protect.

### Other changes I replayed and why I did not pick them

I replayed these against my logged model scores (signal one never changes in a replay). They were done in a scratch script I did not commit, so treat the table as my working, not as a repo artifact. All use the same 24 scored attacks (EV, FP, PI) and my criteria texts as before.

| Change replayed | EV broke | FP broke | PI broke | Criterion 2 gap | AI texts labelled `ai` (of 5) | Why not |
|---|---|---|---|---|---|---|
| Nothing (before) | 9 | 4 | 2 | 0.2427 | 2 | |
| **Stop scoring punctuation (chosen)** | 9 | 3 | 1 | 0.2635 | 3 | |
| Raise `AI_THRESHOLD` to 0.72 | 9 | 2 | 2 | 0.2614 | 0 | `ai` becomes unreachable on all my criteria texts, which breaks criterion 3 for good |
| Remove the clamp | 9 | 5 | 2 | 0.2614 | 4 | accuses one more writer |
| Require both signals at least 0.5 for `ai` | 9 | 4 | 2 | 0.2427 | 2 | changes nothing: FP02 to FP05 have both signals above 0.5 |
| Drop the type-token ratio on short multi-sentence texts | 8 | 5 | 1 | 0.3735 | 5 | would meet criterion 2, but accuses one more writer, against the priority I set in `design.md` |
| Weights 0.6 model / 0.4 style | 9 | 4 | 1 | 0.2539 | 3 | tiny gain, and it leans harder on the signal that over-scores plain text |

The last-but-one row is the honest tension in this project. A change that catches more AI text also accuses more real writers, because the two signals overlap on exactly that kind of text.

### One replay I rejected as an artifact

My first replay of the chosen idea showed a criterion 2 gap of **0.426**. I threw that number out. Removing punctuation without pinning one-sentence text made every single-sentence piece of mine score a style of 0.0, which lowered the human mean through the clamp for no real reason. The 0.2635 above is the version I trust.

### Prediction, written before the after-run

From `python replay_changes.py` (saved in `results/predicted_after.md`). This is a replay of the logged model scores through the new style formula. It is **not** a result.

| What | Before (real) | Predicted after |
|---|---|---|
| Scored attacks that BROKE | 15 | 13 |
| Evasion BROKE (of 9 scored) | 9 | 9 |
| False positive BROKE | 4 | 3 (FP04 `ai` → `unsure`) |
| Prompt injection BROKE | 2 | 1 (PI01 `human` → `unsure`) |
| FP08 (real, three short sentences) | `unsure` | `human` |
| FP02, FP03, FP05 | `ai` | still `ai` (combined 0.6863, 0.6552, 0.7879) |
| Criterion 1: own pieces labelled `ai` | 0 | 0 |
| Criterion 2: gap | 0.2427 | 0.2635 (still below 0.30) |
| Criterion 2: AI texts above 0.50 | 5 | 5 |
| Criterion 3: labels `ai` / `human` / `unsure` over 12 | 2 / 0 / 10 | 2 / 0 / 10 |
| AI texts labelled `ai` (of 5) | 2 | 3 |

**What I expect, in plain words.** It fixes one false accusation (FP04), moves the injection from `human` to `unsure`, and catches one more AI text, with no new false accusation in my own writing. It does **not** fix criterion 2 or 3, it does not help evasion at all, and it does not touch the worst outcome: FP05 stays accused at about 0.79. FP03 stays accused by only 0.0052, so I would not call it fixed in any meaningful sense.

### Limits I know about

- **I chose this on the same texts I'm testing it on.** The after-run cannot independently confirm that the change is a good one, only that it behaves as replayed. A fresh set of human and AI texts would.
- **The AI texts in my criteria set were written by an AI assistant**, and they contain commas. Part of the criterion 2 improvement comes from them. The attack set evidence (six real-writing attacks getting the maximum punctuation points) is the stronger reason.
- **PI01's improvement is luck.** It moves to `unsure` because the signals disagree by 0.42 and the clamp lifts the score to 0.36. The injection still works on the signals; it just no longer lands on `human`.
- **One-sentence text is still weak.** Its style is pinned at a number that says nothing true. I list it under What's Still Broken.

### Before and after, side by side

Fill the After column from the real after-run. If it differs from the prediction, say so and say why.

**Attacks** (`python run_attacks.py --set attack_set --label after`, then `python fill_verdicts.py` on its report):

| Family | Before BROKE | Predicted after | After (real) |
|---|---|---|---|
| evasion | 9 | 9 | *TO FILL* |
| false_positive | 4 | 3 | *TO FILL* |
| prompt_injection | 2 | 1 | *TO FILL* |
| malformed and flood | 0 | 0 | *TO FILL* |
| **Total** | **15** | **13** | *TO FILL* |

The rows that were predicted to change:

| ID | Before: guess, combined | Predicted after | After (real) |
|---|---|---|---|
| FP04 | `ai`, 0.6831 | `unsure`, 0.6248 | *TO FILL* |
| FP08 | `unsure`, 0.3947 | `human`, 0.3280 | *TO FILL* |
| PI01 | `human`, 0.3499 | `unsure`, 0.3600 | *TO FILL* |
| FP02 | `ai`, 0.7228 | `ai`, 0.6863 | *TO FILL* |
| FP03 | `ai`, 0.7047 | `ai`, 0.6552 | *TO FILL* |
| FP05 | `ai`, 0.8033 | `ai`, 0.7879 | *TO FILL* |

**Criteria** (`python run_eval.py --label after`, `python run_checks.py --label after`):

| Criterion | Before | Predicted after | After (real) | Verdict after |
|---|---|---|---|---|
| 1. own pieces labelled `ai` | 0 of 10, MET | 0 of 10 | *TO FILL* | *TO FILL* |
| 2. gap, AI mean minus human mean | 0.2427, MISSED | 0.2635 | *TO FILL* | *TO FILL* |
| 3. labels `ai` / `human` / `unsure` | 2 / 0 / 10, MISSED | 2 / 0 / 10 | *TO FILL* | *TO FILL* |
| 4. appeal path | PASS ×3, MET | no change | *TO FILL* | *TO FILL* |
| 5. bad input | PASS ×3, MET | no change | *TO FILL* | *TO FILL* |

**Worst outcome, FP05, after:** *TO FILL: guess, model, style, combined.* Predicted: still `ai`, combined 0.7879.

**Did the change cost anything?** *TO FILL: any attack or criterion that got worse, and by how much.*

---

## What's Still Broken

*Unit 8.*

---

📖 **How to run this project: [RUNNING.md](RUNNING.md)**