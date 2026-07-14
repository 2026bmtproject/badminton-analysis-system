"""Phase 3 — structural gap filling. The **only** place a hit is ever added.

Base (the primary trajectory's stream) is taken as given. Four rules then look for places
where the *structure* of a rally says a hit must exist and base has none, and each fills
one in from the strongest evidence available:

  R1 **alternation violation** — two consecutive base hits on the same side means the
     opponent hit in between and was missed. Sources, in order: a weak trajectory signal
     on the opposite side; an aux hit; failing both, the confidence-weighted centre of an
     opposite-side dense run (a synthesized point — there is no trajectory evidence at
     all, only BST's).
  R2 **unclaimed lock region** — BST held one class for a long, confident run and no hit
     was placed anywhere near it.
  R3 **upstream rescue** — an aux hit sitting right on a dense onset with high confidence.
     Measured on the reference data, 31% of the misses had *already been detected* by the
     other trajectory and were simply never fused in.
  R4 **serve fill** — the serve-rise anchor has no hit near it, BST sees a serve there,
     and it reads as bottom-court. A bottom serve's toss is usually occluded by the
     player's own body, so this is the one hit the trajectory signals structurally cannot
     find.

Every fill respects ``min_sep`` from every existing hit, so two rules cannot both fill the
same gap.

Returns ``(frame, source, tag)``; ``source`` selects the output offset (see
``config.DEFAULT_OFFSETS``) because a synthesized dense centre and a detected turning
point do not lead the true contact by the same amount.
"""

from __future__ import annotations

from modules.event_detection.config import CompleteConfig, SelectConfig, SignalConfig
from modules.event_detection.evidence import Dense, d_onset
from modules.event_detection.streams import Stream

Addition = tuple[int, str, str]


def _pick_nearest(pool, target):
    return min(pool, key=lambda f: abs(f - target)) if pool else None


def fill_alternation(
    hits: list[int],
    base: Stream,
    aux_pool: list[int],
    dense: Dense,
    cfg: CompleteConfig,
    signal_cfg: SignalConfig,
) -> list[Addition]:
    """R1 — one opposite-side hit between a same-side pair."""
    side_of = base.side_of
    sensitive = dict(base.traj.y_peaks(signal_cfg.sens_yprom))
    for f, prominence in base.traj.x_turns(signal_cfg.sens_xprom).items():
        sensitive[f] = max(sensitive.get(f, 0.0), prominence)

    added: list[Addition] = []
    current = sorted(hits)
    for a in range(len(current) - 1):
        f0, f1 = current[a], current[a + 1]
        if f1 - f0 < cfg.alt_gap:
            continue
        s0, s1 = side_of(f0), side_of(f1)
        if s0 is None or s0 != s1:
            continue
        opposite = side_of.opposite(s0)
        lo, hi = f0 + cfg.alt_margin, f1 - cfg.alt_margin
        mid = (f0 + f1) / 2

        # a) a weak trajectory signal on the opposite side
        pool = [f for f in sensitive if lo <= f <= hi and side_of(f) == opposite]
        if pool:
            best = max(pool, key=lambda f: sensitive[f])
            source = "sens"
        else:
            # b) an aux hit (opposite side, or side unknown)
            pool = [
                f for f in aux_pool if lo <= f <= hi and side_of(f) in (opposite, None)
            ]
            best = _pick_nearest(pool, mid)
            source = "refine"
        if best is None and dense:
            # c) BST only: the weighted centre of an opposite-side run
            runs = [
                r
                for r in dense.runs(cfg.alt_dense_conf, cfg.alt_dense_run, side=opposite)
                if lo < r.f0 and r.f1 < hi
            ]
            if runs:
                runs.sort(key=lambda r: (r.length, r.mean_conf), reverse=True)
                best, source = int(round(runs[0].wcentre)), "dense"
        if best is None:
            continue

        near = set(current) | {f for f, _, _ in added}
        if any(abs(best - h) < cfg.min_sep for h in near):
            continue
        added.append((best, source, "alt_fill"))
    return added


