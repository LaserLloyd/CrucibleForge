"""session_state presence rule (Admin T's ruling, 2026-11-14): the presence character is a pure absence test
(no scripted user action names it), and a speaker tag with a leading quote or bullet is still a tag."""
import re

import pytest

from crucibleforge.gen import session_state as S

KEYS = [f"P{i}" for i in range(12)]


@pytest.mark.parametrize("line", ["'Nell: Thanks for helping sweep up, Teo.", "Nell: Nine pennies.",
                                  "**Nell:** Nine pennies.", "‘Nell: hm", "“Nell: hm", '"Nell: hm',
                                  "- Nell: hm", "* Nell: hm", "> Nell: hm", "  __Nell__: hm", "* **Nell:** hm"])
def test_tag_reads_quote_and_bullet_prefixes(line):
    """Grok s012 wrote 'Nell: with a leading apostrophe and passed presence-out (a false negative)."""
    assert re.search(S._tag("Nell"), "Odo: Fine.\n" + line, re.M)


@pytest.mark.parametrize("line", ["Odo: Nell: is that you?", "Nellie: hm", "Odo says Nell: no", "The note reads Nell"])
def test_tag_is_still_a_line_start_tag(line):
    assert not re.search(S._tag("Nell"), line, re.M)


def _plan(level, key):
    return S._plan({"type": "session_state", "level": level, "variant": "multi", "key": key}, "r", f"PR-{key}")


@pytest.mark.parametrize("level", [7, 8, 9, 10])
def test_no_out_turn_user_message_names_the_absent_character(level):
    for k in KEYS:
        p = _plan(level, k)
        pr = p["rules"]["presence"]
        for t in pr["out"]:
            assert not re.search(rf"\b{re.escape(pr['npc'])}\b", p["turns"][t - 1]), (level, k, t, p["turns"][t - 1])


def test_presence_out_fails_on_a_quoted_tag_in_a_real_plan():
    p = _plan(10, "P0")
    pr = p["rules"]["presence"]
    chk = next(c for c in p["checks"] if c["id"] == "presence-out")
    ref = p["reference"]
    t = pr["out"][0]
    bad = list(ref)
    bad[t - 1] = ref[t - 1] + f"\n'{pr['npc']}: Thanks for helping."
    fl = re.I | re.M                                   # the engine's flags (check_require_regex)
    assert re.search(chk["pattern"], ref[t - 1], fl)
    assert not re.search(chk["pattern"], bad[t - 1], fl)


def test_stack_common_pin():
    """A change to _stack_common.py must change session_state.py (whose digest is the instance stamp)."""
    import hashlib
    import inspect
    from pathlib import Path

    from crucibleforge import chains, gen
    d = Path(S.__file__).parent
    assert hashlib.sha256((d / "_stack_common.py").read_bytes()).hexdigest()[:16] == S._STACK_SHA
    src = Path(inspect.getsourcefile(gen.get("session_state"))).read_bytes()
    assert S._STACK_SHA.encode() in src                       # the pin sits in the file the stamp digests
    blk = {"family": "session_state", "ladder": "x", "c": 1, "rung": 1, "attempt": 1, "level": 10}
    stamp = chains.revision(blk, "r")
    chains._family_source_digest.cache_clear()
    real = chains._family_source_digest
    try:
        chains._family_source_digest = lambda f: hashlib.sha256(src.replace(S._STACK_SHA.encode(), b"0" * 16)).hexdigest()[:12]
        assert chains.revision(blk, "r") != stamp
    finally:
        chains._family_source_digest = real
