from .base import Device


class Light(Device):
    kind = "light"

    def set(self, on: bool, brightness: int | None = None, at=None):
        if brightness is not None and not 0 <= brightness <= 100:
            raise ValueError("brightness is 0..100")
        data = {"on": bool(on)}
        if brightness is not None:
            data["brightness"] = brightness
        return self.report("state", data, at)
