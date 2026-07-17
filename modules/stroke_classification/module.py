"""Pipeline-stage wrapper implementing the BaseModule interface.

One phase, no cache, no thresholds — which is worth saying out loud, because the stage next
door has all three. ``event_detection`` runs BST once per *frame* (tens of thousands of
windows) and every knob it has is a threshold on the result, so it caches the probabilities
under ``cache/dense_scan/`` and re-tunes against them for free. This stage runs BST once per
*hit* — a couple of thousand windows for a whole match, a few seconds on a GPU — and has
nothing to re-tune. Caching that would be machinery in exchange for nothing.

**The dense-scan cache is not reusable here, and that is the trap this stage exists to
avoid.** Those probabilities came from ±0.5 s windows centred on every frame. BST's stroke
head was trained on ``between_2_hits_with_max_limits`` windows — a hit is read from the
previous shot's arrival to a quarter-second past the reply. Same weights, different input
distribution: reading a stroke out of the dense scan would produce confident, plausible,
systematically wrong labels, with nothing anywhere to indicate it. So the windows are cut
fresh (``modules.common.bst.features.between_hits_windows``) and the model is re-run.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable, Optional

from modules.artifacts import read_records, write_artifact
from modules.base import BaseModule, StageResult
from modules.common.bst import adapter
from modules.common.bst.classes import UNKNOWN_CLASS
from modules.common.bst.model import DEFAULT_WEIGHT, default_device, load_bst_model, resolve_weight
from modules.contracts import PIPELINE, stage_path
from modules.stroke_classification import debug
from modules.stroke_classification.config import StrokeClassificationConfig
from modules.stroke_classification.predict import Prediction, classify_segment

OUTPUT_FILENAME = PIPELINE["stroke_classification"].output_filename

ProgressFn = Callable[[float], None]


class StrokeClassificationModule(BaseModule):
    """Stroke-type stage: every hit in ``events.json`` gets a stroke and a hitter."""

    name = "stroke_classification"
    dependencies = PIPELINE["stroke_classification"].dependencies   # events, pose, shuttle

    def __init__(self, config: StrokeClassificationConfig | None = None) -> None:
        self.config = config or StrokeClassificationConfig()

    def get_output_path(self, match_path) -> Path:
        return stage_path(match_path, self.name) / OUTPUT_FILENAME

    # ------------------------------------------------------------------ work
    def classify(
        self,
        match_path: Path,
        segments: list[dict],
        fps: float,
        hits: dict[int, list[tuple[int, int]]],
        on_progress: ProgressFn | None = None,
    ) -> list[Prediction]:
        """Run BST over every hit, rally by rally. Returns predictions in event order."""
        features = adapter.load_segment_features(
            match_path, shuttle_method=self.config.shuttle_method
        )
        if len(features) != len(segments):
            raise RuntimeError(
                f"BST adapter returned {len(features)} segments, expected {len(segments)}"
            )

        checkpoint = resolve_weight(self.config.bst_checkpoint)
        device = self.config.device or default_device()
        model = load_bst_model(checkpoint, device=device)
        print(f"  device:     {device}")
        print(f"  hits:       {sum(len(h) for h in hits.values())} across "
              f"{len(hits)} rally segment(s)")

        predictions: list[Prediction] = []
        done = 0
        total = sum(len(h) for h in hits.values()) or 1
        for segment_index, segment_hits in sorted(hits.items()):
            predictions.extend(
                classify_segment(
                    model,
                    features[segment_index],
                    segment_hits,
                    fps,
                    segment_index,
                    device=device,
                    batch_size=self.config.batch_size,
                )
            )
            done += len(segment_hits)
            if on_progress:
                on_progress(done / total)

        predictions.sort(key=lambda p: p.label.event_index)
        return predictions

    # ------------------------------------------------------------------- run
    def _run(
        self,
        match_path: Path,
        *,
        on_progress: Optional[ProgressFn] = None,
        debug_csv: str | Path | None = None,
    ) -> StageResult:
        """Classify every hit in the match. ``debug_csv`` also writes the per-hit details."""
        output_json = self.get_output_path(match_path)
        segments, fps = adapter.read_segments(match_path)
        hits = self._read_hits(match_path, segments)

        predictions = self.classify(match_path, segments, fps, hits, on_progress)
        records = [p.label for p in predictions]

        write_artifact(
            PIPELINE["stroke_classification"],
            records,
            output_json,
            extra={
                "fps": fps,
                "shuttle_method": self.config.shuttle_method,
                "bst": Path(self.config.bst_checkpoint or DEFAULT_WEIGHT).name,
            },
        )

        if debug_csv:
            debug.write_csv(Path(debug_csv), predictions, topk=self.config.topk)
            print(f"  debug csv:  {len(predictions)} hit(s) -> {debug_csv}")

        self._report(records)
        return StageResult(output_json)

    # ---------------------------------------------------------------- inputs
    def _read_hits(
        self, match_path: Path, segments: list[dict]
    ) -> dict[int, list[tuple[int, int]]]:
        """``events.json`` -> ``{segment_index: [(event_index, local_frame), ...]}``.

        ``HitEvent`` carries an absolute frame and nothing else — which rally it falls in is
        deliberately left as a lookup, and this is the stage that has to do it, because BST's
        windows run between consecutive hits *of the same rally*.
        """
        spec = PIPELINE["event_detection"]
        events = read_records(spec, stage_path(match_path, "event_detection") / spec.output_filename)

        bounds = [(int(s["start_frame"]), int(s["end_frame"])) for s in segments]
        hits: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for event_index, event in enumerate(events):
            frame = int(event["frame"])
            for segment_index, (start, end) in enumerate(bounds):
                if start <= frame <= end:
                    hits[segment_index].append((event_index, frame - start))
                    break
            else:
                # Not a "skip it and carry on" case: every hit was detected *inside* a
                # segment, so a frame that now falls in none means events.json and
                # segments.json are describing different cuts of the match. Anything this
                # stage produced from them would be aligned to neither.
                raise RuntimeError(
                    f"hit at frame {frame} (event {event_index}) falls in no rally segment. "
                    "events.json is stale with respect to segments.json — re-run "
                    "event_detection for this match."
                )
        return dict(hits)

    def _report(self, records: list) -> None:
        counts: dict[str, int] = defaultdict(int)
        for record in records:
            counts[record.stroke_type] += 1
        unknown = counts.pop(UNKNOWN_CLASS, 0)

        ranked = ", ".join(
            f"{stroke} {n}" for stroke, n in sorted(counts.items(), key=lambda kv: -kv[1])
        )
        print(f"  strokes:    {len(records)} hit(s) — {ranked}")
        if unknown:
            # Loud on purpose: these are hits event_detection is sure of and BST could not
            # read, and they leave the pipeline with no stroke and no hitter.
            share = unknown / len(records) if records else 0.0
            print(f"  未知球種:    {unknown} hit(s) ({share:.0%}) — recorded as 未知球種 "
                  "with no player")
