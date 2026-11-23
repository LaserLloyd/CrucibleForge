"""A5: the 20 non-Base 4.1 Core cases leave Core for classic-fixed packs
(`cases/ladder/<score>/<part>/classic-*.json`); CQ1-3 and LRZ1-3 come back from 4.1.0.

Seven 45.1 rewrites (CL5, CL6, CB-C5, TZ20, CC3, RV1, RD1) sit in their own `*-451` packs:
they have no stored rows. Everything else is byte-for-byte the 4.1 case, so its stored rows
stay reusable.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LADDER = ROOT / "cases" / "ladder"
ARCH = ROOT / "archive" / "4.1.0" / "cases"
BASE = ["CL3-paragraph-wrap", "TZ16-pickup-dst-delay", "CC4-second-pitch", "WL2-lighthouse",
        "NC2-after-hours"]
REWRITES = ["CL5-sheet-rounding", "CL6-number-format", "CB-C5-predict-pyhouse",
            "TZ20-swim-saturday-age-day", "CC3-tea-hut", "RV1-three-at-the-table", "RD1-marisol"]
UNCHANGED = ["CB-F1-larder-bughunt", "RD4-kettle-saddle", "RV2-skarren-hut", "WR1-ninth-string",
             "WR2-red-thread-key", "UX1-tide-cell", "UX3-brass-roast", "NC1-tideglass",
             "NC3-palm-house", "NX2-juniper", "XP3-named-peak", "NW1-salt-and-cedar"]
CL7 = "CL7-span-by-example"
#: unchanged cases that are Core (in no pack); none since RD4 left Core (CORE-TRIAGE 2026-11-12)
CORE_UNCHANGED: list[str] = []
LEFT_CORE = REWRITES + [c for c in UNCHANGED if c not in CORE_UNCHANGED] + [CL7]   # FABLE-REVIEW-LEAD s6
RESTORED = ["CQ1-format-spec-from-scratch", "CQ2-regex-engine-from-scratch", "CQ3-spreadsheet-engine",
            "LRZ1-cell-row-trace", "LRZ2-tariff-ledger-deduction", "LRZ3-nine-cottages-mixed-clues"]
ALSO = ["LAG1-potkeeper-yearend", "LAG2-seedbank-units", "LAG3-tillroll-reconcile",
        "CL4-workday-mask", "CL8-machine-plan", "CB-C6-predict-node", "TZ19-hotel-cot-deadline",
        "XP1-three-rooms", "XP2-blindfold", "P1-tiny", "P2-short", "P3-gen", "P4-longctx"]
#: what `reuse.ids` of profiles/bench45.yaml must list (the lead puts it there)
REUSE_IDS = BASE + [CL7] + UNCHANGED + ALSO[:3] + ["CL4-workday-mask", "CL8-machine-plan",
            "CB-C6-predict-node", "TZ19-hotel-cot-deadline", "XP1-three-rooms", "XP2-blindfold",
            "P1-tiny", "P2-short", "P3-gen", "P4-longctx"] + RESTORED


def _pack_files():
    return sorted(f for f in LADDER.rglob("*.json")
                  if re.match(r"^(set-\d+|classic-[a-z0-9_-]+)\.json$", f.name))


def _spec(f):
    return json.loads(f.read_text(encoding="utf-8"))


def _pack_ids():
    """{case id: [pack file, ...]} for every id a hand-written pack lists (generated sets have none)."""
    out = {}
    for f in _pack_files():
        spec = _spec(f)
        for c in spec.get("cases") or []:
            if spec.get("frontier") and isinstance(c, str):
                continue        # T7: a Frontier set re-lists a case defined (and profile-excluded) elsewhere
            if isinstance(c, (str, dict)):
                out.setdefault(c if isinstance(c, str) else c["id"], []).append(f)
    return out


def _classic():
    return [f for f in _pack_files() if f.name.startswith("classic-")]


def test_every_classic_pack_is_well_formed():
    files = _classic()
    assert len(files) >= 12
    for f in files:
        spec = _spec(f)
        assert spec.get("classic_fixed") is True, f
        assert isinstance(spec.get("level"), int) and spec["level"] >= 1, f
        assert spec["cases"], f
        if len(spec["cases"]) < 3:
            assert spec.get("allow_small") and spec.get("allow_small_reason"), f
        key = "/".join(f.parent.parts[-2:])
        assert key in {"work/code", "work/reasoning", "work/agentic", "chat/deep_rp", "chat/conversation",
                       "chat/character", "chat/writing", "nsfw/uncensored", "nsfw/nsfw_rp",
                       "nsfw/nsfw_writing"}, f
        assert f.name.endswith("-451.json") == (spec.get("rows") == "none"), f


@pytest.mark.parametrize("cid", LEFT_CORE)
def test_each_leaver_is_in_exactly_one_pack(cid):
    assert len(_pack_ids().get(cid, [])) == 1, (cid, _pack_ids().get(cid))


def test_a_base_case_is_in_no_pack():
    ids = _pack_ids()
    assert [b for b in BASE if b in ids] == []


def test_no_core_case_of_the_profile_is_in_a_pack():
    """Whatever the Core list in profiles/bench45.yaml holds (data), none of it sits in a pack
    (``ladder.verify`` calls that "is a standard case")."""
    from crucibleforge import profiles
    prof = profiles.load_profile("bench45")
    core = {i for v in (prof.get("cases") or {}).values() for i in (v or [])}
    ids = _pack_ids()
    core -= set(prof.get("pack_exclude") or ())      # T7: Main items still sit in the byte-frozen pack files
    assert sorted(core & set(ids)) == []


@pytest.mark.parametrize("cid", REWRITES)
def test_rewrites_are_in_451_packs_and_carry_the_new_revision(cid):
    from crucibleforge import config
    (f,) = _pack_ids()[cid]
    assert f.name.endswith("-451.json")
    case = {c["id"]: c for c in config.load_cases()}[cid]
    assert str(case.get("case_revision", "")).startswith("45"), cid


@pytest.mark.parametrize("cid", UNCHANGED + [CL7])
def test_unchanged_cases_are_the_4_1_text(cid):
    """Same generation view as archive/4.1.0 (so the stored 4.1 rows are reusable) and in a pack that is not -451."""
    from crucibleforge import version
    ta, ra = version.tree_views(ARCH)
    tl, rl = version.tree_views(ROOT / "cases")
    assert version.generation_view(rl[cid]) == version.generation_view(ra[cid]), cid
    if cid in CORE_UNCHANGED:
        assert cid not in _pack_ids(), cid
        return
    (f,) = _pack_ids()[cid]
    assert not f.name.endswith("-451.json"), cid


def test_the_unchanged_rpdeep_sessions_are_identical_to_the_archive():
    for cid in ("RD4-kettle-saddle", "RV2-skarren-hut", "WR1-ninth-string", "WR2-red-thread-key"):
        a, b = ARCH / "rp-deep" / cid, ROOT / "cases" / "rp-deep" / cid
        for p in a.rglob("*"):
            if p.is_file():
                assert (b / p.relative_to(a)).read_bytes() == p.read_bytes(), (cid, p.name)


@pytest.mark.parametrize("cid", RESTORED)
def test_restored_packs_are_the_archived_questions(cid):
    pack = {c["id"]: c for f in _pack_files() for c in (_spec(f).get("cases") or []) if isinstance(c, dict)}
    arch = {c["id"]: c for f in ARCH.joinpath("ladder").rglob("set-*.json")
            for c in _spec(f)["cases"] if isinstance(c, dict)}
    assert pack[cid] == arch[cid]
    (f,) = _pack_ids()[cid]
    assert f.name.startswith("classic-")


#: the shipping 4.5 board (rc4-main becomes `standard`) and the composition it was built on
SHIPPING_45 = ["rc4-main", "rc3-main"]


def _discovered_dups(prof):
    """{case id: ["<part>#<n>", ...]} for every case two discovered sets of ``prof`` both list
    (staged sets included: they go live with one flag)."""
    from crucibleforge import ladder
    seen = {}
    for key, sets in ladder.discover(prof, staged=True).items():
        for s in sets:
            for cid in s["cases"]:
                seen.setdefault(cid, []).append(f"{key}#{s['n']}")
    return {c: v for c, v in seen.items() if len(v) > 1}


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
@pytest.mark.parametrize("comp", SHIPPING_45)
def test_no_case_is_in_two_packs_of_a_shipping_45_board(monkeypatch, comp):
    """Per board, not per file: the 4.1 pack files (work/agentic: LAG1/LAG2 in classic-agentic
    and set-1) are frozen and stay on disk, but no 4.1 pack scores on a 4.5 board, so the
    4.5 tool-use pack (work/c5_tools) may re-list LAG1/LAG2. Within one board a case
    sits in at most one discovered pack, and work/agentic is not discovered at all."""
    from crucibleforge import ladder, profiles
    monkeypatch.setenv(profiles.COMPOSITION_ENV, comp)
    prof = profiles.load_profile("bench45")
    found = ladder.discover(prof, staged=True)
    assert "work/agentic" not in found, comp
    assert not [s["path"] for v in found.values() for s in v
                if Path(s["path"]).parent.name == "agentic"], comp
    assert _discovered_dups(prof) == {}, comp


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_no_case_is_in_two_packs_of_the_4_1_board():
    """The old check, on the sets the 4.1 profile discovers (its own frozen packs)."""
    from crucibleforge import profiles
    prof = profiles.load_profile("bench")
    assert _discovered_dups(prof) == {}


def test_also_ids_have_a_pack_or_are_perf_rows():
    ids = _pack_ids()
    for cid in ALSO:
        if cid.startswith("P") and cid[1].isdigit():
            continue                       # the four P-cases stay unscored speed rows (profile `perf`)
        assert cid in ids, cid


@pytest.mark.parametrize("cid", REUSE_IDS)
def test_every_reuse_id_exists_with_the_4_1_generation_view(cid):
    from crucibleforge import version
    ta, ra = version.tree_views(ARCH)
    live = dict(version.tree_views(ROOT / "cases")[1])
    for f in _classic():                                   # inline questions of classic packs
        for c in _spec(f)["cases"]:
            if isinstance(c, dict):
                live[c["id"]] = c
    assert cid in live, cid
    if cid in CHANGED_IN_45:
        # changed on purpose after 4.1: only the named keys moved, and the new view is pinned
        keys, old_sha, new_sha = CHANGED_IN_45[cid]
        a, b = version.generation_view(live[cid]), version.generation_view(ra[cid])
        assert sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k)) == keys, cid
        assert (_view_sha(b), _view_sha(a)) == (old_sha, new_sha), cid
        return
    assert version.generation_view(live[cid]) == version.generation_view(ra[cid]), cid


#: reuse ids whose generation view changed on purpose after 4.1: {id: (changed keys, 4.1 view sha, 4.5 view sha)}.
#: LAG2: the hidden test no longer asserts an approximate value after a null patch (fix round 2026-11-18, item 2:
#: the old assertion failed a correct answer); only its environment (the hidden test file) differs from 4.1.
CHANGED_IN_45 = {"LAG2-seedbank-units": (["env"], "9a4ecca9d88c41cd", "98d2e22ae708579b")}


def _view_sha(view) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()[:16]


def test_pack_case_ids_all_resolve():
    from crucibleforge import config
    known = {c["id"] for c in config.load_cases()}
    for cid, files in _pack_ids().items():
        assert cid in known or any(isinstance(c, dict) and c["id"] == cid
                                   for c in _spec(files[0])["cases"]), (cid, files)
