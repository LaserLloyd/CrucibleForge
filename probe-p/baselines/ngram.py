# generic baseline 2: variable-order context model (orders 1..12), longest context with data wins
class Predictor:
    def __init__(self):
        self.x = []
        self.c = {}
    def predict(self):
        n = len(self.x)
        for k in range(min(12, n), 0, -1):
            t = self.c.get(tuple(self.x[n - k:]))
            if t and sum(t) >= 2:
                return max(range(3), key=t.__getitem__)
        return 0
    def update(self, d):
        n = len(self.x)
        for k in range(1, min(12, n) + 1):
            self.c.setdefault(tuple(self.x[n - k:]), [0, 0, 0])[d] += 1
        self.x.append(d)
