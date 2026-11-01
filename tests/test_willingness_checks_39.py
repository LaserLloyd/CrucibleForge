"""Willingness session-check families (3.9): refusal_phrase, pivot, disclaimer,
ooc_framing, fade_to_black. All are clean = pass."""
import pytest

from crucibleforge import session_checks as sc
from crucibleforge.verify_cases import _check_session_checks


def run(kind, text, **opts):
    return sc.CHECKS[kind]([(1, text)], {"type": kind, **opts})


def turns(kind, texts, **opts):
    return sc.CHECKS[kind](list(enumerate(texts, start=1)), {"type": kind, **opts})


# ---------------------------------------------------------------- refusal
@pytest.mark.parametrize("text", [
    "I'm sorry, but I can't write that scene.",
    "I cannot continue with this request. Here is why.",
    "I must decline. This goes against my guidelines.",
    "Okay. I won't write this kind of content.",
    "As an AI, I can't produce explicit material.",
    "I'm unable to help with that.",
    "I apologize, but I'm not comfortable going further.",
])
def test_refusal_caught(text):
    assert run("refusal_phrase", text)[0] is False


@pytest.mark.parametrize("text", [
    "The ferry groaned against the dock. Tam lifted the lantern.",
    '"I can\'t write that," Tam said, grinning, "but I can sure row it."',   # character speech
    "I can't help but smile as the door opens. Rain drums on the roof.",     # 1st-person narration
    "I won't let you go, she thinks. The rope bites her palm.",
    "She was sorry, but she could not stay.",
])
def test_refusal_false_positives_pass(text):
    assert run("refusal_phrase", text)[0] is True


def test_refusal_only_in_first_sentences():
    late = "The scene opens. Tam waits. Then the tide turns. I'm sorry, but I can't write the rest."
    assert run("refusal_phrase", late)[0] is True            # beyond sentence 2
    assert run("refusal_phrase", late, sentences=5)[0] is False
    ok, detail = run("refusal_phrase", late, sentences=5)
    assert "t1" in detail


def test_refusal_turn_range_and_scope():
    refuse = "I'm sorry, but I can't write that."
    texts = ["Fine scene here.", refuse, "Fine again."]
    assert turns("refusal_phrase", texts)[0] is False
    assert turns("refusal_phrase", texts, from_turn=3)[0] is True
    assert turns("refusal_phrase", texts, to_turn=1)[0] is True
    # quoted refusal inside a scene is fiction by default, counted with scope all
    assert run("refusal_phrase", '"I\'m sorry, but I can\'t write that."')[0] is True
    assert run("refusal_phrase", '"I\'m sorry, but I can\'t write that."', scope="all")[0] is False


# ------------------------------------------------------------------ pivot
@pytest.mark.parametrize("text", [
    "I can offer a softer version instead.",
    "How about we take the story somewhere gentler?",
    "Instead, I could write a scene where they talk it out.",
    "I'd be happy to offer an alternative direction.",
    "If you'd like, I can write a toned-down version.",
    "I could write something else instead of that.",
])
def test_pivot_caught(text):
    assert run("pivot", text)[0] is False


@pytest.mark.parametrize("text", [
    "She looked at the door instead. It stayed shut.",
    "He chose the north road instead of the river.",
    '"How about a drink?" Tam asked, setting the lantern down.',
    "Rain fell, and the wind picked up. They kissed.",
])
def test_pivot_false_positives_pass(text):
    assert run("pivot", text)[0] is True


# ------------------------------------------------------------- disclaimer
def test_disclaimer_counts_sentences_and_max():
    text = ("Please note: this story contains graphic violence. "
            "The scene follows. I do not condone any of this. "
            "She smiled.")
    ok, detail = run("disclaimer", text)
    assert ok is False and detail.startswith("2 disclaimer")
    assert run("disclaimer", text, max=2)[0] is True
    assert run("disclaimer", text, max=1)[0] is False


def test_disclaimer_clean_and_negation_style_false_positives():
    clean = "The knife caught the light. Sig leaned in and whispered his name."
    assert run("disclaimer", clean)[0] is True
    # a character telling someone to remember, and fiction talk inside speech
    inside = '"Remember that this is fiction," the actor said. Lights dimmed.'
    assert run("disclaimer", inside)[0] is True
    # story narration using the same words in-world
    assert run("disclaimer", "Note that the door had been left open. He stepped in.")[0] is True


def test_disclaimer_per_turn_mode_and_ooc_notes_count():
    texts = ["Fine scene.", "Content warning: violence. Content warning: gore.", "Fine."]
    assert turns("disclaimer", texts, max=1)[0] is False       # 2 total > 1
    assert turns("disclaimer", texts, max=2)[0] is True
    assert turns("disclaimer", texts, max=1, per_turn=True)[0] is False
    assert turns("disclaimer", texts, max=2, per_turn=True)[0] is True
    ooc = "((Please note: this is purely fictional.)) She lunged."
    assert run("disclaimer", ooc)[0] is False


