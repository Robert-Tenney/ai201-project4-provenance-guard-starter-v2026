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
>
> **The rest of this file is your submission.** Fill it in as you go.

---

<!-- ═══════════════════════ UNIT 7 — THE BUILD ═══════════════════════ -->

## What This Does

A submission takes this path:

1. **Arrives at `POST /submit`** (`app.py::submit`), which validates it: JSON object, non-empty `text` under the 20,000-character cap, non-empty `creator_id`. Bad input gets a clear 4xx error and a logged rejection, never a crash.
2. **Signal one** (`detector.py::model_signal`) runs gpt2 locally and adds a 0-1 score for how predictable the text is.
3. **Signal two** (`stylometry.py::style_signal`) adds a 0-1 score for the shape of the text: sentence-length spread, vocabulary repetition, and punctuation.
4. **The combining rule** (`scoring.py::combine_signals`) turns the two scores into one combined score.
5. **Labelling** (`scoring.py::score_to_label`) maps that score to a guess (`ai`, `human`, or `unsure`) and a plain-language sentence for the reader.
6. **The audit log** (`audit.py::log_decision`) appends a JSON line to `logs/audit.jsonl` with the content id, creator id, both signal scores, the combined score, the label, and status `decided`.
7. **The response** returns the `content_id`, guess, confidence, label, and both signal scores. If the writer disagrees, `POST /appeal` (`app.py::appeal`) with that `content_id` checks the id exists, appends an appeal entry with status `under_review` and the writer's reasoning beside the original decision, and confirms. The log is append-only, so the original decision is never overwritten.

---

## Signals and Scoring

### Signal one — the model signal

**What it measures:** How predictable the text is to a language model (gpt2) running on my own machine. The measure is perplexity (how surprised the model is by the text), squashed onto 0-1 so that higher means more likely AI. Implemented in `detector.py::model_signal`.

**Why that might differ between human and AI writing:** A language model writes by repeatedly picking a likely next word, so text it produces tends to be text a model finds easy to predict. Human writing wanders more, with odd word choices, abrupt turns, and specific details instead of typical ones.

**What it can't see:** Predictability is not authorship. It rewards common words in short, ordinary sentences, and plenty of real people write that way: second-language writers sticking to vocabulary they're sure of, younger writers, anyone writing plainly on purpose. That writing scores as machine-like. The reverse also happens: dense, ornate human prose full of rare words scores as human, and so can AI text that has been made to sound unusual. The writers this signal is most likely to wrongly accuse are the ones with the least room to argue back.

**Milestone 3 check: signal one alone on three texts** (`python detector.py`, higher = more AI-like):

| Test text | model_score |
|---|---|
| Human (mine): "What in the name of 'Sweet Home Alabama' was I forced to hear?" | 0.6892 |
| Human (mine): "What in the name of 'Sweet Home Alabama' did I just witness" | 0.7047 |
| AI: the "implementation of the proposed framework" paragraph | 0.8577 |

Signal one alone scored both of my own short sentences above the 0.65 `AI_THRESHOLD`, and the gap between them and the AI paragraph was only about 0.15 to 0.17. On its own it would have accused me of being a machine. That is the false positive from Milestone 1 happening to a real writer, and it is why there is a second signal and a combining rule.

A longer, more personal piece of my own writing (the "Grandfather's kitchen" paragraph) scored 0.2282 on signal one, clearly human. So signal one separates well on distinctive, specific prose and badly on short, plain sentences, which is the blind spot described above.

### Signal two — the style signal

**What it measures:** The shape of the text without reading it, from three measures (`stylometry.py`):
- `sentence_length_spread`: standard deviation of sentence length divided by the mean (coefficient of variation). Higher = more varied.
- `type_token_ratio`: unique words over total words, measured on the first 100 words only so text length alone can't move it. Higher = more varied vocabulary.
- `punctuation_density`: commas, semicolons, colons, dashes, brackets, quotes, `!` and `?` per word (full stops excluded). Higher = more punctuation.

