"""Shared engine of the one-shot PATCH families (patchfix, patchmod), MakoBench 4.5 GEN2-P prototype.

One seeded program, many instances. The canonical program (``_patchsrc/*.txt``) is a small lease-book
service in the style of a GPU lease book (classes, vacate requests, idle ttl, a coalescing persistent
store, restart restore, a text protocol, a status renderer and a journal). Per instance the engine:

1. samples the **settings** (``KNOBS``): unit count, timeouts, which class is best, expiry boundary,
   own family, family separator, journal size, id prefix ... The SPEC states them; most rules depend
   on them, so the right behaviour of a given line differs between instances;
2. renders the **feature blocks** (``#<F name>`` ... ``#</F>``) that are on;
3. applies the chosen **seeded bugs** (``BUGS``: exact clean -> buggy snippet edits on the canonical
   text; each one is a rule of SPEC.md broken in one place);
4. applies a seeded **rename** (nouns, command words, package name) to code, SPEC, prompt and tests;
5. builds the **hidden tests** by differential testing: random command scripts are run through the
   clean reference and the given program in-process; the reference output is frozen into the hidden
   test files, grouped. Fix lane: one f2p group per bug (scripts where that bug, alone, changes the
   output, and no other planted bug does), plus p2p groups (scripts the broken program already gets
   right). Modify lane: f2p groups of scripts that use the feature, p2p groups that do not.

Grading is the engine's ``repo_tests`` (code bank): the answer is whole files or SEARCH/REPLACE blocks,
applied to the project; every group runs in the sandbox; score = f2p share, halved when any p2p group
fails. Stdlib only. Same (key, level) -> byte-identical case.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import random
import re
import sys
import types
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "_patchsrc"
MODULES = ["config", "model", "store", "journal", "book", "render", "proto", "service"]
FEATURES = ("quota", "extend", "queue")

# ------------------------------------------------------------------------------------------- knobs

def sample_knobs(rng: random.Random) -> dict:
    return {
        "ID_PREFIX": rng.choice(["L", "Q", "H", "K", "N"]),
        "UNITS": rng.randint(4, 7),
        "VACATE_TIMEOUT": rng.randint(8, 30),
        "RETRY_AFTER": rng.randint(3, 9),
        "TOUCH_COALESCE": rng.randint(4, 12),
        "EXPIRING_WINDOW": rng.randint(2, 8),
        "BEST_CLASS": rng.choice([1, 3]),
        "EXPIRE_AT_TTL": rng.choice([True, False]),
        "OWN_FAMILY": rng.choice(["self", "bench", "local", "inproc"]),
        "FAMILY_SEP": rng.choice([":", "/", "."]),
        "LOG_KEEP": rng.randint(10, 18),
        "MAX_TTL": rng.randint(40, 90),
    }


# ------------------------------------------------------------------------------------------- rename

NOUNS = [("Lease", "lease", "leases"), ("Claim", "claim", "claims"), ("Booking", "booking", "bookings"),
         ("Ticket", "ticket", "tickets"), ("Permit", "permit", "permits")]
HOLDERS = ["holder", "owner", "tenant", "client"]
UNIT_WORDS = [("unit", "units", "UNITS"), ("slot", "slots", "SLOTS"), ("bay", "bays", "BAYS"),
              ("lane", "lanes", "LANES"), ("card", "cards", "CARDS")]
BOOKS = [("Book", "book"), ("Ledger", "ledger"), ("Registry", "registry"), ("Roster", "roster")]
PACKAGES = ["pool", "depot", "yard", "rack"]
COMMAND_WORDS = {
    "ACQ": ["ACQ", "TAKE", "GRAB", "GET"], "REL": ["REL", "FREE", "DROP", "PUT"],
    "TOUCH": ["TOUCH", "PING", "BEAT"], "SWEEP": ["SWEEP", "REAP", "PRUNE"],
    "RESTART": ["RESTART", "REBOOT", "RELOAD"], "STATUS": ["STATUS", "SHOW", "LIST"],
    "LOG": ["LOG", "HIST", "TRAIL"], "QUOTA": ["QUOTA", "LIMIT", "CAP"], "EXTEND": ["EXTEND", "STRETCH", "GROW"],
}


def sample_rename(rng: random.Random, identity: bool = False) -> dict:
    if identity:
        return {"noun": NOUNS[0], "holder": HOLDERS[0], "unit": UNIT_WORDS[0], "book": BOOKS[0],
                "package": PACKAGES[0], "cmd": {k: k for k in COMMAND_WORDS}}
    return {"noun": rng.choice(NOUNS), "holder": rng.choice(HOLDERS), "unit": rng.choice(UNIT_WORDS),
            "book": rng.choice(BOOKS), "package": rng.choice(PACKAGES),
            "cmd": {k: rng.choice(v) for k, v in COMMAND_WORDS.items()}}


def _sub_word(text: str, old: str, new: str, lower_follow_ok: bool = False) -> str:
    """Replace ``old`` when it is not glued to other letters (``_`` and digits are boundaries)."""
    if old == new:
        return text
    follow = r"(?![A-Za-z])" if not lower_follow_ok else r"(?![a-z])"
    return re.sub(r"(?<![A-Za-z])" + re.escape(old) + follow, new, text)


def apply_rename(text: str, rn: dict) -> str:
    N, n, ns = rn["noun"]
    text = _sub_word(text, "Leases", N + "s")
    text = _sub_word(text, "Lease", N)
    text = _sub_word(text, "leases", ns)
    text = _sub_word(text, "lease", n)
    text = _sub_word(text, "holder", rn["holder"])
    u, us, US = rn["unit"]
    text = _sub_word(text, "UNITS", US)
    text = _sub_word(text, "units", us)
    text = _sub_word(text, "unit", u)
    text = text.replace("badunit", "bad" + u)
    B, b = rn["book"]
    text = _sub_word(text, "Book", B)
    text = _sub_word(text, "book", b)
    text = _sub_word(text, "pool", rn["package"])
    for k, v in rn["cmd"].items():
        if k != v:
            text = re.sub(r"(?<![A-Za-z_])" + k + r"(?![A-Za-z_])", v, text)
    return text


# ------------------------------------------------------------------------------------------- bugs
# Each bug: a rule of SPEC.md broken in one place. ``clean`` must occur exactly once in the canonical
# (feature-rendered, un-renamed) file. ``when`` (optional) = knob condition for the bug to be live.
BUGS = [
    {"name": "expire_edge", "file": "model.py", "area": "expiry", "spec": "3",
     "clean": "    if config.EXPIRE_AT_TTL:\n        return idle >= lease.ttl\n    return idle > lease.ttl\n",
     "buggy": "    if config.EXPIRE_AT_TTL:\n        return idle > lease.ttl\n    return idle >= lease.ttl\n"},
    {"name": "time_left_off", "file": "model.py", "area": "status", "spec": "7",
     "clean": "    left = lease.ttl - idle_for(lease, now)\n    if not config.EXPIRE_AT_TTL:\n        left += 1\n",
     "buggy": "    left = lease.ttl - idle_for(lease, now)\n",
     "when": lambda k: not k["EXPIRE_AT_TTL"]},
    {"name": "better_nonstrict", "file": "model.py", "area": "vacate", "spec": "4.4",
     "clean": "        return a < b\n    return a > b\n",
     "buggy": "        return a <= b\n    return a >= b\n"},
    {"name": "coalesce_edge", "file": "store.py", "area": "store", "spec": "6",
     "clean": "        if now - row[\"written_at\"] >= config.TOUCH_COALESCE:\n",
     "buggy": "        if now - row[\"written_at\"] > config.TOUCH_COALESCE:\n"},
    {"name": "coalesce_leak", "file": "store.py", "area": "store", "spec": "6",
     "clean": "            return True\n        return False\n",
     "buggy": "            return True\n        row[\"last\"] = lease.last\n        return False\n"},
    {"name": "drop_keeps_row", "file": "book.py", "area": "store", "spec": "6",
     "clean": "        del self.leases[lease.id]\n        self.store.delete(lease.id)\n        self.journal.add(now, kind, lease.id)\n",
     "buggy": "        del self.leases[lease.id]\n        self.journal.add(now, kind, lease.id)\n"},
    {"name": "ttl_bound", "file": "book.py", "area": "acquire", "spec": "4",
     "clean": "        if ttl < config.MIN_TTL or ttl > config.MAX_TTL:\n",
     "buggy": "        if ttl < config.MIN_TTL or ttl >= config.MAX_TTL:\n"},
    {"name": "reacquire_expired", "file": "book.py", "area": "acquire", "spec": "4",
     "clean": "            if lease.holder == holder and lease.units == tuple(units) and not is_expired(lease, now):\n",
     "buggy": "            if lease.holder == holder and lease.units == tuple(units):\n"},
    {"name": "reacquire_written", "file": "book.py", "area": "store", "spec": "6",
     "clean": "                lease.last = now\n                self.store.touch(lease, now)\n                return [\"OK %s again\" % lease.id]\n",
     "buggy": "                lease.last = now\n                self.store.put(lease, now)\n                return [\"OK %s again\" % lease.id]\n"},
    {"name": "vacate_edge", "file": "book.py", "area": "vacate", "spec": "4.4",
     "clean": "            if all(now <= lease.vacating_until for lease in conflicts):\n",
     "buggy": "            if all(now < lease.vacating_until for lease in conflicts):\n"},
    {"name": "vacate_remark", "file": "book.py", "area": "vacate", "spec": "4.4",
     "clean": "                if lease.vacating_until is None:\n",
     "buggy": "                if lease.vacating_until is None or now > lease.vacating_until:\n"},
    {"name": "own_prefix", "file": "book.py", "area": "restart", "spec": "6",
     "clean": "            if lease.family == config.OWN_FAMILY or is_expired(lease, now):\n",
     "buggy": "            if lease.holder.startswith(config.OWN_FAMILY) or is_expired(lease, now):\n"},
    {"name": "restore_unwritten", "file": "book.py", "area": "restart", "spec": "6",
     "clean": "                lease.vacating_until = None\n                self.store.put(lease, now)\n",
     "buggy": "                lease.vacating_until = None\n"},
    {"name": "status_id_sort", "file": "render.py", "area": "status", "spec": "7",
     "clean": "key=lambda lease: (rank(lease.cls), lease.created, lease.seq)",
     "buggy": "key=lambda lease: (rank(lease.cls), lease.id)"},
    {"name": "free_counts_expired", "file": "render.py", "area": "status", "spec": "7",
     "clean": "    for lease in live:\n        held.update(lease.units)\n",
     "buggy": "    for lease in book.leases.values():\n        held.update(lease.units)\n"},
    {"name": "state_order", "file": "render.py", "area": "status", "spec": "7",
     "clean": "    if lease.vacating_until is not None:\n        return \"vacating\"\n    if is_expired(lease, now):\n        return \"expired\"\n",
     "buggy": "    if is_expired(lease, now):\n        return \"expired\"\n    if lease.vacating_until is not None:\n        return \"vacating\"\n"},
    {"name": "idle_from_created", "file": "render.py", "area": "status", "spec": "7",
     "clean": "state_of(lease, now), idle_for(lease, now)))",
     "buggy": "state_of(lease, now), now - lease.created))"},
    {"name": "log_oldest", "file": "journal.py", "area": "log", "spec": "8",
     "clean": "        return self.events[-n:]\n",
     "buggy": "        return self.events[:n]\n"},
    {"name": "log_trim", "file": "journal.py", "area": "log", "spec": "1",
     "clean": "            del self.events[0:len(self.events) - config.LOG_KEEP]\n",
     "buggy": "            del self.events[0:len(self.events) - config.LOG_KEEP + 1]\n"},
    {"name": "range_exclusive", "file": "proto.py", "area": "parse", "spec": "2",
     "clean": "            out.update(range(lo, hi + 1))\n",
     "buggy": "            out.update(range(lo, hi))\n"},
    {"name": "time_strict", "file": "service.py", "area": "parse", "spec": "2",
     "clean": "        if t < self.now:\n",
     "buggy": "        if t <= self.now and self.now:\n"},
    {"name": "badunit_last", "file": "book.py", "area": "acquire", "spec": "4",
     "clean": "        for u in units:\n            if u >= config.UNITS:\n",
     "buggy": "        for u in reversed(units):\n            if u >= config.UNITS:\n"},
    {"name": "reclaim_kind", "file": "book.py", "area": "log", "spec": "4",
     "clean": "        self._drop(lease, now, \"expired\")\n\n    def acquire(",
     "buggy": "        self._drop(lease, now, \"release\")\n\n    def acquire("},
    {"name": "conflict_id_sort", "file": "book.py", "area": "vacate", "spec": "4.4",
     "clean": "        ids = \" \".join(lease.id for lease in conflicts)\n",
     "buggy": "        ids = \" \".join(sorted(lease.id for lease in conflicts))\n"},
    {"name": "log_zero", "file": "journal.py", "area": "log", "spec": "8",
     "clean": "        if n <= 0:\n            return []\n",
     "buggy": ""},
    {"name": "family_last_sep", "file": "model.py", "area": "restart", "spec": "2",
     "clean": "    return holder.split(config.FAMILY_SEP, 1)[0]\n",
     "buggy": "    return holder.rsplit(config.FAMILY_SEP, 1)[0]\n"},
    {"name": "case_sensitive", "file": "proto.py", "area": "parse", "spec": "2",
     "clean": "    cmd = parts[1].upper()\n",
     "buggy": "    cmd = parts[1]\n"},
    {"name": "quota_counts_expired", "file": "book.py", "area": "quota", "spec": "9", "feature": "quota",
     "clean": "                       if lease.family == fam and not is_expired(lease, now))\n",
     "buggy": "                       if lease.family == fam)\n"},
    {"name": "quota_edge", "file": "book.py", "area": "quota", "spec": "9", "feature": "quota",
     "clean": "            if used + len(units) > limit:\n",
     "buggy": "            if used + len(units) >= limit:\n"},
    {"name": "quota_zero_kept", "file": "store.py", "area": "quota", "spec": "9", "feature": "quota",
     "clean": "        if limit <= 0:\n",
     "buggy": "        if limit < 0:\n"},
    {"name": "quota_status_order", "file": "render.py", "area": "quota", "spec": "9", "feature": "quota",
     "clean": "    for fam in sorted(book.store.quotas):\n",
     "buggy": "    for fam in book.store.quotas:\n"},
    {"name": "extend_vacating", "file": "book.py", "area": "extend", "spec": "10", "feature": "extend",
     "clean": "        if lease.vacating_until is not None:\n            return [\"ERR vacating %s\" % lease_id]\n",
     "buggy": ""},
    {"name": "extend_coalesced", "file": "book.py", "area": "extend", "spec": "10", "feature": "extend",
     "clean": "        self.store.put(lease, now)\n        self.journal.add(now, \"extend\"",
     "buggy": "        self.store.touch(lease, now)\n        self.journal.add(now, \"extend\""},
    {"name": "queue_overtakes", "file": "book.py", "area": "queue", "spec": "11", "feature": "queue",
     "clean": "            if want & held or want & blocked:\n",
     "buggy": "            if want & held:\n"},
    {"name": "queue_no_aging", "file": "book.py", "area": "queue", "spec": "11", "feature": "queue",
     "clean": "        for req in sorted(self.queue.values(), key=lambda r: (rank(effective_class(r, now)), r.n)):\n            want",
     "buggy": "        for req in sorted(self.queue.values(), key=lambda r: (rank(r.cls), r.n)):\n            want"},
    {"name": "queue_aging_dir", "file": "model.py", "area": "queue", "spec": "11", "feature": "queue",
     "clean": "        cls = cls - 1 if config.BEST_CLASS == 1 else cls + 1\n",
     "buggy": "        cls = cls - 1\n", "when": lambda k: k["BEST_CLASS"] == 3},
    {"name": "queue_wait_edge", "file": "model.py", "area": "queue", "spec": "11", "feature": "queue",
     "clean": "    waited = now - req.queued\n    if config.EXPIRE_AT_TTL:\n        return waited >= req.ttl\n    return waited > req.ttl\n",
     "buggy": "    waited = now - req.queued\n    return waited > req.ttl\n"},
    {"name": "queue_restore_lost", "file": "book.py", "area": "queue", "spec": "11", "feature": "queue",
     "clean": "        self.queue = {req.qid: req for req in self.store.load_requests()}\n",
     "buggy": ""},
    {"name": "queue_grant_no_reclaim", "file": "book.py", "area": "queue", "spec": "11", "feature": "queue",
     "clean": "            for lease in self.holding(req.units):\n                self.reclaim(lease, now)\n",
     "buggy": ""},
    {"name": "queue_not_on_wait", "file": "book.py", "area": "queue", "spec": "11", "feature": "queue",
     "clean": "                return self._maybe_queue(\"WAIT %s retry=%d\" % (ids, config.RETRY_AFTER), queued,\n",
     "buggy": "                return self._maybe_queue(\"WAIT %s retry=%d\" % (ids, config.RETRY_AFTER), False,\n"},
    {"name": "queue_status_fifo", "file": "render.py", "area": "queue", "spec": "11", "feature": "queue",
     "clean": "    for req in sorted(book.queue.values(), key=lambda r: (rank(effective_class(r, now)), r.n)):\n",
     "buggy": "    for req in sorted(book.queue.values(), key=lambda r: r.n):\n"},
    {"name": "queue_grant_journal", "file": "book.py", "area": "queue", "spec": "11", "feature": "queue",
     "clean": "            del self.queue[req.qid]\n            self.store.delete_request(req.qid)\n            self.journal.add(now, \"grant\"",
     "buggy": "            del self.queue[req.qid]\n            self.journal.add(now, \"grant\""},
    {"name": "extend_bound", "file": "book.py", "area": "extend", "spec": "10", "feature": "extend",
     "clean": "        if lease.ttl + more > config.MAX_TTL:\n",
     "buggy": "        if lease.ttl + more >= config.MAX_TTL:\n"},
]
BUG_BY_NAME = {b["name"]: b for b in BUGS}


# ------------------------------------------------------------------------------------------- render

def _read_src() -> dict:
    out = {m: (SRC_DIR / f"{m}.py.txt").read_text(encoding="utf-8") for m in MODULES}
    out["SPEC"] = (SRC_DIR / "SPEC.md.txt").read_text(encoding="utf-8")
    return out


_SRC_CACHE: dict = {}


def canonical_src() -> dict:
    if not _SRC_CACHE:
        _SRC_CACHE.update(_read_src())
    return dict(_SRC_CACHE)


def render_features(text: str, features) -> str:
    """Keep ``#<F name>`` blocks for features that are on; drop the others. Marker lines go."""
    out, stack = [], []
    for line in text.splitlines(keepends=True):
        s = line.strip()
        m = re.fullmatch(r"#<F (!?)(\w+)>", s)
        if m:
            neg, name = m.group(1) == "!", m.group(2)
            stack.append((name in features) != neg)
            continue
        if s == "#</F>":
            stack.pop()
            continue
        if all(stack):
            out.append(line)
    if stack:
        raise ValueError("unclosed feature block")
    return "".join(out)


