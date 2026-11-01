"""The rule engine: on each event, re-check every rule watching that kind."""
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from .actions import parse_action, run_action
from .conditions import evaluate, parse_condition


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
        self.fired: list[tuple[str, str]] = []

    def on_event(self, ev):
        self.states.setdefault(ev.source, {}).update(ev.data)
        local = ev.at.astimezone(self.zone).time()
        for rule in self.rules:
            if rule.on != ev.kind:
                continue
            now_true = evaluate(rule._tree, self.states, local)
            was = self._was.get(rule.name, False)
            self._was[rule.name] = now_true
            if not now_true or was:
                continue
            last = self._last_fired.get(rule.name)
            at = ev.at.astimezone(timezone.utc)
            if last is not None and at - last < rule.cooldown:
                continue
            self._last_fired[rule.name] = at
            self.fired.append((rule.name, at.isoformat()))
            for act in rule._acts:
                run_action(act, self.devices, self.notifier, rule.name, ev.source, ev.at)
