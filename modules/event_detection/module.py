"""Pipeline-stage wrapper implementing the BaseModule interface.

Runs in two phases, the same split ``shuttle_tracking`` and ``pose`` use, for the same
reason:

1. **Dense scan.** BST is run once per frame of every rally, and the resulting 25-class
   probabilities are cached under ``cache/dense_scan/``. This is the only phase that wants
   a GPU; it is resumable per segment and skipped entirely when the cache is valid.
2. **Detection.** Four phases of pure geometry and thresholds over those probabilities and
   the two shuttle trajectories, ending in ``events.json``.

Nearly every knob in this stage is a threshold on what phase 1 produced. Keeping the
probabilities means tuning any of them re-runs phase 2 alone, in seconds, on a laptop.

Both shuttle trajectories are used, and they are not interchangeable: ``base``
(``inpaint``) is the stream hits are detected on, ``aux`` (``viterbi``) is a rescue pool
that phase 3 draws from where the structure of the rally says a hit is missing. See
``config.EventDetectionConfig``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import numpy as np

from modules.artifacts import read_artifact, read_records, write_artifact
from modules.base import BaseModule, StageState, StageStatus, _now_iso, stage_completed, write_status
from modules.common.bst import centered_windows, predict_windows
from modules.common.bst import adapter
from modules.common.bst.model import DEFAULT_WEIGHT, default_device, load_bst_model, resolve_weight
from modules.contracts import COCO_KEYPOINTS, PIPELINE, HitEvent, stage_path
from modules.event_detection import dense_cache, debug
from modules.event_detection.complete import complete_segment
from modules.event_detection.config import EventDetectionConfig
from modules.event_detection.evidence import Dense
from modules.event_detection.prune import dead_segments, prune_segment
from modules.event_detection.sides import SideOf, skeletons_by_segment
from modules.event_detection.streams import Stream, run_stream
from modules.event_detection.trajectory import Traj, build_trajectories

OUTPUT_FILENAME = PIPELINE["event_detection"].output_filename

ProgressFn = Callable[[float], None]


class SegmentResult:
    """One segment's outcome, kept whole so the debug CSV can explain it afterwards."""

    __slots__ = ("base", "hits", "events", "drop_rule")

    def __init__(
        self,
        base: Stream,
        hits: dict[int, tuple[str, str]],
        events: dict[int, tuple[str, str]],
        drop_rule: dict[int, str],
    ) -> None:
        self.base = base            # the base stream — also the debug CSV's row template
        self.hits = hits            # {frame: (source, tag)} — the survivors
        self.events = events        # {frame: (source, tag)} — including what phase 4 cut
        self.drop_rule = drop_rule  # {frame: rule}

    @property
    def hit_frames(self) -> list[int]:
        return sorted(self.hits)