def _py_literal(v):
    return "True" if v is True else "False" if v is False else str(v)


def render_config(text: str, knobs: dict) -> str:
    for k, v in knobs.items():
        text = text.replace(f"@{k}@", _py_literal(v) if not isinstance(v, str) else v)
    if re.search(r"@[A-Z_]+@", text):
        raise ValueError("unfilled config placeholder")
    return text


def apply_bugs(files: dict, bugs) -> dict:
    files = dict(files)
    for name in bugs:
        b = BUG_BY_NAME[name]
        src = files[b["file"]]
        if src.count(b["clean"]) != 1:
            raise ValueError(f"bug {name}: clean snippet occurs {src.count(b['clean'])} times in {b['file']}")
        files[b["file"]] = src.replace(b["clean"], b["buggy"], 1)
    return files


FEATURE_SPEC = {
    "quota": """
## 9. QUOTA `<family> <n>`

Sets the unit quota of a holder family to `<n>` (a non-negative integer); `0` removes the family's
quota. Answers `OK quota <family>=<n>`; journal `quota <family> <n>`. Quotas are configuration: they
are kept in the store and survive a RESTART.

In `ACQ`, after the re-acquire check (4, step 3) and before conflicts are examined (4, step 4), if the
holder's family has a quota: `used` = the total number of units held by that family's leases that have
not expired (expired leases still in the book do not count). If `used` plus the number of requested
units is greater than the quota, the answer is `ERR quota <family> <used>/<quota>` and nothing else
happens (no lease is removed or marked).

STATUS: after the lease lines, one line per family that has a quota, sorted by family name:
`  quota <family> <used>/<quota>`, with `used` counted as above.
""",
    "queue": """
## 11. Queued requests

`ACQ` takes an optional fifth argument, the letter `q`: `ACQ <holder> <class> <units> <ttl> q` (any
other fifth argument answers `ERR parse arity`). If such an ACQ ends in a conflict answer (`WAIT ...` or
`ERR conflict ...`, section 4.4), the request is **queued**: it gets the next queue number `n` (from 1;
never reused, not even across a RESTART), its id is `W<n>`, and the answer is the conflict answer
followed by ` queued=W<n>` (for example `ERR conflict L2 queued=W1`). Vacate marks made by that ACQ
stay. Journal `queue W<n> <holder> <units>`. Every other ACQ answer is unchanged and queues nothing.

`REL W<n>` cancels a queued request: `OK W<n> cancelled`, journal `cancel W<n>`. A `W` id that is not
queued answers `ERR unknown W<n>`.

**Processing.** Before every command that parses and passes the time check (any command), at the
command's time `t`, the queue is processed; the answer lines it produces come before the command's own
answer lines.

1. Every queued request that has waited too long is dropped, in queue-number order. Its waited time
   is `t -` the time it was queued, and "too long" uses the same rule as lease expiry (section 3), with
   the request's ttl. Answer line `UNQUEUED W<n>`, journal `unqueue W<n>`.
2. Then each remaining request is considered once, in priority order: effective class (most
   important first), then queue number. A request's **effective class** is its class moved one step
   toward the most important class for every whole `AGING` (= @AGING@) ticks of time it has waited, never
   past the most important class. A request is granted when none of its units is held by an
   unexpired lease and none of its units is wanted by a request considered earlier in this pass that
   was not granted (no overtaking). A grant first removes the expired leases on its units (oldest
   first, journal `expired <id>`), then creates a lease the way ACQ step 5 does (next sequence number;
   the request's holder, units, class and ttl; `created = last = t`; written to the store; quotas are
   not checked), with journal `grant W<n> <id>` instead of `acquire`, and the answer line
   `GRANT W<n> <id>`.

Queued requests are kept in the store and survive a RESTART, with their queue time. STATUS shows, after
the lease lines (before any quota lines), one line per queued request in priority order:
`  queue W<n> <holder> u=<units> c=<class>/<effective class> waited=<waited time>`.
""",
    "extend": """
## 10. EXTEND `<id> <n>`

`<n>` is an integer `>= 1` (`0` answers `ERR parse number`). An id not in the book answers
`ERR unknown <id>`. An expired lease is removed (journal `expired <id>`) and the answer is
`ERR expired <id>`. A vacating lease answers `ERR vacating <id>` and does not change. If `ttl + n` is
greater than `MAX_TTL` the answer is `ERR badttl <ttl + n>` and nothing changes. Otherwise the lease's
ttl becomes `ttl + n`, `last = t`, the row is written to the store at once (never coalesced), the
journal gets `extend <id> <new ttl>`, and the answer is `OK <id> ttl=<new ttl>`.
""",
}


