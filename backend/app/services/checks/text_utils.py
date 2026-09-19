import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

_APOSTROPHES = re.compile(r"['‘’`]")
_PUNCTUATION = re.compile(r"[^\w\s]|_")
_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace.

    Apostrophes are removed outright (don't -> dont); other punctuation
    becomes a space so "well-known" doesn't fuse into one word.
    """
    text = _APOSTROPHES.sub("", text.lower())
    text = _PUNCTUATION.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def similarity(a: str, b: str) -> float:
    """Fuzzy similarity of two texts in [0, 1], after normalizing both."""
    return SequenceMatcher(None, normalize_text(a), normalize_text(b), autojunk=False).ratio()


@dataclass(frozen=True)
class WindowMatch:
    similarity: float
    start: int  # word index where the best-matching window starts
    end: int  # exclusive; the window is words[start:end]; start == end means no candidate window


# A window is only worth scoring if it starts and ends on an "anchor" word (one that
# resembles a word of the phrase) and holds at least this share of the phrase's word
# count as anchors. This prunes the search ~10-50x; every window that is scored is
# scored exactly, so pruning can lower the best score but never inflate it.
_ANCHOR_WORD_SIMILARITY = 0.75
_MIN_ANCHOR_SHARE = 0.4


def _anchor_flags(words: Sequence[str], phrase_words: Sequence[str]) -> list[bool]:
    phrase_set = set(phrase_words)
    resembles: dict[str, bool] = {}
    for word in words:
        if word not in resembles:
            resembles[word] = word in phrase_set or (
                len(word) > 2
                and any(
                    (m := SequenceMatcher(None, word, pw, autojunk=False)).real_quick_ratio() >= _ANCHOR_WORD_SIMILARITY
                    and m.ratio() >= _ANCHOR_WORD_SIMILARITY
                    for pw in phrase_set
                )
            )
    return [resembles[w] for w in words]


def best_window_match(phrase: str, words: Sequence[str]) -> WindowMatch:
    """Find the run of consecutive `words` (already normalized) most similar to `phrase`.

    A required phrase is usually a small part of a longer turn, so comparing
    whole segments would score low; instead slide a window about the phrase's
    length (a little shorter/longer, to absorb dropped or inserted words) over
    the words and keep the best. Ties keep the earliest window. Returns an empty
    window (similarity 0) when no stretch of `words` resembles the phrase at all.
    """
    target = normalize_text(phrase)
    phrase_words = target.split()
    n = len(phrase_words)
    if n == 0 or not words:
        return WindowMatch(0.0, 0, 0)

    anchors = _anchor_flags(words, phrase_words)
    prefix = [0]
    for flag in anchors:
        prefix.append(prefix[-1] + flag)
    need = max(1, math.ceil(_MIN_ANCHOR_SHARE * n))

    # Window length may be a little shorter (dropped words) or longer (inserted words).
    shortest = max(1, n - max(2, n // 4))
    longest = n + max(1, n // 5)

    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(target)  # the phrase is fixed; only seq1 changes per window
    best = WindowMatch(0.0, 0, 0)
    positions = [i for i, is_anchor in enumerate(anchors) if is_anchor]
    for first, start in enumerate(positions):
        for last in range(first, len(positions)):
            end = positions[last] + 1  # windows start and end on anchor words
            if end - start > longest:
                break
            if end - start < shortest or prefix[end] - prefix[start] < need:
                continue
            matcher.set_seq1(" ".join(words[start:end]))
            if matcher.real_quick_ratio() <= best.similarity:  # can't beat the best so far
                continue
            ratio = matcher.ratio()
            if ratio > best.similarity:
                best = WindowMatch(ratio, start, end)
    return best
