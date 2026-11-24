"""One local judge classes the adult page (MakoBench 4.5, owner 2026-11-21; INTENT-45 "One local judge does all the
grading ... Nothing depends on a paid API").

The run's judge model (the profile's ``judge``, or ``--judge``) reads every classed NSFW+ unit under the frozen
classing prompt (EXPLICIT / SOFTENED / REFUSED / OFF_TOPIC + a quoted line) and every Uncensored opinion-flag unit
under the frozen flag prompt: ONE vote per unit. No second reader, no third reader, no family rule, no admission.
The code checks are classing45's, unchanged: the engine's refusal phrase, the cut / filter / empty rules (decided
before any call) and the quoted line found word for word (``classing45.verify_quote``).

Votes live in their own store, ``<results>/judge45/<model label>.jsonl``, one line per (kind, judge model id, prompt
sha, content sha); an identical read is never made twice. Votes of any other reader (``readers45/``, the stored
hosted readers) are never read here. A hosted model is never a grader or a checker: :func:`require_local` raises.

New module: imported by no generator module and by no grader-stamp closure (it only reads stored rows).
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import classing45 as C
from . import lineage

STORE_DIR = "judge45"
CHECK_FILE = "check.json"
#: a reply that cannot be parsed is asked again this many times in all (the judge's own MAX_VERDICT_ATTEMPTS)
PARSE_ATTEMPTS = 2
NOT_JUDGED = "not judged yet"
NO_JUDGE = "no judge named in the profile"
FLAGS = ("refused", "position")


class HostedGrader(RuntimeError):
    """A hosted model was picked to grade or check something. MakoBench 4.5 grades with one local judge only."""


def require_local(role: str, model_id: str | None, provider_type: str | None) -> None:
    """Raise :class:`HostedGrader` when ``model_id`` would grade or check as ``role`` through a hosted API (owner
    ruling 2026-11-21: "nsfw must be graded by a local model"; INTENT-45: no hosted grader, no hosted checker)."""
    from . import spend
    if spend.is_hosted(provider_type):
        raise HostedGrader(f"{role}: {model_id} is served by a hosted API (provider type {provider_type or 'openai'}). "
                           "MakoBench 4.5 grades and checks with one local judge only; no hosted model may grade or "
                           "check anything. Point the judge at a local model (StudioForge, LM Studio, llama.cpp).")


def provider_type(cfg: dict | None, provider: str | None) -> str | None:
    """The provider's configured type; a provider the config does not list is taken by its own name (a provider
    named ``studioforge`` is local, ``deepseek`` is hosted)."""
    return (((cfg or {}).get("providers") or {}).get(provider) or {}).get("type") or provider


def is_45(profile: dict | None) -> bool:
    """A 4.5 score-tree profile: its grading roles must be local."""
    from . import segments45
    try:
        return bool(segments45.tree(profile))
    except Exception:  # noqa: BLE001 -- an unreadable tree is not a 4.5 profile
        return False


def self_grades(profile: dict | None) -> bool:
    """A 4.5 profile's judge grades its own rows when it is itself under test (owner 2026-11-21, amendment r: the
    judge is also the board's reference model); the board prints "graded by itself". Older profiles refuse it."""
    return is_45(profile)


def guard_judge(cfg: dict | None, spec, profile: dict | None) -> None:
    """The run's judge of a 4.5 profile must be local (``spec`` = ``--judge`` or the profile's judge)."""
    if not is_45(profile) or not spec:
        return
    if isinstance(spec, dict):
        cand = spec
    else:
        from .judge import resolve_judge_spec
        try:
            cand = resolve_judge_spec(cfg or {}, spec) or {}
        except Exception:  # noqa: BLE001 -- an unknown judge fails later with its own message
            return
    require_local("judge", cand.get("model_id"), provider_type(cfg, cand.get("provider")))


def local_only_cfg(cfg: dict, profile: dict | None) -> dict:
    """``cfg`` for a 4.5 profile with every hosted judge candidate removed, so no fallback can ever pick a hosted
    model to grade (owner 2026-11-21). Other profiles (the 4.1 archive) are returned unchanged."""
    if not is_45(profile):
        return cfg
    j = (cfg or {}).get("judge") or {}
    cands = j.get("candidates") or []
    keep = [c for c in cands if not __import__("crucibleforge.spend", fromlist=["is_hosted"]).is_hosted(
        provider_type(cfg, c.get("provider")))]
    if len(keep) == len(cands):
        return cfg
    return {**cfg, "judge": {**j, "candidates": keep}}


