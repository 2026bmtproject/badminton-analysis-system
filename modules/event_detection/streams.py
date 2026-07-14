"""Phase 2 — three gates every candidate has to pass.

  1. **Amplitude.** Did the shuttle actually swing anywhere near this frame? A hit moves
     it a long way in the following half-second; a tracking wobble does not.
  2. **Same-side dedup.** Two candidates close together on the same side of the net are
     one hit fired twice (the arc top and the acceleration, say). The bigger swing wins.
  3. **Rally context.** Badminton alternates. A real hit has the *other* player hitting
     several times around it; a candidate with no opposite-side company is almost always
     the shuttle bouncing, a stray player, or the tracker losing the plot. A candidate
     whose side is unknown is exempt rather than dropped — the gate needs a side to
     reason about, and not having one is not evidence against the hit.

Phases 1 and 2 run once per trajectory, giving the two :class:`Stream` s (base and aux)
that phase 3 works between.
"""

from __future__ import annotations

from modules.event_detection.config import SelectConfig, SignalConfig
from modules.event_detection.signals import detect_candidates
from modules.event_detection.sides import SideOf
from modules.event_detection.trajectory import Traj


class Stream:
    """One trajectory's detection result: what was proposed, what survived, and why not."""

    def __init__(
        self,
        traj: Traj,
        side_of: SideOf,
        candidates: list[int],
        reasons: dict[int, set[str]],
        kept: list[int],
        sides: dict[int, str | None],
        opp_count: dict[int, int],
        stage: dict[int, str],
    ) -> None:
        self.traj = traj
        self.side_of = side_of
        self.cands = candidates          # every candidate, ascending
        self.reasons = reasons           # {frame: {signal names}}
        self.kept = kept                 # the ones through all three gates, ascending
        self.sides = sides               # {frame: side} for candidates
        self.opp_count = opp_count       # {frame: opposite-side hits nearby}
        self.stage = stage               # {frame: which gate dropped it}

    @property
    def rejected(self) -> list[int]:
        kept = set(self.kept)
        return [f for f in self.cands if f not in kept]


def select_hits(
    candidates: list[int], traj: Traj, side_of: SideOf, cfg: SelectConfig
) -> tuple[list[int], dict[int, str | None], dict[int, int], dict[int, str]]:
    """The three gates -> ``(kept, sides, opp_count, stage)``."""
    stage: dict[int, str] = {}
    amplitude: dict[int, tuple[float, float]] = {}
    gated: list[int] = []

    # 1) amplitude
    for f in candidates:
        a = traj.amp(f, cfg.amp_win)
        # An unmeasurable window (too few tracked points) scores as "huge", so it passes
        # this gate and stays comparable in the dedup below — see Traj.amp.
        amplitude[f] = a if a else (9999, 9999)
        if a is None or a[0] >= cfg.yamp_min or a[1] >= cfg.xamp_min:
            gated.append(f)
        else:
            stage[f] = "amplitude"

    sides = {f: side_of(f) for f in gated}

    # 2) same-side dedup
    kept: list[int] = []
    for f in gated:
        if kept:
            previous = kept[-1]
            here, there = sides.get(f), sides.get(previous)
            if here is not None and here == there and f - previous < cfg.dedup_gap:
                if sum(amplitude[f]) > sum(amplitude[previous]):
                    stage[previous] = "same_side"
                    kept[-1] = f
                else:
                    stage[f] = "same_side"
                continue
        kept.append(f)

    # 3) rally context
    opp_count: dict[int, int] = {}
    if cfg.need_opp > 0:
        survivors: list[int] = []
        for f in kept:
            side = sides.get(f)
            n_opp = sum(
                1
                for other in kept
                if other != f
                and abs(other - f) <= cfg.opp_win
                and sides.get(other) not in (None, side)
            )
            opp_count[f] = n_opp
            if side is None or n_opp >= cfg.need_opp:
                survivors.append(f)
            else:
                stage[f] = "rally_context"
        kept = survivors

    return kept, sides, opp_count, stage


def run_stream(
    traj: Traj | None, side_of: SideOf, signal_cfg: SignalConfig, select_cfg: SelectConfig
) -> Stream | None:
    """Phases 1 and 2 over one trajectory. An empty trajectory yields None."""
    if traj is None or len(traj) == 0:
        return None
    candidates, reasons = detect_candidates(traj, signal_cfg)
    kept, sides, opp_count, stage = select_hits(candidates, traj, side_of, select_cfg)
    return Stream(traj, side_of, candidates, reasons, kept, sides, opp_count, stage)
