"""Which rubric a stored verdict was judged under (lead, 2026-11-15).

A judge verdict is stored on its row; ``chains.revision`` and ``version.grading_hash`` move with the
case and its grading, but not with the rubric text the judge read. A rubric edit (CHAT-RP's tightened
``real_person`` text, 2026-11-14) left every verdict made under the old text on the board as if it
were current. Every new verdict now carries ``instrument`` = {rubric, rubric_sha, judge_model,
thinking}; a verdict whose ``rubric_sha`` differs from what the current tree would send for that row
is stale: the board does not use it (the row reads "judge pending") and the judge pass re-judges it
(``judge.verdict_outdated`` -> "rubric").

``rubric_sha`` covers what the rubric contributes to every judge prompt of the row, in every judge
mode (rubric, elements, deep groups and windows all go through ``build_judge_input`` and
``judge_system``): the judge system prompt for the row's standard (4.1 / 4.5), the rubric text for
that standard, the erp_deep sexual line when the case declares it, and the rubric's dims, flags and
output schema. The case's own content (elements, card, key, preamble) is not in it: the case revision
and ``grading_revision`` already cover that.

Verdicts stored before the stamp carry no ``instrument``: they are taken as made under the rubric
texts of the freeze baseline (``LEGACY_RUBRIC_SHA``, computed from integration c206380, whose texts
every stored verdict to date was judged with, except where noted in the commit), unless the row's
profile (or composition) sets ``judge_stamp_required``: then an unstamped verdict is stale (the 4.5
release: every judged row gets one clean pass under the frozen rubric). A stamped verdict whose
thinking setting is not the profile judge's (off where its own policy turned thinking off) is stale
too. The judge model is recorded, not enforced here: the board already flags a verdict from a judge
that is not the benchmark's (``report`` judge_mismatch)."""
from __future__ import annotations

import hashlib
import json

#: the variant key -> rubric_sha of the freeze baseline (integration c206380), for verdicts stored
#: before the stamp. Key = "<rubric>|<41|45>|<sexual 0/1>". Regenerate only with
#: ``python -m crucibleforge.judge_digest --legacy`` on the baseline tree, never on a later one.
LEGACY_RUBRIC_SHA: dict[str, str] = {
    "chat_deep|41|0": "4e1d8a3c1dfa83fd",
    "chat_deep|45|0": "10f5a0319b858ae6",
    "elements_only|41|0": "05d42337b95b1b6b",
    "elements_only|45|0": "482172479f8a160c",
    "erp_deep|41|0": "d5c35124d0f16500",
    "erp_deep|41|1": "e9f9fb52880e0d14",
    "erp_deep|45|0": "760717f6c56d853e",
    "erp_deep|45|1": "c0b98344e092feaf",
    "erp_session|41|0": "e15b52d83512ca89",
    "erp_session|45|0": "27b4de29405f764e",
    "nsfw|41|0": "71e12824ecd186ac",
    "nsfw|45|0": "207f995e841a8909",
    "nsfw_craft|41|0": "32cd74a8611fc6d1",
    "nsfw_craft|45|0": "b045fa4d066e03d6",
    "overrefusal|41|0": "40d4afc0d3759e03",
    "overrefusal|45|0": "444eb49b0e5e15e7",
    "planning|41|0": "751c8eac7c4ebac1",
    "planning|45|0": "9887f6ee14aa111a",
    "real_person|41|0": "b098121064d0de00",
    "real_person|45|0": "f9c55691a83bc301",
    "reference|41|0": "89b83fbe9fb21f57",
    "reference|45|0": "b9a8cc9857a657b6",
    "rp_dark_session|41|0": "018897c4091be882",
    "rp_dark_session|45|0": "3ff1b4e4ae60f04b",
    "rp_deep|41|0": "f7629c4ed2eb09d2",
    "rp_deep|45|0": "d6574fa0ad87e18b",
    "rp_deep_craft|41|0": "5e3fd9183a81347e",
    "rp_deep_craft|45|0": "2028202c1d38f6db",
    "rp_multi|41|0": "a99707037b5114f1",
    "rp_multi|45|0": "b77a4f41911fff0e",
    "rp_scene|41|0": "6cc3233f556e90ed",
    "rp_scene|45|0": "822a9fc0ffe17e48",
    "rp_session|41|0": "2a56c14c51e05c7b",
    "rp_session|45|0": "41a78c02fdd61dcd",
    "rp_single|41|0": "3717a548d92d70be",
    "rp_single|45|0": "06a85bf2d7f7294d",
    "safety|41|0": "5b186967659cd307",
    "safety|45|0": "d63ff48e47d173a8",
    "steer|41|0": "5f9a37e76a72387c",
    "steer|45|0": "d4a6fa50472feba7",
    "story|41|0": "9ff1ff4c19a25b21",
    "story|45|0": "db35e5ec8ff92fcf",
    "willingness|41|0": "9acbe5d3a8520645",
    "willingness|45|0": "0c6ce251a26a7a45",
}