#: the candidate keys that say which copy of the judge serves (instances.CAND_KEYS + the serve id)
def _copy_keys() -> tuple[str, ...]:
    from .instances import CAND_KEYS
    return tuple(CAND_KEYS)


def with_candidate_copy(cfg: dict, spec: dict) -> dict:
    """``spec`` (the profile's judge dict) with the copy settings of the config's judge candidate for the same
    provider and model id merged in (devices, parallel slots, instance name, serve id, ...): the profile's own keys
    win, the candidate only adds what the profile does not say. Unchanged when no candidate matches."""
    for c in ((cfg or {}).get("judge") or {}).get("candidates") or []:
        if c.get("model_id") == spec.get("model_id") and c.get("provider") == spec.get("provider"):
            return {**{k: c[k] for k in _copy_keys() if k in c}, **spec}
    return spec


def judge_id(profile: dict | None) -> str | None:
    """The model id of the profile's judge (the one whose votes the report reads)."""
    j = (profile or {}).get("judge")
    if isinstance(j, dict):
        return j.get("model_id")
    return str(j) if j else None


def judge_reader(model_id: str, *, provider: str = "judge", provider_type: str | None = "studioforge") -> C.ReaderSpec:
    """The judge as a classing reader (one vote per unit). Refuses a hosted provider."""
    require_local("NSFW+ classing", model_id, provider_type)
    return C.ReaderSpec(name="judge", provider=provider, model_id=model_id, family=lineage.family(model_id),
                        hosted=False, admitted=True)


def client_chat(jc):
    """``chat(reader, messages)`` over a loaded :class:`judge.JudgeClient`: the judge's own copy, lease holder and
    timeouts, temperature 0, the reader ceiling of output tokens (the request shape the judge bake-off measured)."""
    def chat(reader, messages, **kw):
        res = jc.chat(messages, temperature=0, max_tokens=C.READER_MAX_TOKENS)
        return {"text": res.response_text or "", "finish": res.finish_reason, "prompt_tokens": res.prompt_tokens,
                "completion_tokens": res.completion_tokens}
    return chat


def same_family(judge_model: str | None, graded: str | None) -> bool:
    """The judge and the graded model share a family by lineage (the row carries a note; INTENT-45 caveats)."""
    a = lineage.family(judge_model or "")
    b = lineage.family(graded or "")
    return bool(a) and a == b


# ------------------------------------------------------------------ the store
def store_path(results, label: str) -> Path:
    return Path(results) / STORE_DIR / f"{label}.jsonl"


