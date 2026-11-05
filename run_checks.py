#!/usr/bin/env python3
"""
The two criteria run_eval.py cannot check.  <- UNIT 8, MILESTONE 3

    python app.py                              # in one terminal
    python run_checks.py --label before        # in another
    python run_checks.py --label after

run_eval.py only sends /submit and reads the response. Two of my criteria need
more than that:

  Criterion 4 (appeal path): submit 10 texts, appeal each against its real
      content_id, then read GET /log and check that every appeal returned 200
      "under_review", added exactly one "appeal" line, and left the original
      "decided" line unchanged. An unknown content_id must return 404 and add
      no appeal line.

  Criterion 5 (bad input): send 6 bad requests (text missing, empty,
      whitespace only, null, 50,000 words, and a JSON list instead of an
      object). Each must return a 4xx JSON body with an "error" field, none a
      500, each must leave exactly one new rejection line in the log, and
      /ping must still answer afterwards.

Each runs three times (--trials), because the target has to hold in all three.
A 429 does not count as a pass for criterion 5: it means the rate limiter
answered before my validation ran, so the validation was not tested.

This script decides pass/fail for these two because both criteria are
mechanical. I still write the MET/MISSED verdict myself in Milestone 4.

It reads the log through GET /log?limit=500, the same as the attack run, so it
assumes each trial adds well under 500 lines and the log is returned oldest
first.
"""

import argparse
import datetime as dt
import json
import sys
import uuid

import config

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

DEFAULT_URL = f"http://{config.HOST}:{config.PORT}"
LOG_LIMIT = 500

# Ten short, plain sentences to submit and then appeal. Whatever they score,
# the appeal path must behave the same. Each is long enough to be scored.
APPEAL_TEXTS = [
    "I went to the market this morning and bought bread, apples and a small jar of honey.",
    "The train was late again, so I read most of my book standing up in the corridor.",
    "Our team meets on Thursdays to go over what broke during the week and why it broke.",
    "My sister is learning to play the cello, and the whole house now hears her practise.",
    "It rained all weekend, which gave me a good excuse to finally tidy the garage.",
    "The recipe calls for two eggs, but I only had one, so the cake came out flat and dense.",
    "I forgot my umbrella at the office, and the walk home was a long, wet reminder of it.",
    "He explained the bug to me three times before I understood that the loop never stopped.",
    "We repainted the kitchen last month, and the yellow is brighter than any of us expected.",
    "She sent me a long message about the trip, and I answered it with a single photograph.",
]


def get_log(requests, url):
    """The newest LOG_LIMIT lines of the audit log."""
    response = requests.get(f"{url}/log", params={"limit": LOG_LIMIT}, timeout=30)
    response.raise_for_status()
    return response.json().get("entries", [])


def is_decision(entry, content_id):
    return entry.get("content_id") == content_id and entry.get("status") == "decided" \
        and "event" not in entry


def is_appeal(entry, content_id=None):
    if entry.get("event") != "appeal":
        return False
    return content_id is None or entry.get("content_id") == content_id


def is_rejection(entry):
    return entry.get("event") == "rejected"


