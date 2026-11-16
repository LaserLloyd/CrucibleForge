"""D3 / SYSTEM-45 section 2: a bonus pack runs only when the model reaches the unlock share of the MATCHING
Main section. Activation is by score, never by model name, id, vendor or provider."""
import ast
import inspect
import textwrap

from crucibleforge import cli, config, ladder, profiles, report, routing

CFG = config.load_config(config.EXAMPLE_CONFIG_PATH)
#: identity words no activation path may read (as a name, attribute or string key)
IDENTITY = {"model_id", "model_label", "vendor", "provider", "provider_type", "served_model"}
#: every function on the activation path (unlock share, part gates, pack states, the Frontier columns)
ACTIVATION = [report.points_scorecard, report._main_frontier_columns, ladder.unlock_share, ladder.states,
              ladder.points, ladder.due, report.ladder_progress, cli.ladder_todo,
              # 4.5 bank: a routed profile's packs open on the route (routing.py)
              routing.pack_parts, routing.opened, routing._packs, routing.choose, routing.plan,
              routing.route_of]


def _identity_refs(fn):
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    hits = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in IDENTITY:
            hits.add(n.id)
        elif isinstance(n, ast.Attribute) and n.attr in IDENTITY:
            hits.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in IDENTITY:
            hits.add(n.value)
    return hits


def test_no_activation_path_reads_a_model_identity():
    found = {fn.__qualname__: _identity_refs(fn) for fn in ACTIVATION}
    assert not any(found.values()), found


def test_every_pack_unlocks_off_its_own_section_score():
    prof = profiles.load_profile("bench45", CFG)
    assert ladder.unlock_share(prof) == 0.9   # Jake 2026-11-14: "The unlock is .9."
    for sec, spec in prof["scores"].items():
        for key, part in (spec.get("parts") or {}).items():
            if part.get("unlock_part"):
                assert part["unlock_part"] == sec or part["unlock_part"] in spec["parts"], (sec, key)
                assert float(part.get("max") or 0) == 0, (sec, key)    # a pack adds bonus points only


def test_states_depend_only_on_scores():
    seq = [{"n": 1, "level": 1, "points": 1.0}, {"n": 2, "level": 2, "points": 1.0}]
    below = ladder.states(0.79, {}, seq, 0.8, set())
    above = ladder.states(0.80, {}, seq, 0.8, set())
    assert below != above
    assert ladder.states(0.80, {}, seq, 0.8, set()) == above      # same score, same answer, every time


def test_route_opening_depends_only_on_the_route():
    seq = [{"n": 1, "level": 1, "points": 1.0}, {"n": 2, "level": 2, "points": 1.0}]
    assert routing.opened({"unlock": True}) and not routing.opened({"unlock": False}) and not routing.opened(None)
    on = ladder.states(0.0, {}, seq, 0.8, set(), opened=True)
    off = ladder.states(1.0, {}, seq, 0.8, set(), opened=False)
    assert on[1] == "pending" and off == {1: "locked", 2: "locked"}
    assert ladder.states(0.0, {}, seq, 0.8, set(), opened=True) == on
