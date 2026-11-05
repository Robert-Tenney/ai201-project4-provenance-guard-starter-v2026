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
3. **Signal two** (`stylometry.py::style_signal`) adds a 0-1 score for the shape of the text: sentence-length spread, vocabulary repetition, and punctuation.
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

**Why that might differ between human and AI writing:** Generated prose tends toward even, mid-length sentences and a narrower band of vocabulary. Human writing is lumpier, with a three-word sentence next to a forty-word one.

**What it can't see:** It cannot read. Anything that changes the shape without changing the substance moves it: typos, chopped-up sentences, a pasted quotation. That is very cheap to do on purpose. Short, plain human writing also looks uniform to it, which is the same writer signal one is likely to accuse.

**What I saw** (`python stylometry.py`, raw measure values):

| Text | spread | type-token | punctuation | style_signal |
|---|---|---|---|---|
| AI paragraph (85 words, 5 sentences) | 0.239 | 0.753 | 0.047 | 0.6160 |
| "...did I just witness" (12 words, 1 sentence) | 0.000 | 1.000 | 0.000 | 0.4000 |
| "...was I forced to hear?" (13 words, 1 sentence) | 0.000 | 1.000 | 0.077 | 0.2103 |

- Direction is right: the AI paragraph scored higher than both of my sentences.
- **Type-token ratio did almost nothing.** The AI paragraph's 0.753 sits at the human end of my scale (0.75), so it added no AI-ness, and very short texts score 1.000 because there is little room to repeat a word.
- **One-sentence texts have no spread measure**, so the difference between 0.40 and 0.21 for two near-identical sentences came entirely from one question mark. That is fragile, and it is the short-text blind spot in practice.

*Scale endpoints and weights: unchanged from the starting guesses I set before measuring.*

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

**Where the rule failed:** "What in the name of 'Sweet Home Alabama' was I forced to hear? I honestly do not know what that was." scored model 0.7905 and style 0.5173, combined 0.6539, and got the `ai` label. The signals differed by only 0.27, so the clamp never applied. (The second sentence was written by an AI assistant for testing, so this is a near-miss rather than a clean sample.) The rule only protects writers when the signals disagree; it does nothing when both lean AI-like on short, plain text. I have not moved the thresholds. A candidate fix for unit 8 is requiring both signals to be at least 0.5 before the `ai` label can appear.

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

*Draft: edit so every line is true to what you did.*

**Moment 1: acceptance criteria and design notes**

- *What I asked for:* Help with the Milestone 1 trace and the Milestone 2 acceptance criteria.
- *What came back:* A full `design.md` and five complete criteria with numbers and reasons.
- *What I changed:* The brief says criteria should be my own, so I reviewed each one, asking whether someone who couldn't ask me could check it. *(Edit this to say what you actually changed, such as a number you adjusted or a reason you rewrote. If you changed nothing, say so plainly.)*

**Moment 2: the style signal and the false positive it let through**

- *What I asked for:* An implementation of `stylometry.py` and the combining rule, with the disagreement clamp.
- *What came back:* Three measures with starting-guess scale endpoints, and a rule that clamps to "unsure" when the signals split.
- *What I changed:* Nothing in the code, but running it showed problems the starting guesses hid. Type-token ratio did almost nothing (the AI paragraph scored 0.753, at the human end of my scale), and a human sentence plus one extra sentence scored 0.6539 and was labelled `ai`, because the clamp only fires when the signals disagree. I recorded both as findings instead of moving the thresholds, and noted a "both signals at least 0.5" rule as a fix to try in unit 8.

**Moment 3 (unit 8, Milestone 1): rate limiting and the README write-up**

- *What I asked for:* The completed `config.py` and `app.py` for Milestone 1, and help writing up the Rate Limiting section from my real output.
- *What came back:* Limiter settings of 5 per minute and 50 per day with reasoning, the activated `@limiter.limit(...)` decorator, a small change to the 429 handler so it logs `"unknown"` instead of a non-string `creator_id`, and a draft of the README section.
- *What I checked:* I ran the flood test myself and used its real output. When my first log read-back showed only the `flood_tester` rejections and no decisions, I re-ran the three-text check until `m1_check` decisions appeared, rather than pasting entries that didn't show what the section claimed. *(Edit this to say what you actually changed or decided about the limits yourself.)*

