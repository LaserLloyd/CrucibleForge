# patgen reference solver: interleave hypotheses k=1..3; per slot a pool of experts
# (denoised lag-p copy, sum-of-two-periods recurrence, base-3 counter tracker),
# chosen by decayed accuracy.
from math import gcd

def _b3(n):
    s = ""
    while n:
        s = str(n % 3) + s; n //= 3
    return s or "0"

class _Ctr:
    def __init__(self):
        self.lock = None   # (n, i): next digit is _b3(n)[i]
        self.miss = 0
    def guess(self):
        if self.lock is None:
            return None
        n, i = self.lock
        return int(_b3(n)[i])
    def advance(self):
        n, i = self.lock
        i += 1
        if i >= len(_b3(n)):
            n, i = n + 1, 0
        self.lock = (n, i)
    def update(self, y):
        if self.lock is not None:
            g = self.guess()
            self.miss = self.miss * 0.8 + (g != y[-1])
            self.advance()
            if self.miss > 1.5:
                self.lock = None
            return
        if len(y) < 24 or len(y) % 3:
            return
        h = "".join(map(str, y[-24:]))
        for m in range(4, 9):
            for a in range(m + 1):
                s = h[a:a + m]
                if not s or s[0] != "1" and s[0] != "2":
                    continue
                n = int(s, 3)
                buf = "".join(_b3(n + j) for j in range(6))
                seg = h[a:]
                if len(buf) < len(seg):
                    continue
                if sum(x != z for x, z in zip(seg, buf)) <= 1:
                    # position after seg
                    pos, n2 = len(seg), n
                    while pos >= len(_b3(n2)):
                        pos -= len(_b3(n2)); n2 += 1
                    self.lock, self.miss = (n2, pos), 0.0
                    return

class _Slot:
    LAGS = list(range(1, 41))
    PAIRS = [(p, q) for p in range(2, 18) for q in range(p + 1, 18) if gcd(p, q) == 1]
    def __init__(self):
        self.y = []
        self.sc = [0.0] * (len(self.LAGS) + len(self.PAIRS) + 1)
        self.ctr = _Ctr()
        self.last = None
    def _preds(self):
        y, n = self.y, len(self.y)
        out = []
        for p in self.LAGS:
            if n >= p:
                v = [y[n - p]] + ([y[n - 2 * p]] if n >= 2 * p else []) + ([y[n - 3 * p]] if n >= 3 * p else [])
                out.append(max((0, 1, 2), key=v.count) if len(v) == 3 else v[0])
            else:
                out.append(None)
        for p, q in self.PAIRS:
            if n >= 2 * p + 2 * q:
                e = [(y[n - p] + y[n - q] - y[n - p - q]) % 3, (y[n - 2 * p] + y[n - q] - y[n - 2 * p - q]) % 3,
                     (y[n - p] + y[n - 2 * q] - y[n - p - 2 * q]) % 3]
                out.append(max((0, 1, 2), key=e.count))
            elif n >= p + q:
                out.append((y[n - p] + y[n - q] - y[n - p - q]) % 3)
            else:
                out.append(None)
        out.append(self.ctr.guess())
        return out
    def predict(self):
        self.last = self._preds()
        best, bs = 0, -1.0
        for i, g in enumerate(self.last):
            if g is not None and self.sc[i] > bs:
                best, bs = i, self.sc[i]
        g = self.last[best]
        return (g if g is not None else 0), bs
    def update(self, d):
        if self.last is None:
            self.last = self._preds()
        sc = self.sc
        for i, g in enumerate(self.last):
            sc[i] = sc[i] * 0.97 + (g == d)
        self.y.append(d)
        self.ctr.update(self.y)
        self.last = None

class Predictor:
    def __init__(self):
        self.t = 0
        self.hyp = {k: [_Slot() for _ in range(k)] for k in (1, 2, 3)}
        self.hs = {k: 0.0 for k in (1, 2, 3)}
        self.g = {}
    def predict(self):
        best = None
        for k, slots in self.hyp.items():
            g, _ = slots[self.t % k].predict()
            self.g[k] = g
            if best is None or self.hs[k] > self.hs[best]:
                best = k
        return self.g[best]
    def update(self, d):
        for k, slots in self.hyp.items():
            self.hs[k] = self.hs[k] * 0.99 + (self.g.get(k) == d)
            slots[self.t % k].update(d)
        self.t += 1
