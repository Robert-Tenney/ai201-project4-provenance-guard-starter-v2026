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

<!-- ─────────────────────────────────────────────────────────────────────────
     Unit 7 asks for the first five sections. Unit 8 adds the five below.

     Everything is pasted as TEXT. A pasted curl response gets full credit;
     a picture of your terminal gets none.
     ───────────────────────────────────────────────────────────────────────── -->

<!-- ═══════════════════════ UNIT 7 — THE BUILD ═══════════════════════ -->

## What This Does

A submission takes this path:

1. **Arrives at `POST /submit`** (`app.py::submit`), which validates it: JSON object, non-empty `text` under the 20,000-character cap, non-empty `creator_id`. Bad input gets a clear 4xx error and a logged rejection, never a crash.
2. **Signal one** (`detector.py::model_signal`) runs gpt2 locally and adds a 0-1 score for how predictable the text is.
3. **Signal two** (`stylometry.py::style_signal`) adds a 0-1 score for the shape of the text: sentence-length spread, vocabulary repetition, and punctuation.
4. **The combining rule** (`scoring.py::combine_signals`) turns the two scores into one combined score.
5. **Labelling** (`scoring.py::score_to_label`) maps that score to a guess (`ai`, `human`, or `unsure`) and a plain-language sentence for the reader.
6. **The audit log** (`audit.py::log_decision`) appends a JSON line to `logs/audit.jsonl` with the content id, creator id, both signal scores, the combined score, the label, and status `decided`.
7. **The response** returns the `content_id`, guess, confidence, label, and both signal scores. If the writer disagrees, `POST /appeal` with that `content_id` sets the status to `under_review` and records their reasoning next to the original decision.

*Status: steps 1 to 4 and 6 are built (Milestones 3 and 4). The label text in step 5 and the appeal in step 7 are built in Milestone 5.*

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

Signal one alone scored both of my own sentences above the 0.65 `AI_THRESHOLD`, and the gap between my writing and the AI paragraph was only about 0.15 to 0.17. On its own it would have accused me of being a machine. That is the false positive from Milestone 1 happening to a real writer, and it is why there is a second signal and a combining rule.

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

**Why:** Signal one over-scores plain human writing (my own texts scored 0.69 and 0.70), and signal two is easy to fool with typos. When they point opposite ways that is uncertainty, not a verdict. The cost is that AI text caught by only one signal lands in "unsure" instead of "ai". I chose that trade because a wrong accusation costs a writer far more than a missed detection costs the site.

**The numbers:** `WEIGHT_MODEL_SIGNAL = 0.5`, `WEIGHT_STYLE_SIGNAL = 0.5`, `DISAGREEMENT_GAP = 0.35`, `HUMAN_THRESHOLD = 0.35`, `AI_THRESHOLD = 0.65`. All in `config.py`. Equal weights because neither signal has earned more trust yet.

**Where it lives:** `scoring.py::combine_signals`

**Both signals on the same texts** (the first three rows are calculated from the rule, the fourth is real `/submit` output; confirm the first three with `python calibrate.py`):

| Input | model_score | style_score | combined | gap | guess |
|---|---|---|---|---|---|
| AI paragraph (5-sentence version) | 0.8577 | 0.6160 | 0.7369 | 0.24 | ai |
| Human: "...did I just witness" | 0.7047 | 0.4000 | 0.5524 | 0.30 | unsure |
| Human: "...was I forced to hear?" | 0.6892 | 0.2103 | 0.4498 | 0.48 | unsure (split) |
| AI text (3-sentence version, via /submit) | 0.8864 | 0.6208 | 0.7536 | 0.27 | ai |

*Still to add from `calibrate.py`: the formal-human and lightly-edited-AI inputs. PASTE THEIR ROWS HERE.*

