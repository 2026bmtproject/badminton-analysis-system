"""Shuttle tracking (TrackNetV3): rally segments -> per-frame shuttle positions.

TrackNet turns each rally's frames into confidence heatmaps (cached under
``cache/heatmaps/``), and two independent trackers then read a trajectory out of
them:

* ``track_inpaint`` — TrackNetV3's own: the strongest blob per frame, with the gaps
  repaired by InpaintNet.
* ``track_viterbi`` — many weak candidates per frame, with the best *path* through
  them chosen by a min-cost search, then pruned and gap-filled.

Both write to ``shuttle.json``, tagged by ``method``; ``event_detection`` consumes
both. They share their first step (``blob.baseline_track``) but are otherwise
alternatives, not a sequence.
"""

from modules.shuttle_tracking.module import ShuttleTrackingConfig, ShuttleTrackingModule

__all__ = ["ShuttleTrackingConfig", "ShuttleTrackingModule"]
