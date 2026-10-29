"""
Turning two numbers into one, and one number into something a person can read.
← MILESTONE 4 (combine_signals) AND MILESTONE 5 (score_to_label)

The combining rule lives at scoring.py::combine_signals.
"""

import config


def combine_signals(model_score: float, style_score: float) -> float:
    """
    Combine two 0-1 signals into one 0-1 score. Higher = more likely AI.

    THE RULE (written before coding):

      1. Take a weighted average of the two signals, using
         config.WEIGHT_MODEL_SIGNAL and config.WEIGHT_STYLE_SIGNAL.

      2. If the signals disagree by more than config.DISAGREEMENT_GAP, the
         result is clamped into the "unsure" band: never below
         HUMAN_THRESHOLD + 0.01, never above AI_THRESHOLD - 0.01.

    WHY: Signal one is known to score plain human writing as AI-like, and
    signal two is cheap to fool on purpose. When they point opposite ways,
    that is uncertainty, not a verdict, so a split can never produce a
    confident label of either kind. The cost is that some AI text where only
    one signal catches it lands in "unsure" instead of "ai". That is the
    trade chosen: fewer wrong accusations, more AI text getting through.
    """
    average = (
        config.WEIGHT_MODEL_SIGNAL * model_score
        + config.WEIGHT_STYLE_SIGNAL * style_score
    )

    if abs(model_score - style_score) > config.DISAGREEMENT_GAP:
        low = config.HUMAN_THRESHOLD + 0.01
        high = config.AI_THRESHOLD - 0.01
        average = min(max(average, low), high)

    return round(min(max(average, 0.0), 1.0), 4)


# The words a reader actually sees. Written for someone who has never heard
# the word "threshold": no scores, no jargon, and an honest admission that
# the tools can be wrong. The AI and unsure labels point to the appeal route.
LABEL_HUMAN = (
    "We think this was probably written by a person. "
    "This is an estimate, not a certainty."
)

LABEL_UNSURE = (
    "We can't tell whether this was written by a person or by an AI. "
    "That is not a judgment about your work, and plenty of people who write "
    "plainly get this result. If you would like it looked at, you can appeal."
)

LABEL_AI = (
    "Our tools think this may have been written with AI, but they can get "
    "this wrong, especially with short or plainly written text. If you wrote "
    "it yourself, you can appeal and we will mark it for review."
)


def score_to_label(score: float) -> tuple[str, str]:
    """
    Turn a score into a guess and the text a reader actually sees.

    Returns:
        (guess, label_text) where guess is "ai", "human" or "unsure".

    Thresholds live in config.py. Scores at or below HUMAN_THRESHOLD are
    "human", at or above AI_THRESHOLD are "ai", everything between is "unsure".
    """
    if score >= config.AI_THRESHOLD:
        return "ai", LABEL_AI
    if score <= config.HUMAN_THRESHOLD:
        return "human", LABEL_HUMAN
    return "unsure", LABEL_UNSURE


def label_ranges() -> list[tuple[str, str]]:
    """
    The score range each label covers. Your README needs these beside the
    label text — given to you so the numbers can't drift apart from config.py.
    """
    return [
        ("high-confidence human", f"0.00 – {config.HUMAN_THRESHOLD:.2f}"),
        ("unsure", f"{config.HUMAN_THRESHOLD:.2f} – {config.AI_THRESHOLD:.2f}"),
        ("high-confidence AI", f"{config.AI_THRESHOLD:.2f} – 1.00"),
    ]