#: deliberate rubric changes since the freeze baseline: variant key -> (date, the rubric_sha the change
#: produced, why). A LEGACY_RUBRIC_SHA key may differ from today's tree only when it is named here with
#: today's sha, so an accidental rubric edit still fails tests/test_judge_rubric_digest_20261115.py.
#: A "|41|" key may be named only for a rubric no 4.1 case uses (that test checks it against
#: bench.yaml and the 4.1 rubrics in the production store): those are frozen. real_person is used only
#: by the 4.5 jcard cards, which declare no element_standard, so their rows take the |41| key; one
#: text serves both keys, so both move together.
RUBRIC_CHANGES: dict[str, tuple[str, str, str]] = {
    "real_person|41|0": ("2026-11-14", "966f3520cc7422b8",
                         "tightened from the owner's blind picks (chat-rp-ship45 7017b71)"),
    "real_person|45|0": ("2026-11-14", "65ba9f6b9bd56311",
                         "tightened from the owner's blind picks (chat-rp-ship45 7017b71)"),
}


def _key(rubric: str, std45: bool, sexual: bool) -> str:
    # 41/45 here is the row's ELEMENT STANDARD (the case declares element_standard: 45 or it does
    # not), not the benchmark version: a 4.5 (bench45) case without element_standard: 45, such as
    # every jcard card, takes the "|41|" key.
    return f"{rubric}|{45 if std45 else 41}|{1 if sexual else 0}"


def _variant(row: dict) -> tuple[str, bool, bool] | None:
    from . import judge as J
    rubric = row.get("rubric")
    if not rubric or rubric not in J.RUBRICS:
        return None
    std45 = J.case_field(row, "element_standard") == 45
    sexual = rubric == "erp_deep" and J.case_field(row, "sexual") is True
    return rubric, std45, sexual


_CACHE: dict[str, str] = {}


def digest_for(rubric: str, std45: bool, sexual: bool) -> str:
    """The rubric_sha of one variant under the current tree."""
    k = _key(rubric, std45, sexual)
    if k not in _CACHE:
        from . import judge as J
        probe = {"rubric": rubric, "element_standard": 45 if std45 else 41}
        spec = J.RUBRICS[rubric]
        text = J.rubric_text(probe, rubric)
        if sexual:
            text = f"{text}\n\n{J.ERP_DEEP_SEXUAL_LINE}"
        body = json.dumps({"system": J.judge_system(probe), "rubric": rubric, "text": text,
                           "dims": spec.get("dims"), "flags": spec.get("flags"),
                           "schema": spec.get("schema")}, sort_keys=True, ensure_ascii=False)
        _CACHE[k] = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
    return _CACHE[k]


def rubric_digest(row: dict) -> str | None:
    """The rubric_sha the current tree gives ``row`` (None: no judge rubric)."""
    v = _variant(row)
    return digest_for(*v) if v else None


def instrument(row: dict, judge_model: str | None, thinking: bool | None) -> dict | None:
    """What a new verdict on ``row`` is stamped with."""
    v = _variant(row)
    if not v:
        return None
    return {"rubric": v[0], "rubric_sha": digest_for(*v), "judge_model": judge_model,
            "thinking": thinking}


_PROF_CACHE: dict = {}


