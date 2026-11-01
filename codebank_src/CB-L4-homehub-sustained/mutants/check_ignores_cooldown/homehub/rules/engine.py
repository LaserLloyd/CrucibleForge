"""The rule engine: on each event, re-check every rule watching that kind."""
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from .actions import parse_action, run_action
from .conditions import evaluate, has_sustained, parse_condition


@dataclass
class Rule:
    name: str
    on: str                     # event kind that wakes the rule: "reading", "state", "trigger"
    condition: str
    actions: list
    cooldown: timedelta = timedelta(0)
    _tree: tuple = field(default=None, repr=False)
    _acts: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        self._tree = parse_condition(self.condition)
        self._acts = [parse_action(a) for a in self.actions]

    @property
    def sustained(self) -> bool:
        return has_sustained(self._tree)


class Engine:
    """Rules fire on the rising edge: when the condition becomes true (it was
    false, or never evaluated, after the previous event), and not again
    within `cooldown` of the last firing."""

    def __init__(self, rules, devices: dict, notifier, zone: str = "Europe/Berlin"):
        self.rules, self.devices, self.notifier = list(rules), devices, notifier
        self.zone = ZoneInfo(zone)
        self.states: dict = {}
        self._was: dict[str, bool] = {}
        self._last_fired: dict = {}
        self._since: dict = {}
        self.fired: list[tuple[str, str]] = []

    def on_event(self, ev):
        self.states.setdefault(ev.source, {}).update(ev.data)
        for rule in self.rules:
            if rule.on != ev.kind:
                continue
            self._consider(rule, ev.at, ev.source)

    def check(self, now) -> None:
        """Evaluate every rule with a `for` comparison at `now` (no new event)."""
        for rule in self.rules:
            if rule.sustained:
                self._consider(rule, now, "engine")

    def _consider(self, rule, when, source):
        local = when.astimezone(self.zone).time()
        at = when.astimezone(timezone.utc)
        memo = self._since.setdefault(rule.name, {})
        now_true = evaluate(rule._tree, self.states, local, at, memo)
        was = self._was.get(rule.name, False)
        self._was[rule.name] = now_true
        if not now_true or was:
            return
        last = self._last_fired.get(rule.name)
        if source != "engine" and last is not None and at - last < rule.cooldown:
            return
        self._last_fired[rule.name] = at
        self.fired.append((rule.name, at.isoformat()))
        for act in rule._acts:
            run_action(act, self.devices, self.notifier, rule.name, source, when)
