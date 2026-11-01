"""Wiring: config -> devices, bus, rules, notifier, history, scheduler."""
from .config import Config
from .devices import registry
from .events import Bus
from .notify import Notifier
from .rules import Engine, Rule
from .scheduler import Scheduler
from .storage import History
from .util.timeparse import parse_duration


class Hub:
    def __init__(self, config: Config, db_path: str = ":memory:"):
        self.config = config
        self.bus = Bus()
        self.history = History(db_path)
        self.notifier = Notifier(config.throttle, config.quiet, config.zone)
        self.devices = {}
        for d in config.devices:
            cls = registry[d["kind"]]
            self.devices[d["id"]] = cls(d["id"], d.get("name", d["id"]), self.bus)
        rules = [Rule(r["name"], r["on"], r["if"], r["then"], parse_duration(r.get("cooldown", "0s")))
                 for r in config.rules]
        self.engine = Engine(rules, self.devices, self.notifier, config.zone)
        self.scheduler = Scheduler(config.zone)
        for name, clock in config.triggers.items():
            self.scheduler.add(name, clock)
        self.bus.subscribe(self.history.record)
        self.bus.subscribe(self.engine.on_event)

    def tick(self, since, until) -> int:
        """Called every minute by the service loop: publish the scheduler's
        triggers due in (since, until], then let the notifier send what is
        due at `until` (the quiet-hours summary). Returns the trigger count."""
        n = self.scheduler.tick(self.bus, since, until)
        self.notifier.flush(until)
        return n
