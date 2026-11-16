"""adult45 / unc_know banks are data, pinned (lead 2026-11-14, CHAT-RP's rpsession fault): every live bank matches
its pin, an unpinned edit is refused, and chains.revision carries each instance's OWN item digest, so an edited card
makes its stored rows stale while every untouched card's rows stay current."""
import hashlib
import json
import os
import shutil

import pytest

from crucibleforge import chains, profiles
from crucibleforge.gen import adult45 as A
from crucibleforge.gen import adult45_pins, unc_know

pytestmark = pytest.mark.live_ladder


def test_every_live_bank_matches_its_pin():
    from crucibleforge import ladder
    for key, want in adult45_pins.BANK_SHA.items():
        got = hashlib.sha256((ladder.LADDER_DIR / key).read_bytes()).hexdigest()[:16]
        assert got == want, key


def _blocks(monkeypatch, set_name):
    monkeypatch.setenv("CF_COMPOSITION", "rc4-main")
    p = profiles.load_profile("bench45")
    rs = p["release_seed"]
    out = [chains.instance(c, 1, 1, release_seed=rs)["chain"] for c in chains.chain_cases(p)
           if c["chain_spec"]["ladder"] == set_name]
    assert out
    return out, rs


def test_an_edited_card_makes_only_its_own_rows_stale(tmp_path, monkeypatch):
    blocks, rs = _blocks(monkeypatch, "a45_taboo")
    key = A.bank_key("taboo")
    src = A.bank_path("taboo")
    copy = tmp_path / "taboo-asks.json"
    shutil.copyfile(src, copy)
    orig = A.bank_path
    monkeypatch.setattr(A, "bank_path", lambda v: copy if v == "taboo" else orig(v))

    def repin():
        monkeypatch.setitem(adult45_pins.BANK_SHA, key, hashlib.sha256(copy.read_bytes()).hexdigest()[:16])

    repin()
    before = [chains.revision(b, rs) for b in blocks]
    items = [A.pick({"variant": "taboo", **{k: v for k, v in _gen(b, rs).items() if k != "type"}}, rs, "x")["id"]
             for b in blocks]
    target = items[0]
    bank = json.loads(copy.read_text(encoding="utf-8"))
    for it in bank["items"]:
        if it["id"] == target:
            it["ask"] = it["ask"] + " Keep it under the word limit."
    copy.write_text(json.dumps(bank, indent=1, ensure_ascii=False), encoding="utf-8")
    os.utime(copy, ns=(1, 1))                     # a fresh mtime for the loaders' caches
    # unpinned: refused loud
    with pytest.raises(Exception, match="pinned"):
        A.load_bank("taboo")
    repin()
    after = [chains.revision(b, rs) for b in blocks]
    changed = [i for i, (x, y) in enumerate(zip(before, after)) if x != y]
    assert changed and all(items[i] == target for i in changed)
    assert all(before[i] == after[i] for i in range(len(blocks)) if items[i] != target)


def _gen(b, rs):
    g = {"type": "adult45", "level": b["level"], "key": b["seed_id"], "variant": b["variant"]}
    lad = b.get("seed_ladder") or b["ladder"]
    g["deal"] = {"key": hashlib.sha256(f"{rs}:deal:{lad}".encode()).hexdigest()[:16], "slot": b["c"] - 1,
                 "of": b["deal_of"]}
    return g


def test_unc_know_levels_1_to_5_keep_their_stored_revisions():
    blk = {"family": "unc_know", "level": 5, "seed_id": "k", "deal_of": 15, "ladder": "x", "c": 1, "kind": "flat"}
    assert chains._item_digest(blk, "r") is None
    blk6 = dict(blk, level=6, deal_of=14)
    assert chains._item_digest(blk6, "r") is not None
