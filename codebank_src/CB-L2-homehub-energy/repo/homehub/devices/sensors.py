from .base import Device


class TempSensor(Device):
    kind = "temp"

    def reading(self, celsius: float, at=None):
        return self.report("reading", {"celsius": round(float(celsius), 2)}, at, key=f"c:{round(float(celsius), 1)}")


class ContactSensor(Device):
    """Door/window contact: open=True when the door is open."""
    kind = "contact"

    def contact(self, open_: bool, at=None):
        return self.report("state", {"open": bool(open_)}, at, key=f"open:{bool(open_)}")