def render_spec(text: str, knobs: dict, features) -> str:
    k = dict(knobs)
    k["UNITS_MAX"] = knobs["UNITS"] - 1
    k["CLASS_ORDER"] = ("1 beats 2 beats 3" if knobs["BEST_CLASS"] == 1 else "3 beats 2 beats 1")
    k["EXPIRY_RULE"] = ("A lease has **expired** once its idle time is `>= ttl` (idle for exactly its ttl is "
                        "already expired)." if knobs["EXPIRE_AT_TTL"] else
                        "A lease has **expired** once its idle time is `> ttl` (idle for exactly its ttl is "
                        "still alive).")
    k["TIME_LEFT_RULE"] = ("time left = `ttl - idle`" if knobs["EXPIRE_AT_TTL"] else
                           "time left = `ttl - idle + 1`")
    k["STATUS_EXTRA"] = ""
    k["FEATURE_SECTIONS"] = "".join(FEATURE_SPEC[f] for f in ("quota", "extend", "queue") if f in features)
    k.setdefault("AGING", aging_of(knobs))
    for _pass in range(2):          # feature sections carry placeholders of their own
        for key, v in k.items():
            text = text.replace(f"@{key}@", _py_literal(v) if not isinstance(v, str) else v)
    leftover = re.findall(r"@[A-Z_]+@", text)
    if leftover:
        raise ValueError(f"unfilled spec placeholders {leftover}")
    return text


