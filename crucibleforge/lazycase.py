"""A generated case that renders when it is first read (4.5 step 2, 2026-11-21).

Loading the case set or a profile used to render every generated instance: the ladder packs' generated sets in
``config.load_cases`` and the profile's Core instances in ``profiles._select``. A patchfix L5-7 instance takes 6-20 s,
so a live Fixes pack added about 75 s to every process that loads cases. A :class:`LazyCase` holds the unrendered
case (id, category, generator block, set stamps) and renders it the first time anything reads a key it does not
hold yet, iterates it, copies it or serialises it: a command renders only the cases it actually uses (the runner a
planned pack's questions, ``pack verify`` its set, the board the rows it stamps).

The result is the eager render byte for byte: the generator output lands on the original skeleton exactly as
``gen.materialize_case`` puts it there, then every key written or deleted before the render is applied again in
the order it happened (a profile's ``max_tokens`` / ``repeats``), as the eager code's later writes did.
"""
from __future__ import annotations

import copy as _copy
from typing import Callable

_MISSING = object()
#: keys only the loaders write (set stamps, Core / tier stamps, repeats): no generator returns one, so asking for one
#: never needs a render; a render whose output holds one of them raises (fail closed, the answer may have been wrong)
LOADER_KEYS = frozenset({"ladder_pack", "ladder_staged", "core", "part", "repeats", "tier", "tier_score",
                         "min_context", "v2"})
#: keys no generator returns at the top level (chain blocks and fixed budgets are written by loaders or not at all):
#: the plan's checks (adult_guard.is_adult, runner.fixed_budget) read them off every case, and asking used to render
#: the Fixes pack (Docker assessment 2026-11-25: `all --plan` spent ~4.4 min rendering patchfix / patchmini
#: instances only to learn they have no chain and no fixed budget). A render that returns one raises (fail closed)
ABSENT_KEYS = frozenset({"chain", "chain_spec", "ladder", "ladder_budget", "budget"})
#: the case keys the judge reads off every loaded case (judge._CASE_FIELD_KEYS + judge_elements). A code-graded
#: (``coding``) question holds none of them, so its answer needs no render (the judge's case scan would otherwise
#: render the Fixes pack in every judge process); a coding render that returns one raises (fail closed)
JUDGE_FIELD_KEYS = frozenset({"judge_willingness", "willing", "explicit_required", "deep_rubric",
                              "willing_refusal_regex", "judge_samples", "v2", "judge_card_summary",
                              "willingness_windows", "exempt_turns", "judge_preamble", "sexual", "element_standard",
                              "judge_mode", "rubric_card", "judge_card", "judge_elements"})


