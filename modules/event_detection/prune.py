"""Phase 4 — pruning. The **only** place a hit is ever removed.

Filling is done; now everything that should not be there goes, in one pass. Three rules,
each anchored on something structural, and **each fails open** — no anchor, no deletion.
That direction is deliberate: a rule that cannot find its evidence must not start deleting
on a guess.

  P1 **rally span.** A rally starts with a serve. Anchor on a strict-threshold serve lock
     region and drop everything outside ``[first serve - lead, last lock region + tail]``.
     No serve found anywhere in the segment -> the rule does nothing.
  P2 **grounded shuttle in the tail.** After the point ends the shuttle drops, bounces and
     rolls, and the trajectory signals happily read that as hits. Two tests:
       T1 anything after the last lock region whose swing is small;
       T2 a *trailing* hit (nothing follows it within ``gate_tail_win``) that is far from
          every dense onset and swings little. A genuine last shot of a rally sits right
          on an onset; the shuttle settling afterwards sits deep inside a run, or nowhere
          near one. Rescanned from the back until stable, because deleting the last hit
          can make the one before it trailing too.
  P3 **scoreboard dead time** (match level, :func:`dead_segments`) — warm-up and time-out
     footage that got cut as if it were a rally.

The predecessor scattered these across four places interleaved with the *adding* rules, so
a hit could be deleted and then filled straight back in by a later rule. Here, nothing is
added after this point.
"""

from __future__ import annotations

from modules.event_detection.config import PruneConfig
from modules.event_detection.evidence import SERVE_MARK, Dense, d_onset
from modules.event_detection.trajectory import Traj


def rally_span(dense: Dense, cfg: PruneConfig) -> tuple[int, int] | None:
    """P1's valid interval, or None when there is no serve evidence (fail open)."""
    if not dense:
        return None
    regions = dense.lock_regions(cfg.span_serve_run, cfg.span_serve_conf)
    serves = [r for r in regions if any(SERVE_MARK in t for t in r.labels)]
    if not serves:
        return None
    return (
        serves[0].f0 - cfg.span_lead,
        max(r.f1 for r in regions) + cfg.span_tail,
    )


def prune_segment(
    hits: dict[int, tuple[str, str]],
    traj: Traj | None,
    dense: Dense,
    cfg: PruneConfig,
) -> tuple[dict[int, tuple[str, str]], list[tuple[int, str]]]:
    """P1 + P2 -> ``(hits, drops [(frame, rule)])``. ``hits`` is mutated in place."""
    drops: list[tuple[int, str]] = []

    def drop(frame: int, rule: str) -> None:
        del hits[frame]
        drops.append((frame, rule))

    # ---- P1 rally span --------------------------------------------------------- #
    span = rally_span(dense, cfg)
    if span:
        for frame in [f for f in hits if not span[0] <= f <= span[1]]:
            drop(frame, "out_of_rally")

    if not dense or traj is None:
        return hits, drops

    # ---- P2-T1 small swings after the last lock region --------------------------- #
    regions = dense.lock_regions(cfg.tail_lock_run, cfg.tail_lock_conf)
    if regions:
        cut = max(r.f1 for r in regions) + cfg.tail_margin
        for frame in [f for f in sorted(hits) if f > cut]:
            a = traj.amp(frame)
            if a and a[0] < cfg.tail_yamp and a[1] < cfg.tail_xamp:
                drop(frame, "tail_grounded")

    # ---- P2-T2 a trailing hit must sit on a dense onset -------------------------- #
    onsets = dense.onsets(cfg.onset_conf, cfg.onset_len)
    changed = True
    while changed:
        changed = False
        current = sorted(hits)
        for i in range(len(current) - 1, -1, -1):
            frame = current[i]
            if i + 1 < len(current) and current[i + 1] - frame <= cfg.gate_tail_win:
                continue                       # not trailing: something follows it
            if d_onset(frame, onsets) < cfg.gate_d_onset:
                continue                       # right on an onset: a real closing shot
            a = traj.amp(frame)
            if a and a[0] < cfg.gate_yamp and a[1] < cfg.gate_xamp:
                drop(frame, "tail_far_onset")
                changed = True
                break                          # the hit before it may now be trailing
    return hits, drops


def dead_segments(
    score_map: dict[int, tuple[int, int] | None], serve_by_segment: dict[int, bool]
) -> set[int]:
    """P3 (match level) -> the segments to empty out entirely.

    Only fires on runs of consecutive segments at an *unchanged, known break score* —
    0:0 (before the game starts) and 11:x (the mid-game interval). Within such a run, the
    last segment carrying serve evidence is the real rally; everything before it is the
    players walking back, toweling off, or the camera on the crowd. No serve anywhere in
    the run -> fail open.

    Deliberately **not** generalized to every unchanged-score run: BST's serve labels
    produce phantoms, and a genuine rally that segmentation happened to cut in two would
    be deleted by the more aggressive rule.
    """

    def is_break(score) -> bool:
        return score == (0, 0) or (
            score is not None and max(score) == 11 and min(score) < 11
        )

    dead: set[int] = set()
    segments = sorted(score_map)
    i = 0
    while i < len(segments):
        score = score_map[segments[i]]
        j = i
        while (
            j + 1 < len(segments)
            and score is not None
            and score_map[segments[j + 1]] == score
        ):
            j += 1
        run = segments[i:j + 1]
        if len(run) >= 2 and is_break(score):
            with_serve = [s for s in run if serve_by_segment.get(s)]
            if with_serve:
                dead.update(s for s in run if s < with_serve[-1])
        i = j + 1
    return dead
