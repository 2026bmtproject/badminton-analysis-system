"""Terminal progress bar with smoothed rate estimation."""

from __future__ import annotations

import time

from modules.common import console

#: Column the bar starts at. Wide enough for the longest label in the pipeline
#: (``step 1: scan FrameDiff(MAD)``) so consecutive bars do not shift sideways.
LABEL_WIDTH = 28

#: Narrower than this and the bar is dropped entirely: on a cramped terminal the counter
#: and the ETA say everything the bar does, and say it in fewer columns.
MIN_BAR_WIDTH = 8

#: How far the work must advance before a *non-interactive* run says so again. A log file
#: cannot redraw a line, so it gets one line per tenth instead of twelve a second.
LOG_STEP = 0.1


def _rate(rate: float) -> str:
    """A rate in at most seven columns, whatever its magnitude.

    ``{rate:7.2f}`` is only seven columns for rates below a thousand. Above that it grows
    a column per digit, which on a full-width bar pushes the line past the window and on
    a narrow one makes the whole layout twitch between renders.
    """
    if rate >= 1e6:
        return f"{rate / 1e6:.1f}M"
    if rate >= 1e3:
        return f"{rate / 1e3:.1f}k"
    return f"{rate:.2f}"


def _tail(ratio: float, current: int, total: int, rate: float) -> str:
    """Everything to the right of the bar: percentage, counter, rate, ETA."""
    # No rate yet means no ETA to give. Dividing by a floor of 1e-9 instead would
    # announce `ETA 277777777h 46m` on the first render of every bar, which reads as the
    # bar being broken rather than as it having nothing to say yet.
    if current >= total:
        eta = "0.0s"
    elif rate > 0:
        eta = console.duration((total - current) / rate)
    else:
        eta = "--"
    return (f" {ratio * 100:6.2f}% ({current}/{total}) "
            f"{_rate(rate):>7}/s  ETA {eta:>7}")


class SmoothProgress:
    """Terminal progress bar using EWMA rate estimation for a stable ETA.

    Fits itself to the terminal it is going to, and falls back to one line per tenth of
    the work when there is no terminal at all -- a redirected run would otherwise get
    twelve carriage returns a second written into a log nobody can read afterwards.
    """

    def __init__(
        self,
        label: str,
        total: int,
        width: int = 28,
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
        self.rendered = False
        self.done = False

        # Read once: a bar lives for the length of one phase, and re-measuring the
        # window on every render would let a mid-run resize reflow the line under a
        # user who is watching it.
        self.live = console.interactive()
        self.columns = console.columns()
        self.next_log = LOG_STEP

        # Size the bar against the tail at its *widest* -- the counter carrying as many
        # digits as the total. Measuring the tail as it stands would let the bar shrink
        # by a character every time the counter gains a digit, or vanish halfway through
        # on a terminal that is borderline wide enough for it.
        widest_tail = len(_tail(1.0, self.total, self.total, 0.0))
        room = self.columns - 1 - len(console.INDENT) - LABEL_WIDTH - widest_tail - 2
        self.bar_width = min(self.width, room) if room >= MIN_BAR_WIDTH else 0
        # Once the bar is gone the label is the next thing to give: a window this narrow
        # should lose the end of `step 1: scan FrameDiff(MAD)`, not the end of the ETA.
        self.label_width = LABEL_WIDTH if self.bar_width else max(
            self.columns - 1 - len(console.INDENT) - widest_tail, MIN_BAR_WIDTH
        )

    def _build_bar(self, ratio: float, width: int) -> str:
        ratio = max(0.0, min(ratio, 1.0))
        filled = int(ratio * width)

        if filled >= width:
            return "=" * width
        if filled <= 0:
            return ">" + "." * (width - 1)
        return "=" * (filled - 1) + ">" + "." * (width - filled)

    def _line(self, current: int, now: float) -> str:
        """The whole bar as one line, never wider than the window it is drawn in."""
        ratio = max(0.0, min(current / self.total, 1.0))
        elapsed = max(now - self.start_time, 1e-9)
        rate = self.ewma_rate if self.ewma_rate > 0 else (current / elapsed)

        body = f"{self.label[:self.label_width]:<{self.label_width}}"
        if self.bar_width:
            body += f"[{self._build_bar(ratio, self.bar_width)}]"
        # The counter and the ETA outrank the bar, which is why the bar is what gives way
        # on a narrow terminal: a line wider than the window wraps, and after that `\r`
        # only rewinds the last physical row -- which is how a bar ends up smeared down
        # an 80-column console instead of redrawing in place.
        return f"{console.INDENT}{body}{_tail(ratio, current, self.total, rate)}"[
            :self.columns - 1
        ]

    def update(self, current: int, force: bool = False) -> None:
        if self.done:
            # The bar has already been closed off with a newline. A stage that reports
            # 100% twice (its own last chunk, then BaseModule.run's final call) must not
            # redraw over whatever has been printed since.
            return

        now = time.perf_counter()
        current = max(self.current, min(int(current), self.total))
        finished = current >= self.total

        if finished and not self.rendered:
            # Straight from nothing to done without ever showing progress: the work
            # never reported an intermediate step, so there is nothing to draw. Stages
            # with no measurable phases and fully-cached runs both land here, and
            # neither wants a bar that only ever appears full. This asks whether
            # anything was *drawn*, not whether the counter moved -- a bar opened with
            # `update(0, force=True)` has already put a line on screen, and leaving it
            # there unfinished is what used to strand a 0.00% bar with no newline.
            self.done = True
            return

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

        if self.live:
            if not force and not finished and (now - self.last_render_time) < self.min_interval:
                self.current = current
                return
            self._render(current, now)
        else:
            if not finished and current / self.total < self.next_log:
                self.current = current
                return
            console.item(self._line(current, now).strip(), indent=1)
            self.next_log = current / self.total + LOG_STEP
            self.rendered = True

        self.last_render_time = now
        self.current = current

        if finished:
            self.done = True
            if self.live:
                print()
                console.release_line()

    def _render(self, current: int, now: float) -> None:
        line = self._line(current, now)
        # Pad over whatever is still occupying this line. That may be a *different*
        # bar's render, so the width to cover is the console's idea of it rather than
        # this bar's own last line: a shrinking counter, or a handover between two
        # bars, would otherwise trail characters from the render before it.
        padding = " " * max(console.pending() - len(line), 0)
        print(f"\r{line}{padding}", end="", flush=True)
        # Only ``line`` is holding the line now -- the padded tail is blank already.
        console.hold_line(len(line))
        self.rendered = True