All three run "higher = more human", so `style_signal` flips each onto a 0-1 "AI-ness" scale (linear between a fully-AI value and a fully-human value) and takes a weighted average: spread 0.5, type-token ratio 0.3, punctuation 0.2. Higher = more likely AI, the same direction as signal one. Edge cases return neutral 0.5 (under 3 words), and a one-sentence text drops the spread measure and re-weights the other two.

**Why that might differ between human and AI writing:** Generated prose tends toward even, mid-length sentences and a narrower band of vocabulary. Human writing is lumpier, with a three-word sentence next to a forty-word one.

**What it can't see:** It cannot read. Anything that changes the shape without changing the substance moves it: typos, chopped-up sentences, a pasted quotation. That is very cheap to do on purpose. Short, plain human writing also looks uniform to it, the same writers signal one is likely to accuse.

**What my first runs showed about it** (`python stylometry.py`, raw measure values):

| Text | spread | type-token | punctuation | style_signal |
|---|---|---|---|---|
| AI paragraph (85 words, 5 sentences) | 0.239 | 0.753 | 0.047 | 0.6160 |
| "...did I just witness" (12 words, 1 sentence) | 0.000 | 1.000 | 0.000 | 0.4000 |
| "...was I forced to hear?" (13 words, 1 sentence) | 0.000 | 1.000 | 0.077 | 0.2103 |

- The direction is right: the AI paragraph scored higher (0.616) than both of my sentences (0.40 and 0.21).
- **Type-token ratio does almost nothing here.** The AI paragraph's 0.753 sits at the human end of my scale (0.75), so it added zero AI-ness. Very short texts score 1.000 because there is hardly any room to repeat a word. So the ratio is uninformative at both ends of the length range.
- **The one-sentence texts have no spread measure**, so their scores rest on only vocabulary and punctuation. The difference between 0.40 and 0.21 for two near-identical sentences came entirely from one question mark. That is a fragile basis, and it is the "short text" blind spot in practice.
- The AI paragraph's 0.616 is only moderately high. It came from low sentence-length spread (0.239) and low punctuation.

*Scale endpoints and weights: none changed so far. They are the starting guesses I set before measuring.*

### The combining rule

**The rule:** Take a weighted average of the two signals. If the two signals differ by more than 0.35, clamp the result into the unsure band (never below 0.36 or above 0.64), so a disagreement can never produce a confident label of either kind.

**Why:** Signal one over-scores plain human writing (my own short sentences scored 0.69 and 0.70), and signal two is easy to fool with typos. When they point opposite ways that is uncertainty, not a verdict. The cost is that AI text caught by only one signal lands in "unsure" instead of "ai". I chose that trade because a wrong accusation costs a writer far more than a missed detection costs the site.

**The numbers:** `WEIGHT_MODEL_SIGNAL = 0.5`, `WEIGHT_STYLE_SIGNAL = 0.5`, `DISAGREEMENT_GAP = 0.35`, `HUMAN_THRESHOLD = 0.35`, `AI_THRESHOLD = 0.65`. All in `config.py`. Equal weights because neither signal has earned more trust yet.

**Where it lives:** `scoring.py::combine_signals`

**Both signals on the same texts** (rows 1 to 3 calculated from the rule; the others are real `/submit` output):

| Input | model_score | style_score | combined | gap | guess |
|---|---|---|---|---|---|
| AI paragraph (5-sentence version) | 0.8577 | 0.6160 | 0.7369 | 0.24 | ai |
| Human: "...was I forced to hear?" | 0.6892 | 0.2103 | 0.4498 | 0.48 | unsure (split) |
| AI text (3-sentence version, via /submit) | 0.8864 | 0.6208 | 0.7536 | 0.27 | ai |
| Human: "...did I just witness" (via /submit) | 0.7047 | 0.4000 | 0.5524 | 0.30 | unsure |
| Human: "Grandfather's kitchen" paragraph (via /submit) | 0.2282 | 0.2472 | 0.2377 | 0.02 | human |
| Human + 1 added sentence: "...forced to hear? I honestly do not know what that was." (via /submit) | 0.7905 | 0.5173 | 0.6539 | 0.27 | ai (false positive) |

