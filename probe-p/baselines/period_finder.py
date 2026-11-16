# generic baseline 1: online period finder (best lag p<=64 by recent agreement), copy x[t-p]
class Predictor:
    def __init__(self):
        self.x = []
        self.sc = [0.0] * 65
    def predict(self):
        n = len(self.x)
        best = max(range(1, 65), key=lambda p: self.sc[p] if n >= p else -1)
        return self.x[n - best] if n >= best else 0
    def update(self, d):
        n = len(self.x)
        for p in range(1, 65):
            if n >= p:
                self.sc[p] = self.sc[p] * 0.98 + (self.x[n - p] == d)
        self.x.append(d)
