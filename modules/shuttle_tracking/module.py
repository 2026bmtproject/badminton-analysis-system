"""Pipeline-stage wrapper implementing the BaseModule interface.

Runs in two phases:

1. **Heatmaps.** For every rally segment, TrackNet predicts a per-frame confidence
   heatmap, cached under ``cache/heatmaps/``. This is the expensive, GPU-bound
   phase; it is resumable per segment and skipped entirely when the cache is
   already valid — in which case the checkpoint is never even loaded.
2. **Trajectories.** Both trackers run over those heatmaps and both results are
   written to ``shuttle.json``, tagged by ``method``. They are alternatives to each
   other, not steps of one pipeline, and ``event_detection`` consumes both — so
   this stage never has to pick a winner.

Heatmaps deliberately are not a stage artifact: they are derived media (big,
rebuildable, read by nobody outside this package), which is what ``cache/`` is for.
Splitting the trackers into their own stage would buy nothing — resume already
comes from the cache — while forcing a second DAG node whose output no contract
describes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from modules.artifacts import read_segments, write_artifact
from modules.base import BaseModule, StageResult
from modules.common.config import repo_root
from modules.contracts import (
    PIPELINE,
    SHUTTLE_METHODS,
    ShuttlePoint,
    artifact_path,
    resolve_input_video,
)
from modules.shuttle_tracking import blob, heatmap_cache
from modules.shuttle_tracking.track_viterbi import ViterbiConfig

OUTPUT_FILENAME = PIPELINE["shuttle_tracking"].output_filename

DEFAULT_TRACKNET = "models/TrackNet_best.pt"
DEFAULT_INPAINTNET = "models/InpaintNet_best.pt"

#: Frames of one segment held in memory at once. A 512x288 RGB frame costs 442 KB, so
#: this caps the frame buffer — the stage's largest allocation — at ~530 MB however
#: long a rally runs. A longer segment is inferred in consecutive chunks; the only
#: cost is that a frame window cannot span a chunk boundary (one seam per 1200 frames).
#: The heatmaps being accumulated still scale with the segment (147 KB/frame), so peak
#: RAM is not flat, but it grows at a third of the rate. Measured peak on the longest
#: real rally (1011 frames): ~1.0 GB.
MAX_CHUNK_FRAMES = 1200

ProgressFn = Callable[[float], None]


@dataclass
class ShuttleTrackingConfig:
    """Knobs for the shuttle_tracking stage.

    ``eval_mode`` is TrackNet's temporal ensembling. ``nonoverlap`` predicts each
    frame exactly once and is the default; ``weight``/``average`` slide the window
    one frame at a time, costing ``seq_len`` times the compute for a modest gain.

    ``batch_size`` defaults to whatever the GPU's free VRAM can hold (roughly
    0.65 GB per sample) — pin it only to reproduce a specific run. ``chunk_frames``
    caps how many frames of one segment are held at once, which is what bounds the
    stage's peak RAM.
    """

    tracknet_checkpoint: str = DEFAULT_TRACKNET
    inpaintnet_checkpoint: str = DEFAULT_INPAINTNET
    eval_mode: str = "nonoverlap"
    batch_size: int | None = None  # None -> sized from free VRAM
    chunk_frames: int = MAX_CHUNK_FRAMES
    threshold: float = blob.DEFAULT_THRESHOLD
    inpaint_eval_mode: str = "weight"
    inpaint_batch_size: int = 16
    viterbi: ViterbiConfig = field(default_factory=ViterbiConfig)
    methods: tuple[str, ...] = SHUTTLE_METHODS
    device: str | None = None
    refresh_cache: bool = False


class ShuttleTrackingModule(BaseModule):
    """Shuttle-trajectory stage (TrackNetV3).

    Consumes ``match_segmentation``'s ``segments.json`` plus the raw match video;
    writes ``stages/shuttle_tracking/shuttle.json`` and a ``status.json``, and
    leaves the per-segment heatmaps in ``cache/heatmaps/``.
    """

    name = "shuttle_tracking"
    dependencies = PIPELINE["shuttle_tracking"].dependencies  # ["match_segmentation"]

    def __init__(self, config: ShuttleTrackingConfig | None = None) -> None:
        self.config = config or ShuttleTrackingConfig()
        for method in self.config.methods:
            if method not in SHUTTLE_METHODS:
                raise ValueError(
                    f"unknown method {method!r}; expected any of {SHUTTLE_METHODS}"
                )

    def get_output_path(self, match_path) -> Path:
        return artifact_path(match_path, self.name)

    def _checkpoint(self, configured: str) -> Path:
        """Resolve a checkpoint path, relative paths being relative to the repo root."""
        path = Path(configured)
        return path if path.is_absolute() else repo_root() / path

    # ---------------------------------------------------------------- phase 1
    def build_heatmaps(
        self,
        match_path: Path,
        video: Path,
        segments: list[dict],
        on_progress: ProgressFn | None = None,
    ) -> None:
        """Fill the heatmap cache, skipping segments that are already cached."""
        meta = heatmap_cache.build_meta(
            checkpoint=self._checkpoint(self.config.tracknet_checkpoint),
            eval_mode=self.config.eval_mode,
            chunk_frames=self.config.chunk_frames,
            video=video,
            segments=segments,
        )
        heatmap_cache.prepare(match_path, meta, force=self.config.refresh_cache)

        pending = [
            i for i in range(len(segments))
            if not heatmap_cache.segment_file(match_path, i).is_file()
        ]
        if not pending:
            if on_progress:
                on_progress(1.0)
            return

        # Imported here so a run served entirely from cache never pays for torch.
        from modules.shuttle_tracking.tracknet import auto_batch_size, describe_device, load_tracknet

        net = load_tracknet(
            self._checkpoint(self.config.tracknet_checkpoint), self.config.device
        )
        batch_size = self.config.batch_size or auto_batch_size(net.device)

        print(f"  device:   {describe_device(net.device)}")
        if net.device.type != "cuda":
            # Silence here would let someone sit through a 40-minute run without ever
            # realising their GPU went unused.
            # Plain ASCII: this is the one message nobody may miss, and a Windows
            # console in a legacy code page mangles anything else.
            print("  [warn] NO GPU IN USE - running TrackNet on the CPU is about 8x")
            print("         slower (~12 fps vs ~90 fps): expect ~40 min for a full")
            print("         match instead of ~5. If you do have an NVIDIA GPU, your")
            print("         torch is probably the CPU build; re-run `uv sync`.")
        print(
            f"  TrackNet: seq_len={net.seq_len} bg_mode={net.bg_mode!r} "
            f"eval_mode={self.config.eval_mode} batch_size={batch_size}"
            f"{' (auto)' if self.config.batch_size is None else ''}"
        )
        print(f"  heatmaps: {len(pending)} segment(s) to compute, "
              f"{len(segments) - len(pending)} cached")

        for done, index in enumerate(pending):
            heatmaps, img_shape = self._infer_segment(video, segments[index], net, batch_size)
            heatmap_cache.save_segment(
                heatmap_cache.segment_file(match_path, index), heatmaps, img_shape
            )
            print(f"    seg{index:04d}: {len(heatmaps)} frames")
            if on_progress:
                on_progress((done + 1) / len(pending))

    def _infer_segment(
        self,
        video: Path,
        segment: dict,
        net,
        batch_size: int,
    ) -> tuple[np.ndarray, tuple[int, int]]:
        """Heatmaps for one segment, decoded and inferred in memory-bounded chunks."""
        from modules.shuttle_tracking.inference import (
            HEIGHT,
            WIDTH,
            infer_heatmaps,
            read_segment_frames,
        )

        start, end = int(segment["start_frame"]), int(segment["end_frame"])
        total = end - start + 1

        def infer(a: int, b: int):
            frames, img_shape = read_segment_frames(str(video), a, b)
            heatmaps = infer_heatmaps(
                frames, net, eval_mode=self.config.eval_mode, batch_size=batch_size
            )
            return heatmaps, img_shape

        if total <= self.config.chunk_frames:
            # The common case. Return the array as inferred instead of copying it into
            # a second one — a duplicate heatmap buffer is pure waste.
            return infer(start, end)

        out = np.zeros((total, HEIGHT, WIDTH), dtype=np.uint8)
        img_shape: tuple[int, int] = (0, 0)
        offset = 0
        for chunk_start in range(start, end + 1, self.config.chunk_frames):
            chunk_end = min(chunk_start + self.config.chunk_frames - 1, end)
            heatmaps, img_shape = infer(chunk_start, chunk_end)
            out[offset : offset + len(heatmaps)] = heatmaps
            offset += len(heatmaps)
            del heatmaps  # the next chunk's decode must not stack on this one

        # A truncated video can yield fewer frames than the segment claims.
        return out[:offset], img_shape

    # ---------------------------------------------------------------- phase 2
    def build_tracks(
        self,
        match_path: Path,
        segments: list[dict],
        fps: float,
        on_progress: ProgressFn | None = None,
    ) -> list[ShuttlePoint]:
        """Run every configured tracker over the cached heatmaps."""
        # Imported lazily: running only the viterbi tracker then costs no torch at all.
        track_inpaint = inpaint_net = None
        if "inpaint" in self.config.methods:
            from modules.shuttle_tracking import track_inpaint

            inpaint_net = track_inpaint.load_inpaintnet(
                self._checkpoint(self.config.inpaintnet_checkpoint), self.config.device
            )
        from modules.shuttle_tracking import track_viterbi

        records: list[ShuttlePoint] = []
        for index, segment in enumerate(segments):
            path = heatmap_cache.segment_file(match_path, index)
            if not path.is_file():
                raise RuntimeError(f"heatmap cache is missing seg{index:04d}: {path}")

            heatmaps, img_shape = heatmap_cache.load_segment(path)
            xy_base, conf_base = blob.baseline_track(
                heatmaps, img_shape, self.config.threshold
            )
            start_frame = int(segment["start_frame"])

            for method in self.config.methods:
                if method == "inpaint":
                    xy, conf = track_inpaint.track(
                        xy_base, conf_base, img_shape, inpaint_net,
                        eval_mode=self.config.inpaint_eval_mode,
                        batch_size=self.config.inpaint_batch_size,
                    )
                else:
                    xy, conf = track_viterbi.track(
                        heatmaps, xy_base, img_shape, fps, self.config.viterbi
                    )
                records.extend(
                    _to_records(xy, conf, start_frame=start_frame, segment_index=index, method=method)
                )

            if on_progress:
                on_progress((index + 1) / len(segments))
        return records

    # -------------------------------------------------------------------- run
    def _run(
        self,
        match_path: Path,
        *,
        on_progress: Optional[ProgressFn] = None,
        only_heatmap: bool = False,
    ) -> StageResult:
        """Track the shuttle through every rally segment.

        ``only_heatmap`` stops after the cache is filled — useful for doing the
        expensive GPU pass once and then iterating on the trackers.
        """
        output_json = self.get_output_path(match_path)
        video = resolve_input_video(match_path)
        segments, fps = read_segments(match_path)

        # The GPU pass dominates the runtime, so it owns most of the progress bar.
        self.build_heatmaps(
            match_path, video, segments,
            on_progress=(lambda f: on_progress(0.85 * f)) if on_progress else None,
        )
        if only_heatmap:
            # The cache is warm but the stage produced no artifact, so it is not
            # done — leaving it COMPLETED would make the runner skip it forever.
            cache_dir = heatmap_cache.heatmap_dir(match_path)
            print(f"  heatmap cache ready; stage still PENDING (no {OUTPUT_FILENAME} yet)")
            return StageResult(cache_dir, pending=True)

        records = self.build_tracks(
            match_path, segments, fps,
            on_progress=(lambda f: on_progress(0.85 + 0.15 * f)) if on_progress else None,
        )
        write_artifact(
            PIPELINE["shuttle_tracking"],
            records,
            output_json,
            extra={
                "fps": fps,
                "methods": list(self.config.methods),
                "tracknet": Path(self.config.tracknet_checkpoint).name,
                "inpaintnet": Path(self.config.inpaintnet_checkpoint).name,
                "eval_mode": self.config.eval_mode,
                "threshold": self.config.threshold,
                "fill": self.config.viterbi.fill,
            },
        )
        return StageResult(output_json)


def _to_records(
    xy: np.ndarray,
    conf: np.ndarray,
    *,
    start_frame: int,
    segment_index: int,
    method: str,
) -> list[ShuttlePoint]:
    """Turn one segment's ``(xy, conf)`` into contract records.

    Segment-local frame ``t`` becomes the absolute frame ``start_frame + t``, the
    coordinate system every other stage indexes by.
    """
    records = []
    for t in range(len(xy)):
        visible = not np.isnan(xy[t, 0])
        records.append(
            ShuttlePoint(
                frame=start_frame + t,
                segment_index=segment_index,
                method=method,
                x=round(float(xy[t, 0]), 2) if visible else None,
                y=round(float(xy[t, 1]), 2) if visible else None,
                visible=visible,
                confidence=round(float(conf[t]), 4),
            )
        )
    return records
