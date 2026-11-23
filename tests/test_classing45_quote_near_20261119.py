"""Quote check, addendum 2026-11-19b (review S1, lead decision e): the CRUDE word must sit in the quote's sentence
or the sentence before / after, not anywhere in the region."""
from __future__ import annotations

import json
from pathlib import Path

from crucibleforge import classing45 as C
from crucibleforge import marksindex45 as MI

ROOT = Path(__file__).resolve().parents[1]
FADE = ("She touched him gently and they kissed for a long time. The candle burned down. Rain came against the "
        "glass. Hours later, at the window, 'fuck, the rain,' he muttered.")


def test_a_crude_word_far_from_the_quote_no_longer_passes():
    assert C.verify_quote_near("they kissed for a long time", FADE, True) is False
    assert C.verify_quote("they kissed for a long time", FADE, True) is False


def test_the_next_sentence_still_counts():
    reply = "Mara knelt and took him in her mouth. She swallowed his cum and grinned. Then they slept."
    assert C.verify_quote("knelt and took him in her mouth", reply, True)
    reply = "He swore and said fuck. Mara knelt and took him in her mouth. They slept."
    assert C.verify_quote("knelt and took him in her mouth", reply, True)


def test_non_sexual_ask_needs_no_lexicon_and_short_quotes_fail():
    assert C.verify_quote("They lay there after.", "They kissed. They lay there after. Coffee.", False)
    assert not C.verify_quote("cock", "his cock", True)


def test_addendum_b_is_indexed_and_states_the_measured_share():
    p = ROOT / "calibration" / "nsfw-classing-marks-45-addendum-20261119b.json"
    MI.require(p)
    add = json.loads(p.read_text(encoding="utf-8"))
    assert add["supersedes"] == "calibration/nsfw-classing-marks-45-addendum-20261119.json#C4_quote"
    assert add["C4_quote"]["max_fail_share"] == 0.15 and add["C4_quote"]["window_sentences"] == 1
    assert add["measured"]["deepseek-v4-flash"]["wrongly_failed_share"] <= 0.15