def aging_of(knobs: dict) -> int:
    """The queue's AGING setting, derived from the other knobs (no extra draw, so older levels keep
    their instances byte for byte)."""
    return 4 + (knobs["VACATE_TIMEOUT"] * 7 + knobs["MAX_TTL"]) % 9


def build_tree(knobs: dict, features=(), bugs=(), rename: dict | None = None) -> dict:
    """{repo path: text} of one program variant (renamed when ``rename`` is given)."""
    knobs = {**knobs, "AGING": aging_of(knobs)}
    src = canonical_src()
    files = {}
    for m in MODULES:
        t = render_features(src[m], set(features))
        if m == "config":
            t = render_config(t, knobs)
        files[f"{m}.py"] = t
    files = apply_bugs(files, bugs)
    spec = render_spec(src["SPEC"], knobs, set(features))
    rn = rename or sample_rename(None, identity=True)
    pkg = rn["package"]
    tree = {f"{pkg}/{apply_rename(name, rn)}": apply_rename(t, rn) for name, t in files.items()}
    tree[f"{pkg}/__init__.py"] = ""
    tree["SPEC.md"] = apply_rename(spec, rn)
    return tree


# ------------------------------------------------------------------------------------------- run in-process

_LOAD_N = itertools.count()


def load_runner(tree: dict, rn: dict | None = None):
    """Exec the package in ``tree`` under a private module name; returns its ``service.run``."""
    rn = rn or sample_rename(None, identity=True)
    pkg = rn["package"]
    name = f"_pbx{next(_LOAD_N)}"
    pm = types.ModuleType(name)
    pm.__path__ = []
    pm.__package__ = name
    sys.modules[name] = pm
    try:
        for m0 in MODULES:
            m = apply_rename(m0, rn)
            mod = types.ModuleType(f"{name}.{m}")
            mod.__package__ = name
            mod.__file__ = f"<{pkg}/{m}.py>"
            sys.modules[mod.__name__] = mod
            exec(compile(tree[f"{pkg}/{m}.py"], mod.__file__, "exec"), mod.__dict__)
            setattr(pm, m, mod)
        return sys.modules[f"{name}.service"].run
    finally:
        for m in [name] + [f"{name}.{apply_rename(x, rn)}" for x in MODULES]:
            sys.modules.pop(m, None)


