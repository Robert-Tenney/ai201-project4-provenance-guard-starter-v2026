#!/usr/bin/env python3
"""
Provenance Guard — the web service.

    python app.py

Then, in another terminal:

    curl -X POST http://127.0.0.1:5000/ping \
      -H "Content-Type: application/json" \
      -d '{"message": "hello"}'

That's the example route. It works right now, before you've written anything.
**Leave it alone** — it's your control. When something breaks later, check that
`/ping` still answers: if it does, the problem is in your handler; if it
doesn't, it's the service itself.

Routes:

    POST /ping        the worked example. Already works. Don't edit it
    POST /submit      ← YOU BUILD THIS (Milestone 3, 4, 5)
    POST /appeal      ← YOU BUILD THIS (Milestone 5)
    GET  /log         the audit log. Already works
    GET  /health      is the service up, and is the model loaded
"""

import uuid

from flask import Flask, jsonify, request

import audit
import config
import detector
import scoring
import stylometry

app = Flask(__name__)

# Input limit from design.md. Anything longer is refused with a 413.
MAX_TEXT_CHARS = 20_000


# ── Rate limiting — UNIT 8 ────────────────────────────────────────────────────
# Off until you turn it on. In unit 8, Milestone 1:
#
#   1. Set RATE_LIMITING_ENABLED = True in config.py (or AI201_RATE_LIMITS=1)
#   2. Choose your numbers there
#   3. Uncomment the @limiter.limit(...) line on /submit below
#
# The order doesn't matter. While limiting is off, that decorator is a no-op
# that quietly does nothing, so uncommenting it early costs you nothing — but
# it also means no 429 until you've done step 1.
#
# The storage backend is preconfigured, which stops Flask-Limiter printing a
# warning on every start. In-memory is right for one laptop and wrong for
# anything real — a restart forgets every count.

class _LimiterOff:
    """
    Stands in for the real limiter while rate limiting is off.

    It makes @limiter.limit(...) a decorator that returns the route
    untouched, so uncommenting the line before you flip the setting is
    harmless instead of a crash on startup.
    """

    def limit(self, *args, **kwargs):
        return lambda view: view


limiter = _LimiterOff()
if config.RATE_LIMITING_ENABLED:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    def rate_limit_key():
        """
        Who a request counts against.

        Falling back to the caller's address is the easy version. Unit 8's
        stretch asks about per-creator limits instead — and the difference
        matters: one script can look like a thousand callers, and one household
        can look like one.
        """
        payload = request.get_json(silent=True)
        # A body that parses as JSON but isn't an object — a bare list or
        # string — has no .get. Without this guard the key function raises
        # BEFORE the route runs, and a malformed request becomes a 500 that
        # looks like your handler crashed when your handler never ran.
        if not isinstance(payload, dict):
            return get_remote_address()
        creator = payload.get("creator_id")
        return creator if isinstance(creator, str) and creator.strip() else get_remote_address()

    limiter = Limiter(
        key_func=rate_limit_key,
        app=app,
        storage_uri=config.RATE_LIMIT_STORAGE,
        default_limits=[],
    )

    @app.errorhandler(429)
    def rate_limited(exc):
        """
        A rejected request still leaves a trace.

        ⚠️ An attack that shows up only as an absence is one you'll never find.
        """
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            payload = {}
        audit.log_rejection(
            creator_id=payload.get("creator_id", "unknown"),
            reason="rate_limited",
            limit=str(exc.description),
        )
        return jsonify({
            "error": "rate_limited",
            "message": "Too many submissions. Try again shortly.",
            "limit": str(exc.description),
        }), 429


# ── The worked example. Already works. Don't edit. ────────────────────────────

@app.post("/ping")
def ping():
    """
    Everything a route needs, in six lines.

    JSON arrives as a dict. You pull things out of it. You return a dict and
    the framework turns it into a response. That's the whole idea.
    """
    payload = request.get_json(silent=True) or {}
    return jsonify({
        "ok": True,
        "you_sent": payload.get("message"),
        "service": "provenance-guard",
    })


@app.get("/health")
def health():
    """Is the service up, and has the model been downloaded yet?"""
    import detector

    loaded = detector._model is not None
    return jsonify({
        "ok": True,
        "detector_model": config.DETECTOR_MODEL,
        "detector_loaded": loaded,
        "rate_limiting": config.RATE_LIMITING_ENABLED,
        "log_entries": len(audit.read_entries()),
    })


# ── YOU BUILD THIS ────────────────────────────────────────────────────────────

def _reject(status_code, error, message, creator_id="unknown"):
    """
    Refuse a request: log the rejection, answer with the contract's error shape.

    An input that is refused and leaves no trace is exactly the kind of thing
    that can't be diagnosed later.
    """
    audit.log_rejection(creator_id=creator_id, reason=error)
    return jsonify({"error": error, "message": message}), status_code