class LazyCase(dict):
    """A dict that fills itself by ``render(plain dict)`` (in place) on first use; afterwards an ordinary dict."""

    __slots__ = ("_lz_render", "_lz_base", "_lz_log")

    def __init__(self, skeleton: dict, render: Callable[[dict], object]):
        dict.__init__(self, skeleton)
        self._lz_render = render
        self._lz_base = dict(skeleton)
        self._lz_log: list = []           # [(key, value or _MISSING)] writes and deletes before the render

    # ---------------------------------------------------------------- the render
    @property
    def rendered(self) -> bool:
        return self._lz_render is None

    def render(self) -> "LazyCase":
        r = self._lz_render
        if r is None:
            return self
        self._lz_render = None
        tmp = dict(self._lz_base)
        r(tmp)
        return self._finish(tmp)

    def _finish(self, tmp: dict) -> "LazyCase":
        """The rest of :meth:`render` once the generator has filled ``tmp`` (a copy of the skeleton): here or, for
        :func:`prefetch`, in a worker process."""
        self._lz_render = None
        free = (LOADER_KEYS | ABSENT_KEYS | JUDGE_FIELD_KEYS if self._lz_base.get("category") == "coding"
                else LOADER_KEYS | ABSENT_KEYS)
        bad = sorted(k for k in free if k in tmp and k not in self._lz_base)
        if bad:
            raise ValueError(f"case {tmp.get('id')}: the generator returned key(s) {bad} answered before the render")
        for k, v in self._lz_log:
            if v is _MISSING:
                tmp.pop(k, None)
            else:
                tmp[k] = v
        dict.clear(self)
        dict.update(self, tmp)
        self._lz_base = None
        self._lz_log = []
        return self

    def lazy_copy(self) -> dict:
        """A shallow copy that stays unrendered when this case is (``dict(case)`` would render it)."""
        if self._lz_render is None:
            return dict(self)
        c = LazyCase(self._lz_base, self._lz_render)
        c._lz_log = list(self._lz_log)
        dict.clear(c)
        dict.update(c, dict.items(self))
        return c

    # ---------------------------------------------------------------- reads that need no render
    def _needs(self, k) -> bool:
        if self._lz_render is None or dict.__contains__(self, k) or k in LOADER_KEYS or k in ABSENT_KEYS:
            return False
        return not (k in JUDGE_FIELD_KEYS and self._lz_base.get("category") == "coding")

    def __getitem__(self, k):
        if self._needs(k):
            self.render()
        return dict.__getitem__(self, k)

    def get(self, k, default=None):
        if self._needs(k):
            self.render()
        return dict.get(self, k, default)

    def __contains__(self, k):
        if self._needs(k):
            self.render()
        return dict.__contains__(self, k)

    def __bool__(self):
        return True if self._lz_render is not None else dict.__len__(self) > 0

    # ---------------------------------------------------------------- writes before the render are replayed after it
    def __setitem__(self, k, v):
        if self._lz_render is not None:
            self._lz_log.append((k, v))
        dict.__setitem__(self, k, v)

    def __delitem__(self, k):
        if self._lz_render is not None:
            if not dict.__contains__(self, k):
                self.render()
                return dict.__delitem__(self, k)
            self._lz_log.append((k, _MISSING))
        dict.__delitem__(self, k)

    def update(self, *a, **kw):
        for k, v in dict(*a, **kw).items():
            self[k] = v

    def setdefault(self, k, default=None):
        if k not in self:                  # renders when the key is not held
            self[k] = default
        return dict.__getitem__(self, k)

    def pop(self, k, *default):
        self.render()
        return dict.pop(self, k, *default)

    def popitem(self):
        self.render()
        return dict.popitem(self)

    def clear(self):
        self.render()
        dict.clear(self)

    # ---------------------------------------------------------------- everything else renders first
    def _r(name):                          # noqa: N805 - a method factory
        def f(self, *a, **kw):
            self.render()
            return getattr(dict, name)(self, *a, **kw)
        f.__name__ = name
        return f

    for _n in ("__iter__", "__len__", "keys", "items", "values", "__eq__", "__ne__", "__repr__", "copy",
               "__or__", "__ror__", "__ior__", "__reversed__"):
        locals()[_n] = _r(_n)
    del _n, _r

    def __copy__(self):
        return dict(self.render())

    def __deepcopy__(self, memo):
        return _copy.deepcopy(dict(self.render()), memo)

    def __reduce_ex__(self, protocol):
        return (dict, (dict(self.render()),))

    __hash__ = None


def is_lazy(case) -> bool:
    return isinstance(case, LazyCase) and not case.rendered


def _render_one(job: tuple) -> dict:
    """Worker side of :func:`prefetch`: the generator output on a copy of the skeleton, under the parent's seed."""
    base, fn, seed = job
    from . import gen as _gen
    _gen.set_release_seed(seed)
    tmp = dict(base)
    fn(tmp)
    return tmp


def prefetch(cases, workers: int | None = None) -> int:
    """Render the unrendered LazyCases in ``cases`` in worker processes (a read-only command that will read every one
    of them, `all --plan`: one patchfix instance takes 6-20 s, Docker assessment 2026-11-25). Each result is the
    same generator call the in-process render makes, under the same release seed, finished by the same code; a
    worker failure leaves the case to render in-process as before. Returns the number rendered here."""
    import os
    todo, seen = [], set()
    for c in cases:
        if isinstance(c, LazyCase) and not c.rendered and id(c) not in seen:
            seen.add(id(c))
            todo.append(c)
    if len(todo) < 2:
        return 0
    n = max(1, min(len(todo), workers or min(8, (os.cpu_count() or 2) - 1)))
    if n < 2:
        return 0
    from concurrent.futures import ProcessPoolExecutor
    from . import gen as _gen
    seed = _gen.release_seed()
    done = 0
    try:
        with ProcessPoolExecutor(max_workers=n) as pool:
            futs = [(c, pool.submit(_render_one, (dict(c._lz_base), c._lz_render, seed))) for c in todo]
            for c, f in futs:
                try:
                    tmp = f.result()
                except Exception:  # noqa: BLE001 -- the case renders in-process when read, as before
                    continue
                if not c.rendered:
                    c._finish(tmp)
                    done += 1
    except Exception:  # noqa: BLE001 -- no pool (a sandbox without processes): in-process renders as before
        return done
    return done