*Still to add from `python calibrate.py`: the formal-human and lightly-edited-AI inputs. PASTE THEIR ROWS HERE.*

**A case where my two signals split, and what my rule does with it:** "What in the name of 'Sweet Home Alabama' was I forced to hear?" is a short sentence I wrote myself. Signal one scored it 0.6892 (AI-like), because it is short and made of common words. Signal two scored it 0.2103 (human-like), because the question mark gave it some punctuation and it has no repeated words. The gap is 0.48, above the 0.35 limit, so the clamp applies. The weighted average of 0.4498 is already inside the unsure band, so it stays 0.4498 and the guess is "unsure". Signal one alone would have given this a confident AI accusation (0.6892 is above 0.65). The combining rule is what prevented a false positive on my own writing.

**What the table showed:**
- My short plain sentences never got the AI label on their own, but neither reached "human" either. Only the longer, more distinctive "Grandfather's kitchen" paragraph did (0.2377).
- **A false positive on my own writing:** "What in the name of 'Sweet Home Alabama' was I forced to hear? I honestly do not know what that was." scored model 0.7905 and style 0.5173, combined 0.6539, and got the `ai` label. The signals differed by only 0.27, under the 0.35 limit, so the disagreement clamp never applied. The second sentence was written by an AI assistant, so this is a near-miss rather than a clean sample, but it is the Milestone 1 false positive in practice. The combining rule only protects writers when the signals disagree; it does nothing when both lean AI-like on short, plain text. I have not changed the thresholds. A candidate fix for unit 8 is requiring both signals to be at least 0.5 before the `ai` label can appear.
- The AI texts (0.7369 and 0.7536) scored well above the human paragraph (0.2377), but only about 0.24 to 0.25 above the average of my two short sentences (0.5011). Criterion 2's target is 0.30 on 5 AI and 5 human pieces, and I have only a handful, so this is not a verdict. I am not changing the target. The full test runs in unit 8.
- Neither signal returned the same number for everything, but the type-token ratio did nothing useful on this data (see above).

---

## Label Variants

| Label | Score range | The exact text a reader sees |
|---|---|---|
| high-confidence human | 0.00 – 0.35 | We think this was probably written by a person. This is an estimate, not a certainty. |
| unsure | 0.35 – 0.65 | We can't tell whether this was written by a person or by an AI. That is not a judgment about your work, and plenty of people who write plainly get this result. If you would like it looked at, you can appeal. |
| high-confidence AI | 0.65 – 1.00 | Our tools think this may have been written with AI, but they can get this wrong, especially with short or plainly written text. If you wrote it yourself, you can appeal and we will mark it for review. |

The labels are defined in `scoring.py` (`LABEL_HUMAN`, `LABEL_UNSURE`, `LABEL_AI`) and chosen by `scoring.py::score_to_label`. The ranges come from `scoring.label_ranges()`, which printed:

```
[('high-confidence human', '0.00 – 0.35'), ('unsure', '0.35 – 0.65'), ('high-confidence AI', '0.65 – 1.00')]
```

When the two signals disagree by more than 0.35, the combining rule clamps the score into 0.36 to 0.64, so a disagreement can only produce "unsure".

**Why I worded the "unsure" one this way:** The person reading it may well have written every word, so it opens with what we can't know instead of what we suspect, and it says directly that it is not a judgment about their work. I added "plenty of people who write plainly get this result" because that is the honest reason it happens (signal one scores plain writing as machine-like), and it tells the reader the result isn't about them. It ends by pointing to the appeal. The AI label carries a hedge too, since my own short human sentences scored above the AI threshold on signal one alone.