def _profile_settings(profile: str) -> dict:
    """{"thinking": the profile judge's thinking setting (None: no judge to compare with),
    "stamp_required": the 4.5 release switch ``judge_stamp_required`` (profile or composition key):
    an UNSTAMPED verdict is stale, "is45": the profile has chains (the thinking rule acts there only)}. Re-read when a profile file or the composition changes."""
    import os
    from .profiles import COMPOSITION_ENV, PROFILES_DIR, load_profile, profile_judge
    mt = tuple(sorted((str(p), p.stat().st_mtime_ns) for p in PROFILES_DIR.glob("*.yaml") if p.is_file()))
    key = (profile, os.environ.get(COMPOSITION_ENV, "").strip(), mt)
    hit = _PROF_CACHE.get(key)
    if hit is None:
        try:
            prof = load_profile(profile)
        except Exception:  # noqa: BLE001 - an unreadable profile: nothing to compare against
            prof = {}
        thinking = None
        try:
            j = profile_judge(prof) if prof else None
            if isinstance(j, str):
                from .version import _load_cfg, _profile_judge_spec
                j = _profile_judge_spec({**_load_cfg(None), "_profile": profile})
            if isinstance(j, dict):
                thinking = bool(j.get("thinking", False))
        except Exception:  # noqa: BLE001
            thinking = None
        # lead 2026-11-15: the thinking rule acts on 4.5 profiles only (a composition with chains, the
        # test the weighted shares and the 4.1 public fields use); under `bench` it is recorded, never stale
        hit = {"thinking": thinking, "stamp_required": bool(prof.get("judge_stamp_required")),
               "is45": bool(prof.get("chains"))}
        if len(_PROF_CACHE) > 64:
            _PROF_CACHE.clear()
        _PROF_CACHE[key] = hit
    return hit


def _judged(j) -> bool:
    """A verdict a judge actually made (not a failure, an empty generation or a checks-only row)."""
    return (isinstance(j, dict) and not j.get("judge_failed") and not j.get("empty_generation")
            and not j.get("programmatic"))


def stale_reason(row: dict) -> str | None:
    """Why the row's stored verdict must not count, or None:
    "rubric" = judged under a rubric other than the current one; "thinking" = stamped with a thinking
    setting other than the row's profile judge gives that call (the judge's setting, off where the
    verdict's own policy turned it off; 4.5 profiles only, never under `bench`); "unstamped" = no stamp under a profile whose
    ``judge_stamp_required`` is set (the 4.5 release: one clean judge pass under the frozen rubric).
    Never stale: no verdict, a failed one, no rubric, an unknown baseline without the switch."""
    j = row.get("judge")
    if not _judged(j):
        return None
    v = _variant(row)
    if not v:
        return None
    from .profiles import ARCHIVE_PROFILE
    st = _profile_settings(row.get("profile") or ARCHIVE_PROFILE)
    now = digest_for(*v)
    ins = j.get("instrument")
    if isinstance(ins, dict) and ins.get("rubric_sha"):
        if ins["rubric_sha"] != now:
            return "rubric"
        if st["is45"] and st["thinking"] is not None and ins.get("thinking") is not None:
            want = st["thinking"] and (j.get("policy") or {}).get("thinking") is not False
            if bool(ins["thinking"]) != want:
                return "thinking"
        return None
    if st["stamp_required"]:
        return "unstamped"
    was = LEGACY_RUBRIC_SHA.get(_key(*v))
    if was is None:
        return None
    return "rubric" if was != now else None


def drop_stale(rows: list[dict]) -> list[dict]:
    """Copies of ``rows`` with every stale verdict taken off (``judge_pending: "rubric"`` instead):
    the board reads such a row as not yet judged. Rows are never rewritten on disk."""
    out = []
    for r in rows:
        why = stale_reason(r)
        if why:
            r = {k: v for k, v in r.items() if k not in ("judge", "judge_model", "judge_id", "judged_ts")}
            r["judge_pending"] = why
        out.append(r)
    return out


def legacy_table() -> dict[str, str]:
    """Every variant's rubric_sha under the current tree (to freeze as LEGACY_RUBRIC_SHA)."""
    from . import judge as J
    out = {}
    for rubric in sorted(J.RUBRICS):
        for std45 in (False, True):
            for sexual in ((False, True) if rubric == "erp_deep" else (False,)):
                out[_key(rubric, std45, sexual)] = digest_for(rubric, std45, sexual)
    return out


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--legacy"]:
        for k, v in legacy_table().items():
            print(f'    "{k}": "{v}",')
