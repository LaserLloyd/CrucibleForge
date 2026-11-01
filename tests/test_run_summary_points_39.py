"""The run report's one-line result reads the points board (Overall + public
categories), not the pre-3.9 Chat/Coding keys (it printed "Coding -")."""
from crucibleforge import cli


def test_summary_line_names_overall_and_public_categories():
    card = {"total": 17.24, "work": 9.4, "chat": 7.84, "coding": None, "nsfw": 8.3, "priv": 8.2,
            "score_defs": [{"key": "work", "label": "Coding/Work", "public": True},
                           {"key": "chat", "label": "Chat", "public": True},
                           {"key": "nsfw", "label": "NSFW", "public": True},
                           {"key": "priv", "label": "Secret", "public": False}]}
    assert cli._summary_bits(card) == ["Overall 17.2", "Coding/Work 9.4", "Chat 7.8", "NSFW 8.3"]
    card["total"] = None                        # a partial run has no Overall
    assert cli._summary_bits(card)[0] == "Overall -"


def test_pre_points_cards_keep_the_old_line():
    assert cli._summary_bits({"chat": 88.7, "coding": None}) == ["Chat 88.7", "Coding -"]
