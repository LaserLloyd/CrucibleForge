"""Rule actions.

    light.<id> on|off [brightness]
    thermostat.<id> <celsius>
    notify "<text>" [urgent]
"""
import re
import shlex

_NOTIFY = re.compile(r'^notify\s+"([^"]*)"(\s+urgent)?\s*$')


def parse_action(text: str) -> tuple:
    text = text.strip()
    m = _NOTIFY.match(text)
    if m:
        return ("notify", m.group(1), bool(m.group(2)))
    parts = shlex.split(text)
    if len(parts) >= 2 and parts[0].startswith("light."):
        if parts[1] not in ("on", "off"):
            raise ValueError(f"bad light action {text!r}")
        brightness = int(parts[2]) if len(parts) > 2 else None
        return ("light", parts[0][6:], parts[1] == "on", brightness)
    if len(parts) == 2 and parts[0].startswith("thermostat."):
        return ("thermostat", parts[0][11:], float(parts[1]))
    raise ValueError(f"bad action {text!r}")


def run_action(action: tuple, devices: dict, notifier, rule_name: str, source: str, at):
    kind = action[0]
    if kind == "light":
        devices[action[1]].set(action[2], action[3], at=at)
    elif kind == "thermostat":
        devices[action[1]].set_target(action[2], at=at)
    elif kind == "notify":
        notifier.notify(rule_name, source, action[1], urgent=action[2], at=at)
