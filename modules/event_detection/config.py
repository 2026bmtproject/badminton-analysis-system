"""Every knob the stage has, in one place.

One rule: **no parameter has a default anywhere else**. The four detection phases each get
a block, plus the side lookup and the output offsets.

Two things about the numbers that are easy to get wrong later:

* **They are in frames, not seconds, and that is not an oversight.** They were tuned on
  four matches at *mixed* frame rates (25 and 30 fps), so the frame values already
  straddle both; dividing them by fps would move a working operating point for the sake of
  a unit that was never what was fitted. The one genuinely rate-dependent quantity is the
  dense-scan window half-width, which is ``int(fps // 2)`` and lives with the scan.
* **The dense-scan thresholds are thresholds on BST's probabilities**, so they are only
  meaningful against the exact normalization ``modules.common.bst`` implements. Change how
  a joint or the shuttle is normalized and every number below is quietly wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SignalConfig:
    """Phase 1 — pulling hit candidates out of one trajectory (see ``signals.py``)."""

    min_gap: int = 8              # minimum spacing when deduplicating candidates (frames)
    ypeak_prom: int = 20          # minimum prominence of a Y peak
    ypeak_edge_prom: int = 5      # first/last peak trimming (v631-compatible; see signals.py)
    serve_drop_speed: float = 5.0  # serve-drop: minimum speed of the first steep fall (px/frame)
    serve_rise_len: int = 6       # serve-rise: minimum consecutive rising steps
    serve_rise_px: int = 60       # serve-rise: minimum accumulated rise
    accel_win: int = 3
    accel_angle: int = 30
    accel_ratio: float = 1.3
    accel_dist: int = 15
    ramp_win: int = 3
    ramp_ratio: float = 2.5
    ramp_speed_max: float = 40.0
    ramp_dvx: float = 4.0
    sens_yprom: int = 3           # "sensitive point" Y-peak prominence (weak signal, for filling)
    sens_xprom: int = 20          # "sensitive point" X-turn prominence


@dataclass
class SideConfig:
    """Who hit it (see ``sides.py``).

    ``win`` / ``margin`` are the dense-scan side derivation itself (the old
    frame-level side derivation): sum p_top and p_bottom over ±``win`` frames and call it
    for whichever side leads by at least ``margin``x. The rest is the lookup on top.
    """

    win: int = 3                  # ± frames for the p_top / p_bottom rolling sum
    margin: float = 1.2           # winning side's sum must be >= margin x the loser's
    bst_snap: int = 4             # look ±snap frames for the nearest decided BST side
    skel_margin: float = 1.3      # wrist-distance ratio below this is too close to call
    skel_win: int = 2             # when it is, search ±win frames for the clearest one


@dataclass
class SelectConfig:
    """Phase 2 — the three gates every candidate must pass (see ``streams.py``)."""

    amp_win: int = 18             # ± frames of the amplitude window
    yamp_min: int = 100
    xamp_min: int = 40
    dedup_gap: int = 18           # same-side deduplication window
    need_opp: int = 2             # rally-context gate: this many opposite-side hits nearby
    opp_win: int = 140            # ... within this many frames


@dataclass
class CompleteConfig:
    """Phase 3 — structural gap filling (see ``complete.py``).

    Tuned by coordinate descent on the four reference matches (v632_dev/tune_v632.py).
    """

    fuse_tol: int = 4             # an aux hit within ±tol of a base hit is the same hit
    min_sep: int = 5              # nothing is filled in closer than this to an existing hit

    # R1 same-side alternation gap
    alt_gap: int = 10             # a same-side pair must be at least this far apart to be a gap
    alt_margin: int = 8           # how far inside the gap the search starts
    alt_dense_run: int = 3
    alt_dense_conf: float = 0.55

    # R2 unclaimed lock region
    lock_run: int = 7
    lock_conf: float = 0.65
    lock_guard: int = 12

    # R3 upstream rescue (an aux hit backed by a dense onset)
    rescue_d_onset: int = 8
    rescue_conf: float = 0.6
    rescue_near: int = 8
    rescue_tail_win: int = 40

    # R4 serve fill (anchored on serve-rise)
    serve_near: int = 12
    serve_conf: float = 0.4
    serve_side: str = "bottom"
    serve_snap: int = 4

    # dense run onsets (used by R3)
    onset_conf: float = 0.55
    onset_len: int = 3


@dataclass
class PruneConfig:
    """Phase 4 — pruning (see ``prune.py``). Every rule fails open."""

    # P1 rally span, anchored on the serve
    span_serve_run: int = 3
    span_serve_conf: float = 0.75
    span_lead: int = 6
    span_tail: int = 220

    # P2 grounded shuttle in the tail (T1: after the last lock region / T2: far from any onset)
    tail_lock_run: int = 4
    tail_lock_conf: float = 0.5
    tail_margin: int = 4
    tail_yamp: int = 450
    tail_xamp: int = 300
    gate_tail_win: int = 40
    gate_d_onset: int = 8
    gate_yamp: int = 400
    gate_xamp: int = 280
    onset_conf: float = 0.5
    onset_len: int = 3

    # P3 scoreboard dead time (match level)
    score_serve_len: int = 3
    score_serve_conf: float = 0.4


#: Systematic offset, in frames, applied to a hit frame **once**, at record-building time.
#: The detector fires at the shuttle's turning point, which leads the actual contact by
#: about two frames — so anything derived from the trajectory (ball / refine / sens) gets
#: +2. A dense-scan weighted centre and a serve-rise anchor are not turning-point
#: estimates and sit closer to the truth; tuning put them at +1.
DEFAULT_OFFSETS: dict[str, int] = {
    "ball": 2, "refine": 2, "sens": 2, "dense": 1, "serve": 1,
}


@dataclass
class EventDetectionConfig:
    """Everything the stage takes. ``base_method`` / ``aux_method`` name the two shuttle
    trajectories from ``shuttle.json``.

    The two are not interchangeable. ``base`` is the stream hits are actually detected on
    and the one the debug CSV is laid out along; ``aux`` is only ever a *rescue pool* —
    phase 3 pulls from it at positions where the structure says a hit must exist and base
    has none. That asks for base to be the denser, better-behaved curve (peaks and
    amplitudes are read off it) and aux to be the one that saw things base did not.
    ``inpaint`` fills ~96% of frames and ``viterbi`` ~56%, which is the same split (and the
    same roles) v632's ``_ball`` / ``_refine`` pair had.
    """

    base_method: str = "inpaint"
    aux_method: str = "viterbi"

    signal: SignalConfig = field(default_factory=SignalConfig)
    side: SideConfig = field(default_factory=SideConfig)
    select: SelectConfig = field(default_factory=SelectConfig)
    complete: CompleteConfig = field(default_factory=CompleteConfig)
    prune: PruneConfig = field(default_factory=PruneConfig)
    offsets: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_OFFSETS))

    # dense scan (phase 1)
    bst_checkpoint: str | None = None   # None -> modules.common.bst.model.DEFAULT_WEIGHT
    batch_size: int = 256
    device: str | None = None
    refresh_cache: bool = False

    # scoreboard dead-time rule (P3); the scores artifact is optional either way
    use_scores: bool = True
