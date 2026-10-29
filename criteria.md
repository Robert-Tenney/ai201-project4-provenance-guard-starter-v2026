# Acceptance criteria — Provenance Guard

Five criteria that say what "working" means for this service, written in unit 7
**before** you built it.

**All five are yours.** None are given.

An acceptance criterion names a number or something plainly observable. *"The
detector is accurate"* is an opinion — and perfect AI detection isn't possible
anyway, so it's an opinion about something nobody can deliver. *"Of 10 pieces I
wrote myself, at most 1 is labelled high-confidence AI"* is a criterion.

Under each, write a sentence or two on **why that target**. *"I allow at most 1
false positive in 10 because on a writing site a wrong accusation costs more
than a missed detection"* is a real answer.

> Missing your own targets next unit costs you nothing. Setting a target so
> easy you can't miss it does.

---

## Pick numbers you can defend

**Cover at least three of these five areas.** They're prompts, not a form.

| Area | A question it could answer |
|---|---|
| False positives | How often may a human's writing get called AI? |
| Score spread | How far apart should clearly-AI and clearly-human text score? |
| Label coverage | Is every one of your three labels actually reachable? |
| The appeal path | Does an appeal always change status and always get recorded? |
| Bad input | What happens when `text` is missing, empty, or enormous? |

Two things worth knowing before you pick numbers:

- **The expensive mistake is one-sided.** Missing some AI text costs the site a
  little. Accusing a real writer costs that person a lot. A criterion that
  treats both errors as equally bad is describing a system nobody would ship.
- **Unit 8 tests across three trials, and the target has to hold across all
  three.** Your first signal runs a model, so identical input can score
  differently twice. A target of "at most 1 in 10" against trials of 2, 1, 2 is
  a **miss**.

---
## 1. False positives: at most 1 of 10 of my own pieces is labelled high-confidence AI

Of 10 pieces I wrote myself (at least 3 of them plain, short-sentence writing like an email or a simple explanation), at most 1 gets the `ai` label (combined score above `AI_THRESHOLD`). This must hold in each of the three trials, not on average across them.

**Why this target:** On a writing site, a wrong accusation costs the writer far more than a missed detection costs the site, so this is the tightest number I set. I include plain writing on purpose, because signal one scores plain, common-word writing as machine-like. A test set of only ornate prose would let me hit the target without ever meeting the writers the system is most likely to accuse. I chose 1 of 10 rather than 0 because with a local model that scores the same text differently between runs, 0 would be a number I can't promise.

---

## 2. Score spread: clearly-AI text scores at least 0.30 higher than clearly-human text

Take 5 pieces I'm certain are AI-generated and 5 I'm certain a person wrote. The mean combined score of the AI group must be at least 0.30 above the mean of the human group, and at least 4 of the 5 AI pieces must score above 0.50.

**Why this target:** A system that scores everything near 0.5 is not detecting anything, however well-behaved it looks. 0.30 is wide enough that the unsure band (0.35 to 0.65) is a real middle rather than the only place anything lands. Requiring 4 of 5 AI pieces above 0.50 stops a large gap in the means from being produced by one extreme score.

---

## 3. Label coverage: all three labels are reachable, and borderline text lands in "unsure"

Across a set of 12 test inputs (4 clearly AI, 4 clearly human, 4 borderline), the labels `ai`, `human` and `unsure` each appear at least once. Of the 4 borderline pieces, at least 3 get `unsure`.

**Why this target:** A label nothing can reach is a gap in my ranges, and `config.py` already warns that a stub style signal makes the human label unreachable. Borderline text is where the system is most likely to be wrong, and "unsure" is the only label that admits it. If borderline text keeps getting a confident label, the thresholds are too narrow.

---

## 4. The appeal path: every appeal changes status and is recorded, and nothing is overwritten

For 10 appeals sent against real `content_id`s from earlier submissions: all 10 return HTTP 200 with `"status": "under_review"`. All 10 add exactly one new line to `logs/audit.jsonl` with `"event": "appeal"` and the writer's reasoning, and the original `decided` line for that content is unchanged. An appeal with an unknown `content_id` returns 404 and writes no appeal line.

**Why this target:** The appeal is the only thing between a wrong accusation and nothing at all, so it has to work every time and 10 of 10 is the only number that makes sense. Checking that the original line is untouched matters because the log is the evidence of what the system decided and when.

---

## 5. Bad input: malformed requests never crash the service

Send 6 bad requests: `text` missing, `text` empty, `text` whitespace only, `text` null, `text` of 50,000 words, and a JSON body that is a list instead of an object. All 6 return a 4xx response whose body is JSON with an `error` field, none returns a 500, each rejection leaves a line in the audit log, and `/ping` still answers afterwards.

**Why this target:** A crash on malformed input is one of the most common real failures, and the attack set next unit will send exactly these. A 500 means my handler never decided anything, so the writer gets no explanation. Requiring a log line for each rejection means a bad request can't happen without leaving a trace.

---

<!-- ─────────────────────────────────────────────────────────────────────────
     UNIT 8 — read this before you change anything above.

     If a criterion turns out to be BROKEN rather than merely unmet, you can
     revise it, and that earns credit. But never delete or edit the original
     line. Add the revision underneath, like this:

         ## 2. Uncertain cases land in the unsure band

         The system handles uncertain cases well.

         **Why this target:** ...

         > **Revised in unit 8:** Text I judged borderline scores between
         > 0.4 and 0.6 in at least 4 of 5 cases.
         >
         > **Why revised:** "well" gave me nothing to check. I couldn't
         > reach a verdict from it at all.

     That's a revision because the criterion couldn't be MEASURED.

     Lowering a target because you missed it is not a revision, and it costs
     you the point:

         ✗ "At most 1 of 10 of my own pieces flagged as AI" → "at most 3
            of 10", because 1 of 10 turned out to be hard.

     A number you missed stays where it is, gets diagnosed, and gets a fix
     attempted. That's where the points are.
     ───────────────────────────────────────────────────────────────────────── -->