# ---------------------------------------------------------------------------
# Criterion 4
# ---------------------------------------------------------------------------
def check_appeals(requests, url, trial, timeout):
    problems = []
    rows = []

    # 1. Ten real submissions. A different creator_id each, so the rate
    #    limiter (5 per minute per creator) never gets involved.
    submitted = []
    for i, text in enumerate(APPEAL_TEXTS, 1):
        body = {"text": text, "creator_id": f"appeal_t{trial}_{i}"}
        r = requests.post(f"{url}/submit", json=body, timeout=timeout)
        if r.status_code != 200:
            problems.append(f"submission {i} returned {r.status_code}, not 200")
            continue
        submitted.append((i, r.json()["content_id"]))

    before = get_log(requests, url)
    appeals_before = sum(1 for e in before if is_appeal(e))
    decided_before = {}
    for _, cid in submitted:
        found = [e for e in before if is_decision(e, cid)]
        if len(found) != 1:
            problems.append(f"{cid}: expected 1 decided line before appealing, found {len(found)}")
        decided_before[cid] = found[0] if found else None

    # 2. Ten appeals.
    reasons = {}
    for i, cid in submitted:
        reason = f"I wrote this myself (trial {trial}, appeal {i})."
        reasons[cid] = reason
        r = requests.post(f"{url}/appeal", json={"content_id": cid, "reasoning": reason},
                          timeout=timeout)
        payload = r.json() if r.content else {}
        ok = r.status_code == 200 and payload.get("status") == "under_review"
        rows.append({"appeal": i, "content_id": cid, "http": r.status_code,
                     "status": payload.get("status"), "ok": ok})
        if not ok:
            problems.append(f"appeal {i}: HTTP {r.status_code}, status {payload.get('status')!r}")

    # 3. The log afterwards.
    after = get_log(requests, url)
    appeals_after = sum(1 for e in after if is_appeal(e))
    added = appeals_after - appeals_before
    if added != len(submitted):
        problems.append(f"log gained {added} appeal lines, expected {len(submitted)}")

    for i, cid in submitted:
        mine = [e for e in after if is_appeal(e, cid)]
        if len(mine) != 1:
            problems.append(f"appeal {i}: {len(mine)} appeal lines for {cid}, expected exactly 1")
        elif mine[0].get("reasoning") != reasons[cid]:
            problems.append(f"appeal {i}: appeal line does not carry the writer's reasoning")
        elif mine[0].get("status") != "under_review":
            problems.append(f"appeal {i}: appeal line status is {mine[0].get('status')!r}")

        decided_now = [e for e in after if is_decision(e, cid)]
        if len(decided_now) != 1 or decided_now[0] != decided_before[cid]:
            problems.append(f"appeal {i}: the original decided line changed or went missing")

    # 4. Unknown content_id: 404 and no appeal line.
    fake = str(uuid.uuid4())
    r = requests.post(f"{url}/appeal", json={"content_id": fake, "reasoning": "no such id"},
                      timeout=timeout)
    after_fake = get_log(requests, url)
    appeal_lines_for_fake = [e for e in after_fake if is_appeal(e, fake)]
    appeals_total_now = sum(1 for e in after_fake if is_appeal(e))
    rows.append({"appeal": "unknown id", "content_id": fake, "http": r.status_code,
                 "ok": r.status_code == 404})
    if r.status_code != 404:
        problems.append(f"unknown content_id returned {r.status_code}, expected 404")
    if appeal_lines_for_fake or appeals_total_now != appeals_after:
        problems.append("unknown content_id left an appeal line in the log")

    return {"passed": not problems, "problems": problems, "rows": rows}


# ---------------------------------------------------------------------------
# Criterion 5
# ---------------------------------------------------------------------------
def bad_requests(trial):
    """(label, what to send). The value is passed to json= exactly as written."""
    cid = lambda name: f"bad_t{trial}_{name}"  # noqa: E731
    return [
        ("text missing",      {"creator_id": cid("missing")}),
        ("text empty",        {"text": "", "creator_id": cid("empty")}),
        ("text whitespace",   {"text": "   \n\t  ", "creator_id": cid("blank")}),
        ("text null",         {"text": None, "creator_id": cid("null")}),
        ("text 50,000 words", {"text": "word " * 50000, "creator_id": cid("huge")}),
        ("body is a list",    ["this", "is", "a", "list"]),
    ]


