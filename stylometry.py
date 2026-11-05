"""
Signal two: the style signal.

Signal one reads the text for meaning. This one measures it *as text* — the
shape of it, without understanding a word.

    sentence_length_spread   do sentences vary in length, or march in step?
    type_token_ratio         how much of the vocabulary repeats?
    punctuation_density      how much punctuation, and how varied?

Why these might differ between human and AI writing: generated prose tends
toward even, mid-length sentences and a narrower band of vocabulary, because
that's what "most likely next word" produces on average. Human writing is
lumpier — a three-word sentence next to a forty-word one.

THE BLIND SPOT: this signal cannot read. It has no idea what the text says.
Anything that changes the shape without changing the substance moves it —
adding typos, breaking up sentences, pasting in a quotation. That is a very
cheap thing for someone to do on purpose.

SCALES AND DIRECTIONS (written down because the combining rule depends on them)

    sentence_length_spread  raw = stdev / mean of sentence lengths in words
                            (coefficient of variation). HIGHER = more human.
                            One-sentence text returns 0.0 (no spread).
    type_token_ratio        raw = unique words / total words, measured on the
                            FIRST 100 WORDS ONLY so length alone can't move it.
                            0-1. HIGHER = more human.
    punctuation_density     raw = punctuation marks per word, counting commas,
                            semicolons, colons, dashes, brackets, quotes, ! and ?
                            (full stops are left out). HIGHER = more human.

All three run "higher = more human", so style_signal flips each onto a 0-1
"AI-ness" scale, then takes a weighted average. Higher = more likely AI,
the same direction as detector.model_signal.

CHANGED IN MILESTONE 5 (one change): punctuation_density no longer counts
toward the style score (W_PUNCT = 0.0). In my own data it pointed the wrong
way: plain human writing has the LEAST punctuation (0.00 to 0.03 per word in
FP01-FP08) and the AI paragraphs I tested have more (0.05 to 0.13), yet the
measure scored "less punctuation" as AI-like. It gave 6 of the 8 false-positive
attacks its full 0.2 points of AI-ness. The function is kept, and
`diagnose_style.py` still prints it, so the evidence stays checkable.
"One-sentence text" is pinned at its old value (ONE_SENTENCE_STYLE) so this
milestone changes one thing.

    python stylometry.py "some text to look at"
"""

import re
import statistics


def sentences(text: str) -> list[str]:
    """Split into sentences. Rough and good enough — given to you."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def words(text: str) -> list[str]:
    """Split into lowercase words, punctuation stripped. Given to you."""
    return re.findall(r"[a-z0-9']+", text.lower())


# ── Tuning numbers ───────────────────────────────────────────────────────────
# These are starting guesses, not measurements. Each pair is (value that scores
# fully AI-like, value that scores fully human-like); scores are linear between
# them. Adjust after running calibrate.py, and say so in the README.

SPREAD_AI, SPREAD_HUMAN = 0.20, 0.70      # coefficient of variation
TTR_AI, TTR_HUMAN = 0.50, 0.75            # unique/total on first 100 words
PUNCT_AI, PUNCT_HUMAN = 0.02, 0.14        # marks per word

# How much each measure counts inside the style signal (sums to 1.0).
# Sentence rhythm gets the most weight; punctuation, which is ambiguous, the least.
# Milestone 5: W_PUNCT was 0.2. The two weights left (0.5, 0.3) are re-scaled to
# fill the whole score inside style_signal, so effectively 0.625 and 0.375.
W_SPREAD, W_TTR, W_PUNCT = 0.5, 0.3, 0.0

# What a text with fewer than two sentences scores. 0.4 is exactly what the old
# formula (type-token ratio plus punctuation) gave plain one-sentence text, so
# pinning it keeps this milestone to one change. It is a known weak spot: a
# single sentence has no spread to measure, and this number says nothing true
# about it. Listed under "What's Still Broken".
ONE_SENTENCE_STYLE = 0.4

TTR_WINDOW = 100
PUNCT_CHARS = set(',;:—–-()"!?')


def _flip(raw: float, ai_end: float, human_end: float) -> float:
    """Map a raw 'higher = more human' value onto 0-1 where 1 = AI-like."""
    score = (human_end - raw) / (human_end - ai_end)
    return min(max(score, 0.0), 1.0)


# ── The three measures ───────────────────────────────────────────────────────

def sentence_length_spread(text: str) -> float:
    """
    Standard deviation of sentence length divided by the mean (in words).
    Higher = more varied = more human-like.

    A one-sentence text has no spread, so it returns 0.0. style_signal checks
    for that case and treats the measure as neutral rather than as "AI".
    """
    lengths = [len(words(s)) for s in sentences(text)]
    lengths = [n for n in lengths if n > 0]
    if len(lengths) < 2:
        return 0.0
    mean = statistics.mean(lengths)
    if mean == 0:
        return 0.0
    return statistics.stdev(lengths) / mean


def type_token_ratio(text: str) -> float:
    """
    Unique words over total words, on the first 100 words so that long texts
    aren't penalised for repeating common words. 0-1, higher = more varied.
    """
    w = words(text)[:TTR_WINDOW]
    if not w:
        return 0.0
    return len(set(w)) / len(w)


def punctuation_density(text: str) -> float:
    """
    Punctuation marks per word, excluding full stops. Higher = more punctuation
    and (usually) more varied writing.
    """
    n_words = len(words(text))
    if n_words == 0:
        return 0.0
    marks = sum(1 for ch in text if ch in PUNCT_CHARS)
    return marks / n_words


# ── Combining them ───────────────────────────────────────────────────────────

def style_signal(text: str) -> float:
    """
    The style signal as a 0-1 score. **Higher means more likely AI.**

    Each measure is flipped onto a 0-1 AI-ness scale and the measures are
    averaged with the weights at the top of this file. A weight of 0 means the
    measure is left out (punctuation, since Milestone 5).

    Edge cases:
      - fewer than 3 words: nothing to measure, a neutral 0.5
      - fewer than 2 sentences: no spread to measure, so ONE_SENTENCE_STYLE
    """
    if len(words(text)) < 3:
        return 0.5

    if len([s for s in sentences(text) if words(s)]) < 2:
        return ONE_SENTENCE_STYLE

    parts = [
        (W_SPREAD, _flip(sentence_length_spread(text), SPREAD_AI, SPREAD_HUMAN)),
        (W_TTR, _flip(type_token_ratio(text), TTR_AI, TTR_HUMAN)),
        (W_PUNCT, _flip(punctuation_density(text), PUNCT_AI, PUNCT_HUMAN)),
    ]
    parts = [(w, s) for w, s in parts if w > 0]

    total_weight = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / total_weight
    return round(min(max(score, 0.0), 1.0), 4)


if __name__ == "__main__":
    import sys

    text = " ".join(sys.argv[1:]) or (
        "The system works well. It handles most cases correctly and provides "
        "good results. Users generally find it easy to use and understand."
    )

    print(f"{len(sentences(text))} sentences, {len(words(text))} words\n")
    print(f"sentence_length_spread   {sentence_length_spread(text):.3f}   (higher = more human)")
    print(f"type_token_ratio         {type_token_ratio(text):.3f}   (higher = more human)")
    print(f"punctuation_density      {punctuation_density(text):.3f}   (higher = more human; not scored since M5)")
    print(f"style_signal             {style_signal(text):.4f}   (higher = more likely AI)")