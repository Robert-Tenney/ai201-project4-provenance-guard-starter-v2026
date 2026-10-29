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

1. **Arrives at `POST /submit`** (`app.py::submit`). It validates the request: a JSON object, non-empty `text` under 20,000 characters, non-empty `creator_id`. Bad input gets a clear 4xx JSON error and a logged rejection, never a crash.
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

**Against my criteria:** Criterion 2 asks for AI text to score at least 0.30 above human text. Against my two short human sentences the gap is about 0.24 to 0.25; against the longer human paragraph it is about 0.5. I have only a handful of inputs, not the 5 and 5 the criterion specifies, so this is not a verdict, and I am not changing the target.

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

**Also worth saying, if true:** `/appeal` kept returning `501 not_implemented` because my `app.py` still had the starter stub. I only found that by reading the response text, then replaced the function and restarted.

---

## Rate Limiting

*Unit 8.*

## Attack Run — Before

*Unit 8.*

## Run Log and Verdicts

*Unit 8.*

## The Improvement

*Unit 8.*

## What's Still Broken

*Unit 8.*

---

📖 **How to run this project: [RUNNING.md](RUNNING.md)**