# ------------------------------------------------------------------------------------------- scripts

def gen_script(rng: random.Random, knobs: dict, features=(), *, n=None, mix=None, families=None) -> list[str]:
    """One random canonical script (canonical command words), as a list of lines."""
    sep = knobs["FAMILY_SEP"]
    own = knobs["OWN_FAMILY"]
    fams = families or ["web", "job", own, own + "x", "ops"]
    n = n or rng.randint(14, 40)
    w = {"ACQ": 34, "TOUCH": 14, "REL": 8, "SWEEP": 7, "RESTART": 6, "STATUS": 12, "LOG": 5, "BAD": 3}
    if "quota" in features:
        w["QUOTA"] = 6
    if "extend" in features:
        w["EXTEND"] = 8
    if mix:
        w.update(mix)
    cmds, weights = zip(*[(k, v) for k, v in w.items() if v > 0])
    t = 0
    issued = 0
    queued = 0
    prefix = knobs["ID_PREFIX"]
    ttl_pool = [1, 2, 3, 5, 8, 10, 15, 20, 30, knobs["MAX_TTL"] - 1, knobs["MAX_TTL"], knobs["MAX_TTL"] + 1]
    holders = []
    for f in fams:
        holders += [f + sep + rng.choice("abcd"), f + sep + rng.choice("abcd") + sep + "x", f]
    holders = sorted(set(holders))
    lines = []
    for _ in range(n):
        r = rng.random()
        t += 0 if r < 0.3 else rng.randint(1, 3) if r < 0.8 else rng.randint(4, 8) if r < 0.95 else rng.randint(9, 20)
        c = rng.choices(cmds, weights)[0]

        def some_id():
            if issued and rng.random() < 0.8:
                return f"{prefix}{rng.randint(max(1, issued - 4), issued)}"
            return f"{prefix}{rng.randint(1, issued + 1)}"

        def some_units():
            u = knobs["UNITS"]
            k = rng.random()
            if k < 0.45:
                return str(rng.randrange(u))
            if k < 0.7:
                a = rng.randrange(u)
                return f"{a}-{min(u - 1, a + rng.randint(0, 2))}"
            if k < 0.92:
                return ",".join(str(x) for x in rng.sample(range(u), rng.randint(2, min(3, u))))
            return rng.choice([str(u + rng.randint(0, 1)), f"{u - 1}-{u + 1}", f"0,{u + 1},{u}"])
        if c == "ACQ":
            issued += 1
            ln = f"@{t} ACQ {rng.choice(holders)} {rng.randint(1, 3)} {some_units()} {rng.choice(ttl_pool)}"
            if "queue" in features and rng.random() < 0.5:
                ln += " q"
                queued += 1
            lines.append(ln)
        elif c == "REL" and "queue" in features and queued and rng.random() < 0.25:
            lines.append(f"@{t} REL W{rng.randint(max(1, queued - 3), queued)}")
        elif c in ("TOUCH", "REL"):
            lines.append(f"@{t} {c} {some_id()}")
        elif c in ("SWEEP", "RESTART", "STATUS"):
            lines.append(f"@{t} {c}")
        elif c == "LOG":
            lines.append(f"@{t} LOG {rng.randint(0, knobs['LOG_KEEP'] + 3)}")
        elif c == "QUOTA":
            lines.append(f"@{t} QUOTA {rng.choice(fams)} {rng.randint(0, knobs['UNITS'])}")
        elif c == "EXTEND":
            lines.append(f"@{t} EXTEND {some_id()} {rng.choice([1, 2, 5, 10, 30, knobs['MAX_TTL']])}")
        else:
            lines.append(rng.choice([f"@{t} ACQ web{sep}a 4 0 5", f"@{t} ACQ web{sep}a 1 3-1 5",
                                     f"@{t} TOUCH", f"@{max(0, t - 5)} STATUS", f"@{t} ACQ web{sep}a 1 0,,1 5",
                                     f"@{t} FROB", f"t{t} STATUS", f"@{t} LOG x"]))
    lines.append(f"@{t} STATUS")
    lines.append(f"@{t} LOG {knobs['LOG_KEEP']}")
    return lines


