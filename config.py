"""
Settings for Provenance Guard.

Everything you're likely to change lives here, at the top, on purpose.

You'll edit the thresholds in Milestone 5 and the rate limits in unit 8.
"""

import os
from pathlib import Path

ROOT = Path(__file__).parent


# ─── The service ─────────────────────────────────────────────────────────────

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "5000"))

DEBUG = os.getenv("AI201_DEBUG", "1") == "1" and HOST in ("127.0.0.1", "localhost")


# ─── The local model ─────────────────────────────────────────────────────────

DETECTOR_MODEL = os.getenv("AI201_DETECTOR_MODEL", "gpt2")

# Longer text gives a steadier reading but takes longer. 400 tokens is roughly
# 300 words, which is enough for the signal to mean something.
DETECTOR_MAX_TOKENS = 400


# ─── Scoring (Milestone 4) ───────────────────────────────────────────────────

WEIGHT_MODEL_SIGNAL = 0.5
WEIGHT_STYLE_SIGNAL = 0.5

# Two signals further apart than this count as "disagreeing".
DISAGREEMENT_GAP = 0.35


# ─── Thresholds (Milestone 5) ────────────────────────────────────────────────

HUMAN_THRESHOLD = 0.35
AI_THRESHOLD = 0.65


# ─── Rate limiting (UNIT 8) ──────────────────────────────────────────────────
# ON. Limits are counted per creator_id (falling back to the caller's address
# when there is no usable creator_id) — see app.py::rate_limit_key.
#
# Why these numbers:
#   A real writer submits a piece, reads the label, maybe edits and resubmits.
#   Five in a minute is already a fast human; 50 in a day is far more than
#   anyone submitting their own work needs.
#   A script mapping my thresholds by sending a thousand variations needs
#   volume. At 5/minute and 50/day, one creator_id gets 50 probes a day
#   instead of a thousand, and the per-minute cap stops a burst outright.
#   Cost: a writer who pastes in a dozen drafts in a row gets a 429 and has
#   to wait a minute.
#   Known gap: the limit follows creator_id, so an attacker who rotates
#   creator_ids is not slowed by it. That is the per-creator-vs-per-address
#   trade-off the stretch question asks about.

RATE_LIMITING_ENABLED = True

RATE_LIMIT_PER_MINUTE = 5
RATE_LIMIT_PER_DAY = 50

# In-memory is right for one laptop and wrong for anything real — a restart
# forgets every count, and two copies of the service wouldn't share limits.
RATE_LIMIT_STORAGE = "memory://"


# ─── Paths ───────────────────────────────────────────────────────────────────

LOG_DIR = ROOT / "logs"
AUDIT_LOG = LOG_DIR / "audit.jsonl"
RESULTS_DIR = ROOT / "results"