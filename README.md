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

*Status: steps 1, 2 and 6 are built (Milestone 3). Steps 3 to 5 are built in Milestones 4 and 5.*

---

## Signals and Scoring

<!-- For each signal: what property it measures, why that might differ between
     human and AI writing, and WHAT IT CAN'T SEE.

     The blind spot is not optional. If you can't name it you don't understand
     the signal yet — and unit 8's attack set is built out of blind spots. -->

### Signal one — the model signal

**What it measures:** How predictable the text is to a language model (gpt2) running on my own machine. The measure is perplexity (how surprised the model is by the text), squashed onto 0-1 so that higher means more likely AI. Implemented in `detector.py::model_signal`.

**Why that might differ between human and AI writing:** A language model writes by repeatedly picking a likely next word, so text it produces tends to be text a model finds easy to predict. Human writing wanders more, with odd word choices, abrupt turns, and specific details instead of typical ones.

**What it can't see:** Predictability is not authorship. It rewards common words in short, ordinary sentences, and plenty of real people write that way: second-language writers sticking to vocabulary they're sure of, younger writers, anyone writing plainly on purpose. That writing scores as machine-like. The reverse also happens: dense, ornate human prose full of rare words scores as human, and so can AI text that has been made to sound unusual. The writers this signal is most likely to wrongly accuse are the ones with the least room to argue back.

**Milestone 3 check: signal one run on three texts** (from `python detector.py`; `model_signal`, higher = more AI-like):

| Test text | model_score |
|---|---|
| 1. Text I'm sure a person wrote (mine) | 0.6892 |
| 2. A text in between | 0.7047 |
| 3. Text I'm sure is AI (the "implementation of the proposed framework" paragraph) | 0.8577 |

Signal one alone scored both of my texts above the 0.65 `AI_THRESHOLD`. The gap between my own writing and the AI paragraph is only about 0.17. That supports the blind spot above: this signal on its own would call some of my human writing AI, so it can't be trusted alone. This is the reason for the second signal and for the combining rule in Milestone 4.

### Signal two — the style signal

**What it measures:** The shape of the text without reading it, using sentence-length spread, type-token ratio (vocabulary repetition), and punctuation density. Implemented in `stylometry.py::style_signal`. *(Built in Milestone 4.)*

**Why that might differ between human and AI writing:** Generated prose tends toward even, mid-length sentences and a narrower band of vocabulary. Human writing is lumpier, with a three-word sentence next to a forty-word one.

**What it can't see:** It cannot read. Anything that changes the shape without changing the substance moves it: typos, chopped-up sentences, a pasted quotation. That is very cheap to do on purpose. Type-token ratio also falls as text gets longer, so length alone can look like a signal. Short, plain human writing looks uniform to it, which is the same writer signal one is likely to accuse.

### The combining rule

<!-- Write the rule in words, then give the numbers. What happens when the two
     signals disagree? -->

**The rule:** *PENDING (Milestone 4). Write the rule in words before coding it.*

**The numbers:** *PENDING (Milestone 4). Weights and thresholds live in `config.py`.*

**Where it lives:** `scoring.py::combine_signals`

<!-- ⚠️ The grader checks your code against that line. If your rule lives
     somewhere else, say where. -->

**A case where my two signals split, and what my rule does with it:**

*PENDING (Milestone 4).*

---

## Label Variants

<!-- The exact text of all three labels, as a reader would see them, and the
     score range each covers.

     Write them for someone who has never heard the word "threshold". -->

*PENDING (Milestone 5). Current `config.py` thresholds: human below 0.35, unsure 0.35 to 0.65, AI above 0.65.*

| Label | Score range | The exact text a reader sees |
|---|---|---|
| high-confidence human |  |  |
| unsure |  |  |
| high-confidence AI |  |  |

**Why I worded the "unsure" one this way:**

*PENDING (Milestone 5).*

---

## Sample Run

<!-- One submission and one appeal, pasted as text: the request, the response,
     and the log entries. -->

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

*(Milestone 3 state: `model_score` is real. `style_score` and `confidence` are `null`, and the label is a placeholder, until Milestones 4 and 5.)*

```powershell
PS> $body = @{ text = "The implementation of the proposed framework requires careful consideration of several key factors. First, it is important to note that the system must be able to handle a wide variety of different inputs. Additionally, the overall performance of the solution depends on a number of important variables."; creator_id = "me" } | ConvertTo-Json
PS> Invoke-RestMethod -Method Post -Uri http://127.0.0.1:5000/submit -ContentType "application/json" -Body $body | ConvertTo-Json
```

```json
PASTE THE REAL /submit RESPONSE HERE
```

**A rejected request**

My first attempts sent the body through `curl.exe` from PowerShell, and the quoting mangled it, so Flask never received a JSON object. The service correctly refused each one with `invalid_body` and logged it. That is the bad-input path from criterion 5 working, and it shows up in the audit log:

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
PS> curl.exe "http://127.0.0.1:5000/log?limit=3"
```

```json
PASTE THE REAL /log OUTPUT HERE (should show your decided entry with a content_id and model_score)
```

---

## How I Used AI

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