"""Terminal progress bar with smoothed rate estimation."""

from __future__ import annotations

import time


class SmoothProgress:
    """Terminal progress bar using EWMA rate estimation for a stable ETA."""

    def __init__(
        self,
        label: str,
        total: int,
        width: int = 32,
        smoothing: float = 0.18,
        min_interval: float = 0.08,
    ) -> None:
        self.label = label
        self.total = max(int(total), 1)
        self.width = max(int(width), 10)
        self.smoothing = float(max(0.01, min(smoothing, 0.99)))
        self.min_interval = float(max(min_interval, 0.01))

        self.current = 0
        self.start_time = time.perf_counter()
        self.last_render_time = 0.0
        self.last_sample_time = self.start_time
        self.last_sample_value = 0
        self.ewma_rate = 0.0
        self.done = False

    def _build_bar(self, ratio: float) -> str:
        ratio = max(0.0, min(ratio, 1.0))
        filled = int(ratio * self.width)

        if filled >= self.width:
            return "=" * self.width
        if filled <= 0:
            return ">" + "." * (self.width - 1)
        return "=" * (filled - 1) + ">" + "." * (self.width - filled)

    def update(self, current: int, force: bool = False) -> None:
        now = time.perf_counter()
        current = max(self.current, min(int(current), self.total))

        dt = now - self.last_sample_time
        dn = current - self.last_sample_value
        if dt > 0 and dn > 0:
            inst_rate = dn / dt
            if self.ewma_rate <= 0.0:
                self.ewma_rate = inst_rate
            else:
                self.ewma_rate = self.smoothing * inst_rate + (1.0 - self.smoothing) * self.ewma_rate
            self.last_sample_time = now
            self.last_sample_value = current

        if not force and (now - self.last_render_time) < self.min_interval and current < self.total:
            self.current = current
            return

        ratio = current / self.total
        bar = self._build_bar(ratio)
        elapsed = max(now - self.start_time, 1e-9)
        rate = self.ewma_rate if self.ewma_rate > 0 else (current / elapsed)
        eta = 0.0 if current >= self.total else (self.total - current) / max(rate, 1e-9)

        print(
            f"\r{self.label:<18} [{bar}] {ratio * 100:6.2f}% "
            f"({current}/{self.total}) | {rate:7.2f}/s | ETA {eta:7.1f}s",
            end="",
            flush=True,
        )

        self.last_render_time = now
        self.current = current

        if current >= self.total and not self.done:
            self.done = True
            print()
