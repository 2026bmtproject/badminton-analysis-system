"""The 18-column detail CSV, behind ``--debug-csv``.

``events.json`` carries hit frames and nothing else, which is the right contract and a
terrible debugging surface. This writes the whole state of the decision instead: every
candidate, what fired it, the measurements behind it, the side, and — for the ones that did
not survive — which gate or prune rule took it out.

The layout is v632's, column for column, so a run of this stage and a run of the reference
can be diffed directly. ``tests/test_event_detection_reference.py`` does exactly that
against ``ASG_vs_AA_2020_hitevent/``.

One thing to know when reading it: the ``Hit`` flag is written at ``frame + offset`` while
everything else stays on the detector's native frame, so a hit row is usually a couple of
rows below the candidate row that explains it. That is not a mistake — see
``config.DEFAULT_OFFSETS`` — it is the whole reason the offset is applied once, here and in
the records, rather than being smeared through the pipeline.
"""

from __future__ import annotations

import csv
from pathlib import Path

from modules.event_detection.streams import Stream

COLUMNS = [
    "Frame", "Visibility", "X", "Y", "Hit", "Reason", "Candidate", "Prominence",
    "Accel_Angle", "Accel_Ratio", "Ramp_Ratio", "Ramp_DVX", "YAmp", "XAmp",
    "Side", "Opp_Count", "Kept", "Remove_Stage",
]

SIGNAL_LABEL = {
    "serve": "serve", "serve_rise": "serve_rise", "ypeak": "Ypeak",
    "accel": "Accel", "ramp": "Ramp",
}
SIGNAL_ORDER = ["serve", "serve_rise", "ypeak", "accel", "ramp"]


def _cell(value):
    return "" if value is None else value


def _reason(signals: set[str]) -> str:
    return "+".join(SIGNAL_LABEL[s] for s in SIGNAL_ORDER if s in signals)


def write_segment_csv(
    path: str | Path,
    base: Stream,
    hits: dict[int, tuple[str, str]],
    events: dict[int, tuple[str, str]],
    drop_rule: dict[int, str],
    offsets: dict[str, int],
    sens_prom: float = 3,
) -> None:
    """One segment's detail CSV.

    ``hits`` are the survivors, ``events`` is everything that was ever a hit (including
    what phase 4 then deleted), and ``drop_rule`` says why each casualty went.
    """
    traj = base.traj
    frames = traj.frames
    if not frames:
        return
    row_of = {f: i for i, f in enumerate(frames)}
    prominences = traj.y_peaks(sens_prom)

    # Where the Hit flag lands: offset by source, clamped inside the segment.
    hit_rows = set()
    for frame, (source, _) in hits.items():
        shifted = frame + offsets.get(source, 0)
        shifted = min(max(shifted, frames[0]), frames[-1])
        if shifted in row_of:
            hit_rows.add(row_of[shifted])

    detail: dict[int, dict] = {}
    for frame in base.cands:
        kept = frame in hits
        detail[frame] = dict(
            candidate=1,
            reason=_reason(base.reasons.get(frame, set())),
            prominence=round(prominences.get(frame, 0.0), 1),
            values=traj.signal_values(frame),
            side=base.sides.get(frame, base.side_of(frame)),
            opp=base.opp_count.get(frame),
            kept=kept,
            stage=None if kept else (drop_rule.get(frame) or base.stage.get(frame)),
        )
    for frame, (_, tag) in events.items():
        if frame in detail:                    # a filled hit that was also a candidate
            if tag:
                existing = detail[frame]["reason"]
                detail[frame]["reason"] = f"{existing}+{tag}" if existing else tag
            continue
        kept = frame in hits
        detail[frame] = dict(
            candidate=0,
            reason=tag,
            prominence=round(prominences.get(frame, 0.0), 1),
            values=traj.signal_values(frame),
            side=base.side_of(frame),
            opp=None,
            kept=kept,
            stage=None if kept else drop_rule.get(frame),
        )

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for i, frame in enumerate(frames):
            hit = 1 if i in hit_rows else 0
            visible = 1 if traj.visible[i] else 0
            x = round(traj.xs[i]) if traj.visible[i] else 0
            y = round(traj.ys[i]) if traj.visible[i] else 0
            d = detail.get(frame)
            if not d:
                writer.writerow([frame, visible, x, y, hit] + [""] * 13)
                continue
            v = d["values"]
            writer.writerow([
                frame, visible, x, y, hit,
                d["reason"], d["candidate"], _cell(d["prominence"]),
                _cell(v.get("accel_angle")), _cell(v.get("accel_ratio")),
                _cell(v.get("ramp_ratio")), _cell(v.get("ramp_dvx")),
                _cell(v.get("yamp")), _cell(v.get("xamp")),
                _cell(d["side"]), _cell(d["opp"]),
                1 if d["kept"] else 0, _cell(d["stage"]),
            ])