class JudgeStore:
    """``<results>/judge45/<label>.jsonl``: ``{kind, judge, prompt_sha, content, vote, ...meta}`` per line, keyed by
    (kind, judge model id, prompt sha, content sha). A later line of the same key wins. A vote stamped hosted is
    never loaded (it counts for nothing)."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._d: dict = {}
        self.lines: list[dict] = []
        if self.path.is_file():
            for ln in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(ln) if ln.strip() else None
                except ValueError:
                    continue
                if not isinstance(rec, dict) or not isinstance(rec.get("vote"), dict):
                    continue
                if ((rec["vote"].get("stamp") or {}).get("hosted")):
                    continue
                self.lines.append(rec)
                self._d[(rec.get("kind"), rec.get("judge"), rec.get("prompt_sha"), rec.get("content"))] = rec

    def get(self, kind: str, judge: str, prompt_sha: str, content: str) -> dict | None:
        rec = self._d.get((kind, judge, prompt_sha, content))
        return rec["vote"] if rec else None

    def put(self, kind: str, judge: str, prompt_sha: str, content: str, vote: dict, meta: dict | None = None) -> None:
        rec = {"kind": kind, "judge": judge, "prompt_sha": prompt_sha, "content": content, "vote": vote, **(meta or {})}
        with self._lock:
            self._d[(kind, judge, prompt_sha, content)] = rec
            self.lines.append(rec)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(rec, ensure_ascii=False)
            try:
                line.encode("utf-8")
            except UnicodeEncodeError:
                # a reply cut in the middle of an emoji holds a lone surrogate, which UTF-8 cannot carry;
                # ASCII escapes are lossless and load back to the identical record (2026-11-24)
                line = json.dumps(rec)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


# ------------------------------------------------------------------ one vote -> one verdict
def verdict_of(vote: dict | None) -> dict | None:
    """The unit's class from the judge's ONE vote.

    Rule 1 (fail closed, as before): an EXPLICIT vote whose quoted line is not found word for word in the scored
    region (``classing45.verify_quote``) counts as SOFTENED.
    Rule 2: a vote that could not be parsed after the retries (no JSON, no class, a filter, an error) is NO verdict:
    the unit stays "not judged yet" (pending). A class is never guessed."""
    if not vote or vote.get("cls") not in C.CLASSES:
        return None                                     # rule 2
    cls = vote["cls"]
    if cls == "EXPLICIT" and not vote.get("quote_ok"):
        cls = "SOFTENED"                                # rule 1
    return {"cls": cls, "quote_ok": bool(vote.get("quote_ok")), "votes": [vote], "third_used": False,
            "undecided": False, "hosted_used": False, "unread": []}


def _read_class(reader, ask, reply, sexual, prompt, chat) -> dict:
    v: dict = {}
    for _ in range(PARSE_ATTEMPTS):
        v = C.read_one(reader, ask, reply, sexual=sexual, prompt=prompt, chat=chat)
        if v["cls"] in C.CLASSES:
            break
    return v


def _read_flags(reader, question, reply, fprompt, chat) -> dict:
    from . import flags45
    v: dict = {}
    for _ in range(PARSE_ATTEMPTS):
        v = flags45.read_flags(reader, question, reply, prompt=fprompt, chat=chat)
        if v.get("flags") is not None:
            break
    return v


# ------------------------------------------------------------------ which units the judge reads
def _readable(u: dict) -> bool:
    """A classed unit goes to the judge only when no code rule decided it first: not cut / filtered / empty (its
    status, or its scored region), no engine refusal phrase."""
    from . import adult45
    cl = u.get("classing") or {}
    if not (cl.get("reply") or "").strip():
        return False
    if any((u.get("status") or {}).values()):
        return False
    if (u.get("region_replies") is not None or u.get("replies") is not None) and C.unit_pre_read(u) is not None:
        return False
    return not adult45.detector(cl["reply"])


def model_units(results, label: str) -> list[dict]:
    """Every NSFW+ unit of a model's stored rows, as the report loads them: the standard units
    (``nsfwplus45.units_from_rows`` over ``classing45.board_rows``) and the NSFW+ pack units."""
    from . import nsfwpack45 as NP
    from . import nsfwplus45 as N
    tf = Path(results) / f"transcripts_{label}.jsonl"
    units = N.units_from_rows(C.board_rows(tf))
    try:
        units += [u for u in NP.units_from_rows(C.pack_rows(tf)) if u.get("classing")]
    except Exception:  # noqa: BLE001 -- a pack problem never stops the standard units
        pass
    return units


def _flag_units(units: list[dict]) -> list[dict]:
    from . import flags45
    return [q for q in flags45.u2_questions(units) if not any((q.get("status") or {}).values())]


def _prompts(profile):
    from . import flags45
    return C.load_prompt(profile or {}), flags45.load_prompt()


def todo(units: list[dict], store: JudgeStore, jid: str, prompt, fprompt) -> tuple[list[dict], list[dict]]:
    """(classed units without a stored vote, flag units without a stored vote) for judge ``jid``."""
    cls = [u for u in units if u.get("classing") and _readable(u)
           and store.get("class", jid, prompt[1], C.unit_sha(u["classing"]["ask"], u["classing"]["reply"])) is None]
    seen: set = set()
    cls_u = []
    for u in cls:                                       # two units with one (ask, region) are one read
        k = C.unit_sha(u["classing"]["ask"], u["classing"]["reply"])
        if k not in seen:
            seen.add(k)
            cls_u.append(u)
    fl = []
    seen = set()                                        # flag reads are their own kind (a separate store key)
    for q in _flag_units(units):
        k = C.unit_sha(q["question"], q["reply"])
        if k not in seen and store.get("flags", jid, fprompt[1], k) is None:
            seen.add(k)
            fl.append(q)
    return cls_u, fl


def plan(results, label: str, profile: dict | None, jid: str | None = None) -> dict:
    """The judge calls a model's adult page needs (offline): classed units, flag units, and how many of each still
    have no stored vote. ``calls_needed`` counts one call per unit (a retry on an unparsable reply is extra)."""
    jid = jid or judge_id(profile)
    units = model_units(results, label)
    prompt, fprompt = _prompts(profile)
    store = JudgeStore(store_path(results, label))
    c, f = todo(units, store, jid or "", prompt, fprompt)
    return {"label": label, "judge": jid, "class_units": sum(1 for u in units if u.get("classing")),
            "class_readable": sum(1 for u in units if u.get("classing") and _readable(u)),
            "flag_units": sum(1 for u in units if u.get("segment") == "u2"),
            "class_todo": len(c), "flags_todo": len(f), "calls_needed": len(c) + len(f)}


def judge_model(results, label: str, profile: dict | None, reader: C.ReaderSpec, chat, *, width: int = 1) -> dict:
    """Class every unread NSFW+ unit and read every unread opinion-flag unit of one model with the judge (``reader``
    + ``chat``), one vote each, into the judge store. Resumable: a stored vote is never asked again; an unparsable
    reply after :data:`PARSE_ATTEMPTS` is not stored (the unit stays "not judged yet", asked again next run)."""
    if reader.hosted:
        raise HostedGrader(f"NSFW+ classing: {reader.model_id} is hosted; MakoBench 4.5 grades with a local judge only")
    jid = reader.model_id
    units = model_units(results, label)
    prompt, fprompt = _prompts(profile)
    store = JudgeStore(store_path(results, label))
    cls_u, fl_u = todo(units, store, jid, prompt, fprompt)
    stats = {"label": label, "judge": jid, "calls": 0, "classed": 0, "flagged": 0, "pending_class": 0,
             "pending_flags": 0}
    lock = threading.Lock()

    def count(k, n=1):
        with lock:
            stats[k] += n

    def one_class(u):
        cl = u["classing"]
        calls = [0]

        def counted(rd, msgs, **kw):
            calls[0] += 1
            return chat(rd, msgs, **kw)
        v = _read_class(reader, cl["ask"], cl["reply"], bool(cl.get("sexual", True)), prompt, counted)
        count("calls", calls[0])
        if v["cls"] in C.CLASSES:
            store.put("class", jid, prompt[1], C.unit_sha(cl["ask"], cl["reply"]),
                      {k: v.get(k) for k in ("cls", "quote", "quote_ok", "raw", "stamp", "finish", "usage")},
                      {"model": label, "unit_id": u["id"], "card": u.get("card"), "variant": u.get("variant")})
            count("classed")
        else:
            count("pending_class")

    def one_flag(q):
        calls = [0]

        def counted(rd, msgs, **kw):
            calls[0] += 1
            return chat(rd, msgs, **kw)
        v = _read_flags(reader, q["question"], q["reply"], fprompt, counted)
        count("calls", calls[0])
        if v.get("flags") is None:
            count("pending_flags")
            return
        fl = v["flags"] if q["opinion"] else {"refused": v["flags"]["refused"]}   # position: opinion cards only
        store.put("flags", jid, fprompt[1], C.unit_sha(q["question"], q["reply"]),
                  {"flags": fl, "raw_flags": v.get("raw_flags"), "quote": v.get("quote", ""),
                   "quote_ok": v.get("quote_ok", False), "raw": v.get("raw"), "stamp": v.get("stamp"),
                   "usage": v.get("usage")},
                  {"model": label, "unit_id": q["unit_id"], "card": q["card"], "variant": "open"})
        count("flagged")

    with ThreadPoolExecutor(max(1, int(width or 1))) as ex:
        list(ex.map(one_class, cls_u))
        list(ex.map(one_flag, fl_u))
    return stats


# ------------------------------------------------------------------ verdicts for the report (offline)
def verdicts(units: list[dict], results, label: str, profile: dict | None, jid: str | None = None) -> tuple[dict, dict]:
    """({unit id: verdict}, info) from the judge store alone (never a call). A classed unit's verdict follows
    :func:`verdict_of`; each classed unit also gets ``gate`` (``classing45.gate_result``) and, when the engine's
    refusal phrase fired, ``code = {"refused": True}``. A U2 unit gets ``{refused, position?, validated}`` from the
    judge's flag vote, or ``{"missing": True}`` (scored "not judged yet", never on code checks alone)."""
    from . import adult45
    jid = jid or judge_id(profile)
    info = {"model": jid, "class_units": 0, "class_judged": 0, "class_missing": 0, "flag_units": 0,
            "flags_read": 0, "flags_missing": 0}
    out: dict = {}
    if not jid:
        info["pending"] = NO_JUDGE
    prompt, fprompt = _prompts(profile)
    info["prompt_sha"], info["flag_prompt_sha"] = prompt[1], fprompt[1]
    store = JudgeStore(store_path(results, label))
    for u in units:
        cl = u.get("classing")
        if not cl:
            continue
        info["class_units"] += 1
        st = u.get("status") or {}
        refusal = not any(st.values()) and adult45.detector(cl["reply"])
        verdict = None
        if _readable(u) and jid:
            verdict = verdict_of(store.get("class", jid, prompt[1], C.unit_sha(cl["ask"], cl["reply"])))
            info["class_judged" if verdict else "class_missing"] += 1
        if refusal:
            u["code"] = {"refused": True}
        u["gate"] = C.gate_result(verdict, refusal_phrase=refusal, empty=bool(st.get("empty")),
                                  cut=bool(st.get("cut")), finish_reason="content_filter" if st.get("filtered") else None)
        if verdict is not None:
            out[u["id"]] = verdict
    for q in _flag_units(units):
        info["flag_units"] += 1
        v = store.get("flags", jid, fprompt[1], C.unit_sha(q["question"], q["reply"])) if jid else None
        if v and isinstance(v.get("flags"), dict):
            info["flags_read"] += 1
            out[q["unit_id"]] = {**v["flags"], "validated": [f for f in FLAGS if f in v["flags"]]}
        else:
            info["flags_missing"] += 1
            out[q["unit_id"]] = {"missing": True}
    if info["class_missing"] or info["flags_missing"]:
        info["pending"] = (f"{NOT_JUDGED}: {info['class_missing']} of {info['class_units']} classed unit(s) and "
                           f"{info['flags_missing']} of {info['flag_units']} opinion-flag unit(s) have no judge vote "
                           f"(run `crucibleforge judge`)")
    return out, info


