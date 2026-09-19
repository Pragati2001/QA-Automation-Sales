import random
from difflib import SequenceMatcher

import pytest

from app.services.checks.text_utils import best_window_match, normalize_text, similarity

PHRASE = "this call is recorded for quality and compliance purposes"


# --- normalize_text ----------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("Hello, World!", "hello world"),
    ("  lots   of\n\tspace  ", "lots of space"),
    ("THIS Call IS Recorded.", "this call is recorded"),
    ("Don't stop", "dont stop"),
    ("It’s fine", "its fine"),  # curly apostrophe
    ("well-known/terms&conditions", "well known terms conditions"),
    ("31.9c/kWh", "31 9c kwh"),
    ("under_score", "under score"),
    ("...!?", ""),
    ("", ""),
])
def test_normalize_text(raw, expected):
    assert normalize_text(raw) == expected


# --- similarity --------------------------------------------------------------

def test_similarity_ignores_case_punctuation_and_spacing():
    assert similarity("This call, is RECORDED!", "this  call is recorded") == 1.0


def test_similarity_is_high_for_a_small_wording_change_and_low_for_unrelated_text():
    close = similarity(PHRASE, "this call is being recorded for quality and compliance purposes")
    assert 0.9 < close < 1.0
    assert similarity(PHRASE, "how can i help you with your energy plan today") < 0.6


# --- best_window_match -------------------------------------------------------

def test_exact_phrase_inside_longer_text_is_found_with_exact_word_positions():
    words = normalize_text("good afternoon thanks for calling " + PHRASE + " how can i help").split()
    match = best_window_match(PHRASE, words)

    assert match.similarity == 1.0
    assert words[match.start : match.end] == PHRASE.split()


def test_phrase_is_matched_across_case_and_punctuation():
    assert best_window_match("This call, is RECORDED for quality -- and compliance purposes!", PHRASE.split()).similarity == 1.0


def test_no_resemblance_gives_an_empty_window():
    words = normalize_text("how can i help you with your energy plan today").split()
    match = best_window_match(PHRASE, words)
    assert (match.similarity, match.start, match.end) == (0.0, 0, 0)


@pytest.mark.parametrize("phrase, words", [("", ["a", "b"]), ("   ...  ", ["a"]), (PHRASE, [])])
def test_empty_inputs_give_an_empty_window(phrase, words):
    match = best_window_match(phrase, words)
    assert (match.similarity, match.start, match.end) == (0.0, 0, 0)


def test_when_the_phrase_appears_twice_the_earliest_window_wins():
    words = PHRASE.split() + ["and", "again"] + PHRASE.split()
    match = best_window_match(PHRASE, words)
    assert (match.similarity, match.start) == (1.0, 0)


def test_transcript_shorter_than_the_phrase_does_not_crash():
    match = best_window_match(PHRASE, ["this", "call"])
    assert match.similarity < 0.5


# --- pruning safety ----------------------------------------------------------
# best_window_match prunes windows before scoring them. That must never make a
# match look BETTER than a full search would (no false PASS), and for minor edits it
# must agree with the full search.

PHRASES = [
    PHRASE,
    "please confirm you are the account holder",
    "you have a ten day cooling off period from the date you receive your contract",
]
FILLER = (
    "the a to of and in that it is for you we your can will with about please thanks great sure okay right "
    "call bill month usage plan rate power energy account price today now well just so know good afternoon hello help"
).split()


def exhaustive(phrase, words):
    target = normalize_text(phrase)
    n = len(target.split())
    sizes = {min(len(words), s) for s in range(max(1, n - max(2, n // 4)), n + max(1, n // 5) + 1)}
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(target)
    best = 0.0
    for size in sizes:
        for start in range(len(words) - size + 1):
            matcher.set_seq1(" ".join(words[start : start + size]))
            best = max(best, matcher.ratio())
    return best


def perturb(words, rng, edits):
    words = list(words)
    for _ in range(edits):
        i = rng.randrange(len(words))
        op = rng.choice(["drop", "insert", "substitute", "typo"])
        if op == "drop" and len(words) > 3:
            del words[i]
        elif op == "insert":
            words.insert(i, rng.choice(FILLER))
        elif op == "substitute":
            words[i] = rng.choice(FILLER)
        elif op == "typo" and len(words[i]) > 3:
            j = rng.randrange(len(words[i]))
            words[i] = words[i][:j] + words[i][j + 1 :]
    return words


def test_pruning_never_beats_the_full_search_and_agrees_on_minor_edits():
    rng = random.Random(7)
    for _ in range(40):
        phrase = rng.choice(PHRASES)
        edits = rng.choice([0, 1, 2, 4])
        said = perturb(normalize_text(phrase).split(), rng, edits)
        words = [rng.choice(FILLER) for _ in range(rng.randrange(15))] + said + [rng.choice(FILLER) for _ in range(rng.randrange(15))]

        full = exhaustive(phrase, words)
        pruned = best_window_match(phrase, words).similarity

        assert pruned <= full + 1e-9, f"pruned score above full search for {said!r}"
        if edits <= 1:
            assert pruned >= full - 0.02, f"pruning changed a minor-edit result for {said!r}"