**Moment 4 (unit 8, Milestone 2): running the attack set**

- *What I asked for:* Help preparing the attack run, and two small helper scripts, `fill_verdicts.py` and `m2_extract.py`, to fill the Verdict column and pull scores out of my own `audit_extract.json`.
- *What came back:* Predictions before I ran it, built from my code and a local run of `stylometry.py`. Most were right: the route's 400/413/422 responses, five of twelve flood rows passing, and RQ10 and RQ11 hitting 429 before my route ran. Two were wrong. I expected EV01 (typos) and EV04 (rare words) to be caught, and both came back `unsure`. EV01's typos moved signal one, not signal two as I assumed.
- *What I decided myself:* *(Edit this. For example: which verdict rule I used for "unsure", why MF03 to MF07 count as held, why I chose FP05 as the worst outcome. The cause of the worst outcome is mine to work out in Milestone 4.)*

**Moment 5 (unit 8, Milestone 3): running my own five criteria**

- *What I asked for:* A `scenarios.py` for my five criteria and a way to test the two that `run_eval.py` can't (the appeal path and bad input).
- *What came back:* A filled `scenarios.py` with five AI texts and four borderline texts written by the assistant, and a new `run_checks.py` that submits, appeals, reads the log back and checks the bad-input requests. The ten pieces of my own writing were left for me, because criterion 1 is only meaningful with writing I did myself.
- *What went wrong:* The first copy of `run_checks.py` I saved was incomplete, and Python stopped with a `SyntaxError` at line 296. I replaced it with the full file and it ran. The assistant also told me all three labels had appeared in my criterion 3 set, which was wrong: `human` never appeared in those 12 inputs. I caught it by reading my own run output.
- *What I decided myself:* *(Edit this. For example: which ten pieces I used and why pieces 1 to 3 are the plain ones, and whether I agree the borderline texts are borderline.)*

**Also worth saying, if true:** `/appeal` kept returning `501 not_implemented` because my `app.py` still had the starter stub. I only found that by reading the response text, then replaced the function and restarted.

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

**Which stage let it through:** not diagnosed yet. That is Milestone 4. In my breakout I'm sharing only the input and the output.

---

## Run Log and Verdicts

**How I ran it:** `scenarios.py` holds what each of my five criteria needs. `run_eval.py` runs three of them (criteria 1, 2, 3 and the five dict-shaped bad inputs of 5) three times. `run_checks.py` runs criteria 4 and 5 in full, three times, because `run_eval.py` only sends `/submit`: the appeal path needs a submission and then an appeal against its real `content_id`, and bad input needs a JSON list body and a log check, which `run_eval.py` can't express. I wrote `run_checks.py` as an extension for that reason. Everything was run against the same service as the attack run, with `python run_eval.py --label before` and `python run_checks.py --label before`.

Reports: `results/run_2026-11-05_1407_before.md` and `results/checks_2026-11-05_1412_before.md`.

**What each scenario contained.** Criterion 1: ten pieces I wrote myself (pieces 1 to 3 plain, short-sentence writing). Criterion 2: five AI texts and my pieces 1, 3, 5, 7, 9. Criterion 3: four AI texts, my pieces 2, 4, 6, 8, and four borderline texts. I fixed those splits before running so I couldn't choose pieces after seeing scores. The AI texts and the borderline texts were written by an AI assistant for this test; the borderline ones are AI-style drafts with human-style edits.

### Run log

Verdicts are left blank. I decide MET or MISSED in Milestone 4. The Run cells below hold what came back; the label counts are real output. Cells marked TO FILL need the scores from the report.