**A case where my two signals split, and what my rule does with it:** "What in the name of 'Sweet Home Alabama' was I forced to hear?" is a short sentence I wrote myself. Signal one scored it 0.6892 (AI-like), because it is short and made of common words. Signal two scored it 0.2103 (human-like), because the question mark gave it some punctuation and it has no repeated words. The gap is 0.48, above the 0.35 limit, so the clamp applies. The weighted average of 0.4498 is already inside the unsure band, so it stays 0.4498 and the guess is "unsure". Signal one alone would have given this a confident AI accusation (0.6892 is above 0.65). The combining rule is what prevented a false positive on my own writing.

**What the table showed:**
- Plain human writing stayed below the 0.65 AI threshold in both cases, but neither human text reached the "human" label (both are "unsure"). That is a concern for Milestone 5, which needs all three labels reachable.
- The AI texts (0.7369 and 0.7536) scored about 0.24 to 0.25 above the human average (0.5011), short of criterion 2's 0.30 target. This is a very small sample: 2 AI texts and 2 human texts, not the 5 and 5 the criterion calls for. I am not changing the target. The full test runs in unit 8.
- Neither signal returned the same number for everything, but the type-token ratio did nothing useful on this data (see above).

---

## Label Variants

<!-- The exact text of all three labels, as a reader would see them, and the
     score range each covers.

     Write them for someone who has never heard the word "threshold". -->

*PENDING (Milestone 5). Current `config.py` thresholds: human at or below 0.35, unsure 0.35 to 0.65, AI at or above 0.65.*

| Label | Score range | The exact text a reader sees |
|---|---|---|
| high-confidence human |  |  |
| unsure |  |  |
| high-confidence AI |  |  |

**Why I worded the "unsure" one this way:**

*PENDING (Milestone 5).*

---

## Sample Run

**Before any submission** (the log is empty)

```powershell
PS> curl.exe "http://127.0.0.1:5000/log"
```

```json
{
  "count": 0,
  "entries": []
}
```

**A submission**

*(Milestone 4 state: `model_score`, `style_score` and `confidence` are all real. The label text is still a placeholder until Milestone 5. `confidence` is the combined score, where higher means more likely AI.)*

```powershell
PS> $body = @{ text = "The implementation of the proposed framework requires careful consideration of several key factors. First, it is important to note that the system must be able to handle a wide variety of different inputs. Additionally, the overall performance of the solution depends on a number of important variables."; creator_id = "me" } | ConvertTo-Json
PS> Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/submit -ContentType "application/json" -Body $body | ConvertTo-Json
```

```json
{
  "content_id": "8944c449-e06d-42ba-81f0-860e697d5a41",
  "guess": "ai",
  "confidence": 0.7536,
  "label": "PLACEHOLDER — write the high-confidence AI label.",
  "model_score": 0.8864,
  "style_score": 0.6208,
  "status": "decided"
}
```

The combining rule gave (0.8864 + 0.6208) / 2 = 0.7536. The two signals differ by 0.27, under the 0.35 disagreement limit, so no clamp applied, and 0.7536 is above the 0.65 AI threshold, so the guess is `ai`.

**A rejected request**

My first attempts sent the body through `curl.exe` from PowerShell, and the quoting mangled it, so Flask never received a JSON object. The service correctly refused each one with `invalid_body` and logged it. That is the bad-input path from criterion 5 working. These lines are from that earlier run (I cleared the log afterwards):

```jsonl
{"timestamp": "2026-10-29T18:37:18+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T18:37:38+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T18:38:09+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T19:14:49+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
{"timestamp": "2026-10-29T19:15:32+00:00", "creator_id": "unknown", "event": "rejected", "status": "rejected", "reason": "invalid_body"}
```

**An appeal**

*PENDING (Milestone 5).*

**What the log shows afterwards**

```powershell
PS> curl.exe "http://127.0.0.1:5000/log?limit=1"
```