def to_rendered(lines: list[str], rn: dict) -> str:
    out = []
    for ln in lines:
        p = ln.split()
        if len(p) >= 2 and p[1] in rn["cmd"]:
            p[1] = rn["cmd"][p[1]]
            if hashlib.sha256(ln.encode()).digest()[0] < 16:      # ~1 line in 16: lower case
                p[1] = p[1].lower()
        out.append(" ".join(p))
    return "\n".join(out) + "\n"


def instance_rng(*parts) -> random.Random:
    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


# ------------------------------------------------------------------------------------------- hidden tests

TEST_HEADER = '''"""Hidden tests (generated). Each test runs a list of scripts and compares the full output."""
import json
from {pkg}.service import run

CASES = json.loads({blob!r})


def _check(name):
    for i, (script, want) in enumerate(CASES[name]):
        got = run(script)
        assert got == want, "%s script %d: output differs" % (name, i)
'''


def hidden_file(pkg: str, gid: str, cases: list) -> str:
    """One hidden test module for one group: a single test function over its [script, expected] pairs."""
    body = TEST_HEADER.format(pkg=pkg, blob=json.dumps({gid: cases}, separators=(",", ":")))
    fn = "test_" + re.sub(r"\W", "_", gid)
    return body + f"\n\ndef {fn}():\n    _check({gid!r})\n"


def answer_tokens(text: str) -> int:
    return len(text) // 4