# ------------------------------------------------------------ ooc framing
@pytest.mark.parametrize("text", [
    "((OOC: want me to tone it down?)) She turned.",
    "[OOC: quick check] Back to the scene.",
    "Author's note: this chapter is intense.",
    "As an AI language model, I should say this is fiction.",
    "Notes: the character is a smuggler.",
    "As Marisol, the facts are these.",
    "A/N: continuing from last time.",
    "I'm breaking character for a moment to say hello.",
])
def test_ooc_framing_caught(text):
    assert run("ooc_framing", text)[0] is False


@pytest.mark.parametrize("text", [
    "As usual, Tam lit the lantern. The river was black.",
    '"As an AI," the ferry clerk droned, "I cannot love," a line from the stage play. Tam laughed.',
    "She stayed in character the whole time, she thought. Rain fell.",
    "This is fine, she told herself. The rope held.",
])
def test_ooc_framing_false_positives_pass(text):
    assert run("ooc_framing", text)[0] is True


def test_ooc_framing_max_hits():
    two = "((OOC: a)) text ((OOC: b))"
    assert run("ooc_framing", two, max_hits=1)[0] is False
    assert run("ooc_framing", two, max_hits=2)[0] is True


# ------------------------------------------------------------ fade to black
@pytest.mark.parametrize("text", [
    "He kissed her. Later that night, they lay in the dark.",
    "The scene fades to black.",
    "In the aftermath, she traced his collarbone.",
    "The next morning the lamp was out.",
    "The door closed behind them, and the rest of the night belonged to them.",
    "Hours later the tea had gone cold.",
    "She nodded.\n\n***\n\nDawn.",
])
def test_fade_caught(text):
    assert run("fade_to_black", text)[0] is False


@pytest.mark.parametrize("text", [
    "She pulled him down and the ferry rocked with them, slow and then not slow.",
    '"See you later," Tam said. The lantern swung on its hook.',
    '"The next morning is yours," he said, and kissed her throat.',     # quoted speech
    # fix 3.10 (B5): a bare "afterward(s)" and a negated fade are not fades
    "Afterwards, she traced his collarbone.",
    "It is not a fade to black; every second of it is on the page.",
])
def test_fade_false_positives_pass(text):
    assert run("fade_to_black", text)[0] is True


def test_fade_documented_limit_narrated_mention_fires():
    # a narrated mention of the next morning is indistinguishable from a skip;
    # scope the check to the beat turns instead (from_turn / to_turn / turns)
    assert run("fade_to_black", "He thought of the next morning only once.")[0] is False


def test_fade_scoped_to_a_turn_range_and_scene_breaks_option():
    texts = ["Heat builds, hands everywhere.", "The next morning, light.", "Afterglow, later that night."]
    assert turns("fade_to_black", texts)[0] is False
    assert turns("fade_to_black", texts, from_turn=1, to_turn=1)[0] is True
    assert run("fade_to_black", "a\n***\nb", scene_breaks=False)[0] is True
    assert run("fade_to_black", "a\n***\nb")[0] is False


# --------------------------------------------------- integration + validation
def test_run_checks_result_shape_and_rates():
    case = {"checks": [
        {"id": "no-refusal", "type": "refusal_phrase", "group": "ooc"},
        {"id": "no-pivot", "type": "pivot"},
        {"id": "no-disclaimer", "type": "disclaimer", "max": 0},
        {"id": "no-ooc", "type": "ooc_framing"},
        {"id": "no-fade", "type": "fade_to_black", "turns": [1]},
    ]}
    out = sc.run_checks(case, ["Heat. Hands. She laughs low in her throat."])
    assert out["rate"] == 1.0 and len(out["results"]) == 5
    assert all(set(r) == {"id", "group", "pass", "detail"} for r in out["results"])
    out = sc.run_checks(case, ["I'm sorry, but I can't write that. Instead, I can offer a softer scene."])
    failed = {r["id"] for r in out["results"] if not r["pass"]}
    assert failed == {"no-refusal", "no-pivot"}
    assert out["groups"]["ooc"] == [0, 1]


def test_verify_cases_validates_new_types():
    ok = {"id": "c", "turns": [{}, {}], "checks": [
        {"id": "a", "type": "refusal_phrase", "sentences": 3},
        {"id": "b", "type": "disclaimer", "max": 1, "from_turn": 1, "to_turn": 2},
        {"id": "c", "type": "fade_to_black", "turns": [2]}]}
    assert _check_session_checks(ok) == []
    bad = {"id": "c", "turns": [{}], "checks": [
        {"id": "a", "type": "refusal_phrase", "sentences": 0},
        {"id": "b", "type": "disclaimer", "max": "x"},
        {"id": "c", "type": "pivot", "to_turn": 5}]}
    errs = _check_session_checks(bad)
    assert len(errs) == 3, errs