def fill_lock_regions(
    hits: list[int],
    base: Stream,
    aux_pool: list[int],
    dense: Dense,
    cfg: CompleteConfig,
    signal_cfg: SignalConfig,
    select_cfg: SelectConfig,
) -> list[Addition]:
    """R2 — one hit inside a confident lock region that nothing claimed."""
    if not dense:
        return []
    sensitive_y = sorted(base.traj.y_peaks(signal_cfg.sens_yprom))
    guard = cfg.lock_guard
    added: list[Addition] = []
    current = set(hits)

    for region in dense.lock_regions(cfg.lock_run, cfg.lock_conf):
        lo, hi = region.f0 - guard, region.f1 + guard
        if any(lo <= h <= hi for h in current):
            continue
        centre = (region.f0 + region.f1) / 2

        # a) the weak Y peak nearest the region's centre — still has to clear the
        #    amplitude gate, or this rule would happily invent a hit out of a wobble
        best = _pick_nearest([f for f in sensitive_y if lo <= f <= hi], centre)
        source = "sens"
        if best is not None and not base.traj.amp_pass(
            best, select_cfg.yamp_min, select_cfg.xamp_min, select_cfg.amp_win
        ):
            best = None
        if best is None:
            # b) an aux hit inside the region
            best = _pick_nearest([f for f in aux_pool if lo <= f <= hi], centre)
            source = "refine"
        if best is None:
            continue
        added.append((best, source, "lock_fill"))
        current.add(best)
    return added


def rescue_onsets(
    hits: list[int], aux_pool: list[int], dense: Dense, cfg: CompleteConfig
) -> list[Addition]:
    """R3 — take the aux hits that sit on a dense onset with BST agreeing."""
    if not dense:
        return []
    onsets = dense.onsets(cfg.onset_conf, cfg.onset_len)
    added: list[Addition] = []
    current = sorted(hits)

    for f in aux_pool:
        known = sorted(set(current) | {g for g, _, _ in added})
        if any(abs(f - h) <= cfg.rescue_near for h in known):
            continue
        if d_onset(f, onsets) > cfg.rescue_d_onset:
            continue
        if dense.conf_near(f, 3) < cfg.rescue_conf:
            continue
        later = [h for h in known if h > f]
        if not later or later[0] - f > cfg.rescue_tail_win:
            # Nothing follows this within a rally's worth of frames, so it looks like the
            # shuttle settling after the point ended. Phase 4 owns that call; this rule
            # simply declines to create the hit that phase 4 would then have to delete.
            continue
        added.append((f, "refine", "rescue"))
    return added


def add_serve(
    hits: list[int],
    base: Stream,
    aux: Stream | None,
    dense: Dense,
    signal_cfg: SignalConfig,
    cfg: CompleteConfig,
) -> list[Addition]:
    """R4 — the serve, anchored on serve-rise. At most one per segment."""
    anchor = base.traj.first_rise(signal_cfg.serve_rise_len, signal_cfg.serve_rise_px)
    if anchor is None or not dense:
        return []
    if any(abs(h - anchor) <= cfg.serve_near for h in hits):
        return []
    if dense.conf_near(anchor, 4) < cfg.serve_conf:
        return []
    if cfg.serve_side and dense.side_at(anchor, 4) != cfg.serve_side:
        return []

    # Snap to the nearest candidate some gate rejected, so the hit lands on the detector's
    # own timing rather than on the anchor's; fall back to the anchor when there is none.
    rejected = sorted(set(base.rejected) | set(aux.rejected if aux else []))
    near = [c for c in rejected if abs(c - anchor) <= cfg.serve_snap]
    frame = min(near, key=lambda c: abs(c - anchor)) if near else anchor
    return [(frame, "serve", "serve_fill")]


def complete_segment(
    base: Stream,
    aux: Stream | None,
    dense: Dense,
    signal_cfg: SignalConfig,
    select_cfg: SelectConfig,
    cfg: CompleteConfig,
) -> tuple[dict[int, tuple[str, str]], list[Addition]]:
    """R1 -> R2 -> R3 -> R4 over base's hits.

    -> ``(hits {frame: (source, tag)}, additions)``. The rules run in that order and each
    sees what the previous one added, so the strongest evidence claims a gap first.
    """
    hits: dict[int, tuple[str, str]] = {f: ("ball", "") for f in base.kept}

    # An aux hit that coincides with a base hit is the same hit seen twice, not a rescue
    # candidate — the pool is what aux saw and base did not.
    aux_pool: list[int] = []
    if aux:
        aux_pool = [
            f for f in aux.kept if all(abs(f - g) > cfg.fuse_tol for g in base.kept)
        ]

    additions: list[Addition] = []
    rules = (
        lambda: fill_alternation(sorted(hits), base, aux_pool, dense, cfg, signal_cfg),
        lambda: fill_lock_regions(
            sorted(hits), base, aux_pool, dense, cfg, signal_cfg, select_cfg
        ),
        lambda: rescue_onsets(sorted(hits), aux_pool, dense, cfg),
        lambda: add_serve(sorted(hits), base, aux, dense, signal_cfg, cfg),
    )
    for rule in rules:
        for frame, source, tag in rule():
            if frame in hits:
                continue
            hits[frame] = (source, tag)
            additions.append((frame, source, tag))
            if source == "refine" and frame in aux_pool:
                aux_pool.remove(frame)
    return hits, additions
