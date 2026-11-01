from .base import Device


class Plug(Device):
    """A smart plug with a cumulative energy meter (Wh since the plug's last reset)."""
    kind = "plug"

    def meter(self, wh: int, at=None):
        if wh < 0:
            raise ValueError("meter readings are >= 0")
        return self.report("reading", {"wh": int(wh)}, at, key=f"wh:{int(wh)}")
