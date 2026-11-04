"""Recent payload throughput; conversion and catalogue reads aren't uploads."""
from collections import deque
import time


class TransferSpeed:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.samples = deque()

    def reset(self):
        self.samples.clear()

    def update(self, transferred, uploading=True):
        now = self.clock()
        if not uploading:
            self.samples.clear()
            self.samples.append((now, transferred))
            return 0.0
        if self.samples and transferred < self.samples[-1][1]:
            self.reset()
        if not self.samples or now > self.samples[-1][0]:
            self.samples.append((now, transferred))
        while len(self.samples) > 2 and self.samples[1][0] <= now - 3:
            self.samples.popleft()
        if len(self.samples) < 2:
            return 0.0
        elapsed = now - self.samples[0][0]
        return max(0.0, (transferred - self.samples[0][1]) / max(elapsed, .001))