| Criterion | Target | Run 1 | Run 2 | Run 3 | Verdict |
|---|---|---|---|---|---|
| 1. False positives | At most 1 of 10 of my pieces labelled `ai`, in each trial | 0 `ai`, 2 `human`, 8 `unsure` | 0 `ai`, 2 `human`, 8 `unsure` | 0 `ai`, 2 `human`, 8 `unsure` | |
| 2. Score spread | AI mean at least 0.30 above human mean, and at least 4 of 5 AI texts above 0.50 | AI: 2 `ai`, 3 `unsure`; human: 2 `human`, 3 `unsure`. Means and scores: *TO FILL* | same labels. *TO FILL* | same labels. *TO FILL* | |
| 3. Label coverage | `ai`, `human` and `unsure` each appear in the 12 inputs; at least 3 of 4 borderline get `unsure` | AI: 2 `ai`, 2 `unsure`; human: 4 `unsure`; borderline: 4 `unsure`. `human` does not appear | same | same | |
| 4. Appeal path | 10 of 10 appeals return 200 `under_review`, add exactly one `appeal` line, leave the original line unchanged; unknown id returns 404 and no line | PASS | PASS | PASS | |
| 5. Bad input | 6 of 6 bad requests return a 4xx JSON `error`, no 500, one rejection line each, `/ping` still answers | PASS | PASS | PASS | |

### What actually came back

Real output, copied from the console.

```
my own writing  (criterion 1)
  trial 1: human, unsure, human, unsure, unsure, unsure, unsure, unsure, unsure, unsure
  trial 2: human, unsure, human, unsure, unsure, unsure, unsure, unsure, unsure, unsure
  trial 3: human, unsure, human, unsure, unsure, unsure, unsure, unsure, unsure, unsure

spread ai  (criterion 2)
  trial 1: ai, unsure, unsure, ai, unsure
  trial 2: ai, unsure, unsure, ai, unsure
  trial 3: ai, unsure, unsure, ai, unsure

spread human  (criterion 2)
  trial 1: human, human, unsure, unsure, unsure
  trial 2: human, human, unsure, unsure, unsure
  trial 3: human, human, unsure, unsure, unsure

coverage ai  (criterion 3)
  trial 1: ai, unsure, unsure, ai
  trial 2: ai, unsure, unsure, ai
  trial 3: ai, unsure, unsure, ai

coverage human  (criterion 3)
  trial 1: unsure, unsure, unsure, unsure
  trial 2: unsure, unsure, unsure, unsure
  trial 3: unsure, unsure, unsure, unsure

coverage border  (criterion 3)
  trial 1: unsure, unsure, unsure, unsure
  trial 2: unsure, unsure, unsure, unsure
  trial 3: unsure, unsure, unsure, unsure

bad input  (criterion 5)
  trial 1: —, —, —, —, —
  trial 2: —, —, —, —, —
  trial 3: —, —, —, —, —
```

```
trial 1
  criterion 4 (appeal path): PASS
  criterion 5 (bad input): PASS

trial 2
  criterion 4 (appeal path): PASS
  criterion 5 (bad input): PASS

trial 3
  criterion 4 (appeal path): PASS
  criterion 5 (bad input): PASS
```

The `—` marks in the bad input rows are not failures. A rejection has no `guess`, so `run_eval.py` has nothing to print. The status codes are in the report.

**What I notice, not yet a verdict:**

- **Identical every time.** All three trials gave the same labels for every scenario, so the model signal did not wobble enough to change a label in this run.
- **My own writing.** None of my ten pieces was labelled `ai`, but only two of ten reached `human`. The other eight landed in `unsure`.
- **My human pieces in the coverage set.** All four came back `unsure`, so `human` is not reached by anything in the 12 inputs of criterion 3. The two `human` labels in this run came from pieces 1 and 3, which sit in the criterion 1 and 2 sets, not this one.
- **My AI texts.** Only two of the five got `ai`. Three got `unsure`, so criterion 2's second clause (at least 4 of 5 above 0.50) depends on their combined scores, which I need from the report.
- **Borderline.** All four got `unsure`.
- **Scores:** *TO FILL from `results/run_2026-11-05_1407_before.md`.*

**Criteria 4 and 5** each passed in all three trials. The detail (the ten appeal responses, the six bad-input status codes and error names, the `/ping` check) is in `results/checks_2026-11-05_1412_before.md`: *TO FILL, paste the trial 1 JSON blocks from that file.*

## The Improvement

*Unit 8.*

## What's Still Broken

*Unit 8.*

---

📖 **How to run this project: [RUNNING.md](RUNNING.md)**