@app.post("/submit")
# @limiter.limit(f"{config.RATE_LIMIT_PER_MINUTE}/minute;{config.RATE_LIMIT_PER_DAY}/day")
def submit():
    """
    Take a piece of text, decide about it, log the decision, answer.

    Expects JSON:
        {"text": "...", "creator_id": "..."}

    Milestone 4 state: both signals are real and combined into one score.
    The label TEXT is still a placeholder from scoring.score_to_label until
    Milestone 5; the guess (ai / human / unsure) is already real.
    """
    payload = request.get_json(silent=True)

    # 1. The body has to be a JSON object.
    if not isinstance(payload, dict):
        return _reject(400, "invalid_body",
                       "Send a JSON object with 'text' and 'creator_id'.")

    text = payload.get("text")
    creator_id = payload.get("creator_id")

    # Use the creator_id in the rejection log only if it is a usable string.
    logged_creator = creator_id if isinstance(creator_id, str) and creator_id.strip() else "unknown"

    # 2. creator_id: required, non-empty string.
    if not isinstance(creator_id, str) or not creator_id.strip():
        return _reject(400, "invalid_creator_id",
                       "'creator_id' is required and must be a non-empty string.",
                       logged_creator)

    # 3. text: required, non-empty string, under the length cap.
    if not isinstance(text, str) or not text.strip():
        return _reject(400, "invalid_text",
                       "'text' is required and must be a non-empty string.",
                       logged_creator)
    if len(text) > MAX_TEXT_CHARS:
        return _reject(413, "text_too_long",
                       f"'text' must be {MAX_TEXT_CHARS:,} characters or fewer.",
                       logged_creator)

    # 4. Signal one. Both failure modes are the caller's or the host's problem,
    #    not a crash.
    try:
        model_score = detector.model_signal(text)
    except ValueError:
        return _reject(422, "text_too_short",
                       "That text is too short to score. Try a few sentences.",
                       logged_creator)
    except detector.DetectorUnavailable:
        return _reject(503, "detector_unavailable",
                       "The detector model isn't available right now.",
                       logged_creator)

    # 5. Signal two, then the combining rule, then the guess.
    style_score = stylometry.style_signal(text)
    confidence = scoring.combine_signals(model_score, style_score)
    guess, label = scoring.score_to_label(confidence)   # label text: Milestone 5

    content_id = str(uuid.uuid4())
    status = "decided"

    # 6. Log it, with BOTH signal scores and the combined one.
    audit.log_decision(
        content_id=content_id,
        creator_id=creator_id,
        guess=guess,
        model_score=model_score,
        style_score=style_score,
        combined_score=confidence,
        label=label,
        status=status,
    )

    # 7. Answer with the full contract shape.
    return jsonify({
        "content_id": content_id,
        "guess": guess,
        "confidence": confidence,
        "label": label,
        "model_score": model_score,
        "style_score": style_score,
        "status": status,
    }), 200


@app.post("/appeal")
def appeal():
    """
    A writer says you got it wrong.

    Expects JSON: {"content_id": "...", "reasoning": "..."}

    Validates the body, checks the content_id belongs to a real decision,
    appends an appeal entry (status "under_review") beside the original, and
    confirms. The log is append-only, so the original decision is untouched.
    """
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return _reject(400, "invalid_body",
                       "Send a JSON object with 'content_id' and 'reasoning'.")

    content_id = payload.get("content_id")
    reasoning = payload.get("reasoning")

    if not isinstance(content_id, str) or not content_id.strip():
        return _reject(400, "invalid_body",
                       "'content_id' is required and must be a non-empty string.")
    if not isinstance(reasoning, str) or not reasoning.strip():
        return _reject(400, "invalid_body",
                       "'reasoning' is required: tell us why you think this was wrong.")

    content_id = content_id.strip()

    # The original decision is the entry with no "event" field.
    original = next((e for e in audit.entries_for(content_id) if "event" not in e), None)
    if original is None:
        return _reject(404, "unknown_content_id",
                       "We couldn't find a submission with that content_id. "
                       "Check it matches the one you were given.")

    audit.log_appeal(
        content_id=content_id,
        creator_id=original.get("creator_id", "unknown"),
        reasoning=reasoning.strip(),
    )

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "We received your appeal and marked this submission for review.",
    }), 200

# ── Already works ─────────────────────────────────────────────────────────────

@app.get("/log")
def get_log():
    """
    The audit log, most recent last.

        curl "http://127.0.0.1:5000/log?limit=10"

    You need this in unit 8 to paste entries into your README, and you need it
    now to see whether your logging is actually working.
    """
    limit = request.args.get("limit", type=int)
    entries = audit.read_entries(limit=limit)
    return jsonify({"count": len(entries), "entries": entries})


def main():
    print(f"Provenance Guard on http://{config.HOST}:{config.PORT}")
    print(f"  detector model:  {config.DETECTOR_MODEL}")
    print(f"  rate limiting:   {'ON' if config.RATE_LIMITING_ENABLED else 'off (unit 8)'}")
    print(f"  audit log:       {config.AUDIT_LOG}")
    print()
    print("Check it's alive:")
    print(f'  curl -X POST http://{config.HOST}:{config.PORT}/ping \\')
    print('    -H "Content-Type: application/json" \\')
    print("    -d '{\"message\": \"hello\"}'")
    print()
    # debug=True hands out an interactive debugger, so it's local-only — see config.py
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)


if __name__ == "__main__":
    main()