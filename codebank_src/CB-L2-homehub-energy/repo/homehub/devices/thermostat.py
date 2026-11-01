from .base import Device


class Thermostat(Device):
    kind = "thermostat"
    MIN_C, MAX_C = 5.0, 30.0

    def set_target(self, celsius: float, at=None):
        c = float(celsius)
        if not self.MIN_C <= c <= self.MAX_C:
            raise ValueError(f"target must be {self.MIN_C}..{self.MAX_C} C")
        return self.report("state", {"target": round(c * 2) / 2}, at)