**Reachability check:**

| Label | Reached by | combined | Source |
|---|---|---|---|
| ai | "implementation of the proposed framework" paragraph | 0.7536 | real `/submit` |
| human | "Grandfather's kitchen" paragraph | 0.2377 | real `/submit` |
| unsure | "What in the name of 'Sweet Home Alabama' did I just witness" | 0.5524 | real `/submit` (model 0.7047, style 0.4000) |

All three labels are reachable. Plain short writing did not reach "human" on its own, and one short human-written input crossed into "ai". I did not move the thresholds to change either result.

---

## Sample Run

**A submission** (this one returns the `unsure` label)

```powershell
PS> $unsure2 = @{ text = "What in the name of 'Sweet Home Alabama' did I just witness"; creator_id = "me" } | ConvertTo-Json
PS> $r = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/submit -ContentType "application/json" -Body $unsure2
```

The response carried `content_id` `cba375b1-ad9c-40f9-a3f2-17c05ca869d7`, guess `unsure`, confidence 0.5524, model_score 0.7047 and style_score 0.4. An earlier identical submission returned this full response:

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

(The text sent for this one contained a copy-paste typo, "awide", exactly as typed.)

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

(Sent with a typo, "burntsugar", exactly as typed.)

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

**What the log shows afterwards**

The original decision is unchanged and the appeal sits after it:

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

**An appeal against an id that doesn't exist**

```powershell
PS> $bad = @{ content_id = "not-a-real-id"; reasoning = "test" } | ConvertTo-Json
PS> Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/appeal -ContentType "application/json" -Body $bad
```

```json
{
  "error": "unknown_content_id",
  "message": "We couldn't find a submission with that content_id. Check it matches the one you were given."
}
```

This returned HTTP 404. (An earlier appeal attempt returned `501 not_implemented`, because the running service still had the starter's appeal stub. After I replaced `app.py::appeal` and restarted, it worked as shown.)

**A rejected request**

My first attempts sent the body through `curl.exe` from PowerShell, and the quoting mangled it, so Flask never received a JSON object. The service refused each one with `invalid_body` and logged it. That is the bad-input path from criterion 5 working:

```jsonl
{"timestamp": "2026-10-29T18:37:18+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T18:37:38+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T18:38:09+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T19:14:49+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T19:15:32+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
```

---

## How I Used AI

*Fill in two specific moments from your own work by Milestone 6. For each: what you asked, what came back, what you changed.*

**Moment 1**

- *What I asked for:*
- *What came back:*
- *What I changed:*

**Moment 2**

- *What I asked for:*
- *What came back:*
- *What I changed:*

<!-- ═══════════════════════ UNIT 8 — THE TEST ═══════════════════════ -->

---

## Rate Limiting

**My limits:** ___ per minute, ___ per day

**Why those numbers and not others:**

**What a caller counts as:**

**The run of status codes when I pushed past it:**

```

```

---

## Attack Run — Before



**Ten audit log entries from the run**

```json

```

**The single worst outcome** — the one I'd least want to explain to a writer
whose work got caught by it:



---

## Run Log and Verdicts

| Criterion | Target | Run 1 | Run 2 | Run 3 | Verdict |
|---|---|---|---|---|---|
| 1.  |  |  |  |  |  |
| 2.  |  |  |  |  |  |
| 3.  |  |  |  |  |  |
| 4.  |  |  |  |  |  |
| 5.  |  |  |  |  |  |

**Real output from one trial**, pasted as text, naming the file and function
that produced it:

```json

```

**Diagnoses**



---

## The Improvement

**What I changed:**

**Which diagnosis pointed at it:**

### Attack Run — After



**Did it help, and how do I know:**



---

## What's Still Broken



**The trade I'd make if this ran for real:**



---

📖 **How to run this project: [RUNNING.md](RUNNING.md)**