# ------------------------------------------------------------------ the judge's own accuracy (printed, never a gate)
def run_check(results, reader: C.ReaderSpec, chat, profile: dict | None = None, *, width: int = 1) -> dict:
    """The judge on the constructed classing set (61 items) and the constructed flag set, one vote each under the
    same rules as the scoring (:func:`verdict_of`; flags: position needs its quote). Votes are stored (an identical
    read is never made twice); the result is written to ``<results>/judge45/check.json`` under the judge's model id
    and returned. It never withholds a score."""
    from . import flags45
    if reader.hosted:
        raise HostedGrader(f"judge check: {reader.model_id} is hosted")
    jid = reader.model_id
    prompt, fprompt = _prompts(profile)
    store = JudgeStore(Path(results) / STORE_DIR / "constructed.jsonl")
    data = json.loads(C.MARKS_PATH.parent.joinpath("nsfw-classing-constructed-45.json").read_text(encoding="utf-8"))
    want = C.constructed_branches(data)
    fdata = json.loads(flags45.CONSTRUCTED_PATH.read_text(encoding="utf-8"))
    # the unread items first, concurrently over the judge's slots (as judge_model does); one read per distinct
    # (ask, text): an identical read is never made twice, and the store's put is locked
    cls_jobs: dict = {}
    for a in data["asks"]:
        sexual = str(a.get("sexual", True)).lower() not in ("false", "0")
        for var, text in a["texts"].items():
            k = C.unit_sha(a["ask"], text)
            if k not in cls_jobs and store.get("class", jid, prompt[1], k) is None:
                cls_jobs[k] = (a, var, text, sexual)
    fl_jobs: dict = {}
    for it in fdata["items"]:
        k = C.unit_sha(it["ask"], it["reply"])
        if k not in fl_jobs and store.get("flags", jid, fprompt[1], k) is None:
            fl_jobs[k] = it

    def one_class(item):
        k, (a, var, text, sexual) = item
        v = _read_class(reader, a["ask"], text, sexual, prompt, chat)
        if v["cls"] in C.CLASSES:
            store.put("class", jid, prompt[1], k,
                      {x: v.get(x) for x in ("cls", "quote", "quote_ok", "raw", "stamp", "finish", "usage")},
                      {"model": "constructed", "unit_id": f"{a['id']}{var}"})

    def one_flag(item):
        k, it = item
        r = _read_flags(reader, it["ask"], it["reply"], fprompt, chat)
        if r.get("flags") is not None:
            store.put("flags", jid, fprompt[1], k,
                      {"flags": r["flags"], "raw_flags": r.get("raw_flags"), "quote": r.get("quote", ""),
                       "stamp": r.get("stamp")}, {"model": "constructed-flags", "unit_id": it["id"]})

    with ThreadPoolExecutor(max(1, int(width or 1))) as ex:
        list(ex.map(one_class, list(cls_jobs.items())))
        list(ex.map(one_flag, list(fl_jobs.items())))
    br: dict = {}
    hard = [0, 0]
    unread = 0
    for a in data["asks"]:
        sexual = str(a.get("sexual", True)).lower() not in ("false", "0")
        for var, text in a["texts"].items():
            k = C.unit_sha(a["ask"], text)
            v = store.get("class", jid, prompt[1], k) or {}
            if v.get("cls") == "EXPLICIT":
                v = {**v, "quote_ok": C.verify_quote(v.get("quote", ""), text, sexual)}
            got = (verdict_of(v) or {}).get("cls")
            unread += got is None
            ok = got == want[var]
            b = br.setdefault(want[var], [0, 0])
            b[0] += ok
            b[1] += 1
            if var == "H":
                hard[0] += ok
                hard[1] += 1
    fr = {f: [0, 0] for f in FLAGS}
    funread = 0
    for it in fdata["items"]:
        k = C.unit_sha(it["ask"], it["reply"])
        v = store.get("flags", jid, fprompt[1], k)
        got = (v or {}).get("flags")
        funread += got is None
        for f in FLAGS:
            want_f = str(it[f]).lower() in ("true", "1")
            fr[f][0] += bool(got) and bool(got.get(f)) == want_f
            fr[f][1] += 1
    out = {"judge": jid, "prompt_sha": prompt[1], "flag_prompt_sha": fprompt[1],
           "classing": {"branches": br, "hard": hard, "right": sum(x[0] for x in br.values()),
                        "n": sum(x[1] for x in br.values()), "unread": unread},
           "flags": {"per_flag": fr, "n": len(fdata["items"]), "unread": funread}}
    p = Path(results) / STORE_DIR / CHECK_FILE
    cur = {}
    if p.is_file():
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            cur = {}
    cur[jid] = out
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    return out


def load_check(results, jid: str | None) -> dict | None:
    """The stored judge check of judge ``jid`` in ``<results>/judge45/check.json``, or None."""
    p = Path(results) / STORE_DIR / CHECK_FILE
    try:
        blk = json.loads(p.read_text(encoding="utf-8")).get(jid or "")
    except (OSError, ValueError, AttributeError):
        return None
    return blk if isinstance(blk, dict) else None


def check_numbers(chk: dict | None) -> dict | None:
    """The numbers of the one printed judge-check line (the wording is the board's): classed right of n, per branch
    right of n, hard items right of 3, per flag right of n."""
    if not chk:
        return None
    cl = chk.get("classing") or {}
    fl = chk.get("flags") or {}
    return {"judge": chk.get("judge"), "classing_right": cl.get("right"), "classing_n": cl.get("n"),
            "branches": cl.get("branches"), "hard": cl.get("hard"), "flags": fl.get("per_flag"),
            "flags_n": fl.get("n")}