```json
{
  "count": 1,
  "entries": [
    {
      "combined_score": 0.7536,
      "content_id": "8944c449-e06d-42ba-81f0-860e697d5a41",
      "creator_id": "me",
      "guess": "ai",
      "label": "PLACEHOLDER — write the high-confidence AI label.",
      "model_score": 0.8864,
      "status": "decided",
      "style_score": 0.6208,
      "timestamp": "2026-10-29T19:47:12+00:00"
    }
  ]
}
```

The log entry has the same `content_id` as the response, and it records both signal scores and the combined score.

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
<!-- Think about two people: a real writer submitting their own work a few
     times an hour, and a script sending a thousand variations to map your
     thresholds. Your numbers have to be liveable for the first and hostile to
     the second. -->

**What a caller counts as:** <!-- per address, or per creator_id? They fail
differently — one script can look like a thousand callers, and one household
can look like one. -->

**The run of status codes when I pushed past it:**

<!-- python run_attacks.py --flood 15 -->

```

```

---

## Attack Run — Before

<!-- Every attack with its outcome and a held-or-broke mark.
     `run_attacks.py --label before` produces the table. -->



**Ten audit log entries from the run**

<!-- Choose entries that show the interesting failures, not the ten easiest. -->

```json

```

**The single worst outcome** — the one I'd least want to explain to a writer
whose work got caught by it:



---

## Run Log and Verdicts

<!-- Your five criteria across three trials. `run_eval.py --label before`. -->

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

<!-- For each miss AND each broken attack: the stage and the mechanism.

     Your service has five stages: the route, signal one, signal two, the
     combining rule, and the label mapping.

     Not a diagnosis:  "The evasion attacks got through."
     A diagnosis:      "Four evasion inputs got through. All four had typos
                        added to AI-generated text. My second signal measures
                        word variety, and typos raise word variety — so it read
                        them as more human. My combining rule weights that
                        signal at 0.6, so it dragged the whole score down."

     Look for a pattern. Five failures on one signal is one problem, not five. -->



---

## The Improvement

**What I changed:**

**Which diagnosis pointed at it:**

### Attack Run — After

<!-- Same format. `run_attacks.py --label after` -->



**Did it help, and how do I know:**

<!-- Widening the unsure band usually fixes one problem and creates another.
     Reporting that trade honestly is worth full credit. -->



---

## What's Still Broken

<!-- For each attack still getting through and each criterion still missed:
     what you'd do, and why you stopped. -->



**The trade I'd make if this ran for real:**

<!-- Two sentences. Fewer wrong accusations means more AI text getting through.
     Say which way you'd go and WHO PAYS FOR IT. -->



<!-- ═════════════════════════════════════════════════════════════════════

     SUBMISSION CHECKLIST — unit 7

       [ ] criteria.md has five numbered criteria, each naming a target
       [ ] Each has a reason underneath
       [ ] POST /submit returns content_id, guess, confidence, label,
           model_score and style_score
       [ ] Two signals that measure DIFFERENT things, each with a named blind spot
       [ ] Three label variants, all reachable, written out in full
       [ ] POST /appeal changes status and writes to the log
       [ ] Signals and Scoring names scoring.py::combine_signals
       [ ] Sample Run: a submission AND an appeal, as pasted text
       [ ] At least four new commits
       [ ] Repository URL submitted — WRITE IT DOWN

     SUBMISSION CHECKLIST — unit 8

       [ ] Rate limiting on /submit, with your numbers justified
       [ ] The pasted run of status codes showing 429
       [ ] Audit log with all nine fields, recording rejections too
       [ ] The attack set run IN FULL, every input with an outcome
       [ ] Ten audit log entries pasted as text
       [ ] Five criteria across three trials
       [ ] A diagnosis for every miss and every broken attack — stage AND mechanism
       [ ] One improvement, with Attack Run — After
       [ ] What's Still Broken, including the production trade
       [ ] At least four new commits
       [ ] The SAME repository URL as last unit

     Do not delete and recreate this repository.
     ═════════════════════════════════════════════════════════════════════ -->

---

📖 **How to run this project: [RUNNING.md](RUNNING.md)**