class EventDetectionModule(BaseModule):
    """Hit-detection stage.

    Consumes ``shuttle.json`` (both trajectories) and ``pose.json``, plus — through
    ``modules.common.bst.adapter`` — ``segments.json`` and ``court.json``, which the
    dependencies above already guarantee. ``scores.json`` is used when it exists and the
    stage says so out loud when it does not.
    """

    name = "event_detection"
    dependencies = PIPELINE["event_detection"].dependencies                    # shuttle, pose
    optional_dependencies = PIPELINE["event_detection"].optional_dependencies  # scores

    def __init__(self, config: EventDetectionConfig | None = None) -> None:
        self.config = config or EventDetectionConfig()

    def get_output_path(self, match_path) -> Path:
        return stage_path(match_path, self.name) / OUTPUT_FILENAME

    # ---------------------------------------------------------------- phase 1
    def build_dense_scan(
        self,
        match_path: Path,
        segments: list[dict],
        fps: float,
        on_progress: ProgressFn | None = None,
    ) -> None:
        """Fill the dense-scan cache, skipping segments that are already there."""
        half = int(fps // 2)
        checkpoint = resolve_weight(self.config.bst_checkpoint)
        meta = dense_cache.build_meta(
            checkpoint=checkpoint,
            half=half,
            shuttle_method=self.config.base_method,
            segments=segments,
        )
        dense_cache.prepare(match_path, meta, force=self.config.refresh_cache)

        pending = [
            i for i in range(len(segments))
            if not dense_cache.segment_file(match_path, i).is_file()
        ]
        if not pending:
            print(f"  dense scan: {len(segments)} segment(s) cached")
            if on_progress:
                on_progress(1.0)
            return

        # Only now does anything import torch or touch the geometry: a cached run pays for
        # neither.
        features = adapter.load_segment_features(
            match_path, shuttle_method=self.config.base_method
        )
        if len(features) != len(segments):
            raise RuntimeError(
                f"BST adapter returned {len(features)} segments, expected {len(segments)}"
            )

        device = self.config.device or default_device()
        model = load_bst_model(checkpoint, device=device)
        print(f"  device:     {device}")
        print(f"  dense scan: {len(pending)} segment(s) to compute, "
              f"{len(segments) - len(pending)} cached (window +/-{half} frames)")

        total = sum(len(features[i]) for i in pending) or 1
        done = 0
        for index in pending:
            segment_features = features[index]
            windows = centered_windows(len(segment_features), half)
            probabilities = predict_windows(
                model,
                segment_features,
                windows,
                device=device,
                batch_size=self.config.batch_size,
            )
            dense_cache.save_segment(
                dense_cache.segment_file(match_path, index),
                probabilities,
                segment_features.start_frame,
            )
            done += len(segment_features)
            if on_progress:
                on_progress(done / total)

    # ---------------------------------------------------------------- phase 2
    def detect_segment(
        self,
        base_traj: Traj | None,
        aux_traj: Traj | None,
        dense: Dense,
        skeletons: dict[int, dict[str, dict]] | None,
    ) -> SegmentResult | None:
        """Phases 1-4 over one segment. No base trajectory -> no result."""
        if base_traj is None or len(base_traj) == 0:
            return None

        cfg = self.config
        side_map = dense.side_map(cfg.side.win, cfg.side.margin)

        def side_for(traj: Traj) -> SideOf:
            # One SideOf per trajectory: the skeleton fallback measures the shuttle against
            # the wrists, so it has to be asking about *this* stream's shuttle.
            return SideOf(
                side_map, skeletons, traj.at,
                snap=cfg.side.bst_snap,
                margin=cfg.side.skel_margin,
                win=cfg.side.skel_win,
            )

        base = run_stream(base_traj, side_for(base_traj), cfg.signal, cfg.select)
        if base is None:
            return None
        aux = (
            run_stream(aux_traj, side_for(aux_traj), cfg.signal, cfg.select)
            if aux_traj is not None and len(aux_traj)
            else None
        )

        hits, _ = complete_segment(base, aux, dense, cfg.signal, cfg.select, cfg.complete)
        events = dict(hits)
        hits, drops = prune_segment(hits, base_traj, dense, cfg.prune)
        return SegmentResult(base, hits, events, dict(drops))

    def detect(
        self,
        match_path: Path,
        segments: list[dict],
        scores: dict[int, tuple[int, int] | None] | None,
        on_progress: ProgressFn | None = None,
    ) -> dict[int, SegmentResult]:
        """Every segment, then the match-level scoreboard rule."""
        cfg = self.config
        shuttle = read_records(PIPELINE["shuttle_tracking"], self._artifact(match_path, "shuttle_tracking"))
        base_trajectories = build_trajectories(shuttle, cfg.base_method)
        aux_trajectories = build_trajectories(shuttle, cfg.aux_method)
        if not base_trajectories:
            raise RuntimeError(
                f"shuttle_tracking output has no {cfg.base_method!r} points — it was run "
                "with a different method"
            )

        poses = read_records(PIPELINE["pose"], self._artifact(match_path, "pose"))
        skeletons = skeletons_by_segment(poses, COCO_KEYPOINTS)

        results: dict[int, SegmentResult] = {}
        dense_by_segment: dict[int, Dense] = {}
        for index in range(len(segments)):
            path = dense_cache.segment_file(match_path, index)
            if not path.is_file():
                raise RuntimeError(f"dense-scan cache is missing seg{index:04d}: {path}")
            probabilities, start_frame = dense_cache.load_segment(path)
            dense = Dense(probabilities, start_frame)
            dense_by_segment[index] = dense

            result = self.detect_segment(
                base_trajectories.get(index),
                aux_trajectories.get(index),
                dense,
                skeletons.get(index),
            )
            if result is not None:
                results[index] = result
            if on_progress:
                on_progress((index + 1) / len(segments))

        # ---- match level: scoreboard dead time -------------------------------- #
        if scores:
            serve_by_segment = {
                index: dense_by_segment[index].has_serve(
                    cfg.prune.score_serve_len, cfg.prune.score_serve_conf
                )
                for index in results
            }
            dead = dead_segments(scores, serve_by_segment)
            emptied = 0
            for index in dead:
                result = results.get(index)
                if not result:
                    continue
                emptied += len(result.hits)
                for frame in list(result.hits):
                    result.drop_rule[frame] = "score_dead"
                    del result.hits[frame]
            print(f"  scoreboard: {len(dead)} dead segment(s), {emptied} hit(s) dropped")
        return results

    # -------------------------------------------------------------------- run
    def run(
        self,
        match_path,
        on_progress: Optional[ProgressFn] = None,
        debug_csv: str | Path | None = None,
    ) -> Path:
        """Detect every hit in the match. ``debug_csv`` also writes the 18-column details."""
        match_path = Path(match_path)
        out_dir = stage_path(match_path, self.name)
        output_json = self.get_output_path(match_path)

        state = StageState(name=self.name, status=StageStatus.RUNNING, started_at=_now_iso())
        write_status(out_dir, state)

        try:
            segments, fps = self._read_segments(match_path)
            scores = self._read_scores(match_path)

            # The scan dominates the runtime, so it owns most of the progress bar.
            self.build_dense_scan(
                match_path, segments, fps,
                on_progress=(lambda f: on_progress(0.9 * f)) if on_progress else None,
            )
            results = self.detect(
                match_path, segments, scores,
                on_progress=(lambda f: on_progress(0.9 + 0.1 * f)) if on_progress else None,
            )

            records = [
                HitEvent(frame=frame)
                for frame in sorted(
                    self._offset(frame, source, result.base.traj)
                    for result in results.values()
                    for frame, (source, _) in result.hits.items()
                )
            ]
            write_artifact(
                PIPELINE["event_detection"],
                records,
                output_json,
                extra={
                    "fps": fps,
                    "base_method": self.config.base_method,
                    "aux_method": self.config.aux_method,
                    "bst": Path(self.config.bst_checkpoint or DEFAULT_WEIGHT).name,
                    "offsets": self.config.offsets,
                    "scoreboard_rule": scores is not None,
                },
            )

            if debug_csv:
                self._write_debug(Path(debug_csv), results)

            print(f"  {len(records)} hit(s) across {len(results)} segment(s)")

            state.status = StageStatus.COMPLETED
            state.finished_at = _now_iso()
            state.output_path = str(output_json.relative_to(match_path))
            write_status(out_dir, state)
            if on_progress:
                on_progress(1.0)
            return output_json
        except Exception as e:
            state.status = StageStatus.FAILED
            state.finished_at = _now_iso()
            state.error = str(e)
            write_status(out_dir, state)
            raise

    def _offset(self, frame: int, source: str, traj: Traj) -> int:
        """Apply the systematic offset, once, and keep the hit inside its segment."""
        shifted = frame + self.config.offsets.get(source, 0)
        return min(max(shifted, traj.frames[0]), traj.frames[-1])

    def _write_debug(self, out_dir: Path, results: dict[int, SegmentResult]) -> None:
        for index, result in results.items():
            debug.write_segment_csv(
                out_dir / f"seg{index:04d}_hitevent.csv",
                result.base, result.hits, result.events, result.drop_rule,
                self.config.offsets, sens_prom=self.config.signal.sens_yprom,
            )
        print(f"  debug csv:  {len(results)} file(s) -> {out_dir}")

    # ----------------------------------------------------------------- inputs
    def _artifact(self, match_path: Path, stage: str) -> Path:
        spec = PIPELINE[stage]
        return stage_path(match_path, stage) / spec.output_filename

    def _read_segments(self, match_path: Path) -> tuple[list[dict], float]:
        spec = PIPELINE["match_segmentation"]
        envelope = read_artifact(spec, self._artifact(match_path, "match_segmentation"))
        segments = envelope[spec.record_key]
        if not segments:
            raise RuntimeError("no segments in match_segmentation output")
        fps = envelope.get("fps")
        if not fps:
            raise RuntimeError("match_segmentation output carries no fps")
        return segments, float(fps)

    def _read_scores(self, match_path: Path) -> dict[int, tuple[int, int] | None] | None:
        """``{segment_index: (a, b) | None}``, or None when the rule is not running.

        Says so either way. The scoreboard rule is the one thing in this stage that can be
        absent without an error, which makes it exactly the thing that could go missing for
        a whole match without anyone noticing.
        """
        if not self.config.use_scores:
            print("  scoreboard: rule disabled (--no-scores)")
            return None
        if not stage_completed(match_path, "score_recognition"):
            print("  scoreboard: [warn] score_recognition has not run for this match, so")
            print("              the dead-time rule is OFF. Warm-up and time-out footage")
            print("              between rallies will keep whatever hits are detected in")
            print("              it. Run `python -m modules.score_recognition <match>`")
            print("              first to enable it.")
            return None

        spec = PIPELINE["score_recognition"]
        records = read_records(spec, self._artifact(match_path, "score_recognition"))
        scores: dict[int, tuple[int, int] | None] = {}
        for record in records:
            index = int(record["segment_index"])
            a, b = record.get("score_a"), record.get("score_b")
            scores[index] = (int(a), int(b)) if a is not None and b is not None else None
        if not scores:
            print("  scoreboard: [warn] scores.json is empty, dead-time rule is OFF")
            return None
        print(f"  scoreboard: rule ON ({len(scores)} segment(s) scored)")
        return scores