def check_bad_input(requests, url, trial, timeout):
    problems = []
    rows = []

    log_before = get_log(requests, url)
    rejections_before = sum(1 for e in log_before if is_rejection(e))

    for label, body in bad_requests(trial):
        try:
            r = requests.post(f"{url}/submit", json=body, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label}: request failed ({type(exc).__name__}: {exc})")
            rows.append({"request": label, "http": None, "error": None, "ok": False})
            continue

        try:
            payload = r.json()
        except ValueError:
            payload = None
        error = payload.get("error") if isinstance(payload, dict) else None

        ok = (400 <= r.status_code < 500 and r.status_code != 429 and bool(error))
        rows.append({"request": label, "http": r.status_code, "error": error, "ok": ok})
        if r.status_code >= 500:
            problems.append(f"{label}: HTTP {r.status_code} (a crash)")
        elif r.status_code == 429:
            problems.append(f"{label}: HTTP 429, rate limited before validation ran (not tested)")
        elif not (400 <= r.status_code < 500):
            problems.append(f"{label}: HTTP {r.status_code}, expected a 4xx")
        elif not error:
            problems.append(f"{label}: no JSON 'error' field in the body")

    log_after = get_log(requests, url)
    added = sum(1 for e in log_after if is_rejection(e)) - rejections_before
    if added != 6:
        problems.append(f"log gained {added} rejection lines, expected 6")

    ping_status = None
    try:
        ping_status = requests.post(f"{url}/ping", json={"message": "hello"},
                                    timeout=timeout).status_code
    except Exception:  # noqa: BLE001
        pass
    ping_ok = ping_status == 200
    rows.append({"request": "/ping afterwards", "http": ping_status, "ok": ping_ok})
    if not ping_ok:
        problems.append("/ping did not answer 200 afterwards")

    return {"passed": not problems, "problems": problems, "rows": rows}


# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--label", default="")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    try:
        import requests
    except ImportError:
        print("The `requests` package isn't installed.\nRun: pip install -r requirements.txt",
              file=sys.stderr)
        sys.exit(1)

    try:
        requests.get(f"{args.url}/health", timeout=5)
    except Exception:  # noqa: BLE001
        print(f"Nothing answering at {args.url}. Start it:  python app.py", file=sys.stderr)
        sys.exit(1)

    if args.trials < 3:
        print(f"!! {args.trials} trial(s). The submission asks for three.\n")

    results = {"4": [], "5": []}
    for trial in range(1, args.trials + 1):
        print(f"\ntrial {trial}")
        for number, fn, name in (("4", check_appeals, "appeal path"),
                                 ("5", check_bad_input, "bad input")):
            outcome = fn(requests, args.url, trial, args.timeout)
            results[number].append(outcome)
            print(f"  criterion {number} ({name}): {'PASS' if outcome['passed'] else 'FAIL'}")
            for problem in outcome["problems"]:
                print(f"      - {problem}")

    write_report(results, args)


def write_report(results, args):
    config.RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    label = f"_{args.label}" if args.label else ""
    path = config.RESULTS_DIR / f"checks_{stamp}{label}.md"

    names = {"4": "The appeal path", "5": "Bad input"}
    n = args.trials

    lines = [
        f"# Criteria 4 and 5{f' - {args.label}' if args.label else ''}",
        "",
        "- Produced by: `run_checks.py::main`",
        f"- {n} trials per criterion - {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "| Criterion | " + " | ".join(f"Trial {i}" for i in range(1, n + 1)) + " | Held in all trials |",
        "|---|" + "|".join(["---"] * n) + "|---|",
    ]
    for number in ("4", "5"):
        cells = ["PASS" if o["passed"] else "FAIL" for o in results[number]]
        held = "yes" if all(o["passed"] for o in results[number]) else "no"
        lines.append(f"| {number}. {names[number]} | " + " | ".join(cells) + f" | {held} |")

    lines += ["", "---", "", "## What actually came back", ""]
    for number in ("4", "5"):
        lines += [f"### Criterion {number}: {names[number]}", ""]
        for trial, outcome in enumerate(results[number], 1):
            lines += [f"**Trial {trial}: {'PASS' if outcome['passed'] else 'FAIL'}**", ""]
            if outcome["problems"]:
                lines += [f"- {p}" for p in outcome["problems"]] + [""]
            lines += ["```json", json.dumps(outcome["rows"], indent=2), "```", ""]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {path.relative_to(config.ROOT)}")
    print("Commit it. It's the evidence the checks actually happened.")


if __name__ == "__main__":
    main()