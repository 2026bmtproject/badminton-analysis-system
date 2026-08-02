"""The scoreboard cache: one small JSON per frame range under ``cache/scores/``.

Every other cache in this project trades disk for GPU time. This one trades disk for
*money*: each entry is one Gemini call that was paid for, and re-cutting a rally by two
frames used to re-buy the whole match. Keyed by content like the rest, so only the
rallies whose frames actually moved are read again.

Two kinds of entry share the directory, because both are the same question asked of a
frame range — "what does the scoreboard say between these two frames?":

* the first pass, one per segment;
* the bisection windows ``refine_merged_segments`` reads inside a merged segment.

**Nothing is cached unless both scores came back.** A failed or partial read is a
transient fact about the API, not about the video, and pinning one would make a bad
afternoon permanent.

**No manifest is written, so** :func:`modules.common.segment_cache.prune` **will not
touch this directory.** An unreferenced entry here is a call already paid for; reclaiming
a few hundred KB by throwing it away is the wrong trade in a way it is not for heatmaps.
"""

from __future__ import annotations

import json
from pathlib import Path

from modules.common.segment_cache import SegmentCache, atomic_write_json
from modules.contracts import cache_path

CACHE_SUBDIR = "scores"


def scores_dir(match_path: str | Path) -> Path:
    """``matches/{match}/cache/scores``."""
    return Path(cache_path(match_path)) / CACHE_SUBDIR


def build_params(*, config, video: str | Path) -> dict:
    """What a cached scoreboard read is a function of.

    The compositing knobs are all here: they decide which frames are sampled and what
    image Gemini is shown, so changing any of them asks a different question. The model
    name is here for the same reason. Retry and rate-limit settings are not — they change
    how the call is made, never what it means.

    ``resize_width`` is optional (None = keep the source resolution) and is carried
    through as None rather than coerced, because "no resize" is a real setting and must
    not collide with any particular width.
    """
    return {
        "model": config.model,
        "n_frames": int(config.n_frames),
        "resize_width": None if config.resize_width is None else int(config.resize_width),
        "max_frames": None if config.max_frames is None else int(config.max_frames),
        "sigma_clip_k": float(config.sigma_clip_k),
        "sigma_clip_iter": int(config.sigma_clip_iter),
        "video": Path(video).name,
    }


class ScoreCache:
    """Reads and writes scoreboard answers by frame range. A ``None`` cache is a no-op."""

    def __init__(self, cache: SegmentCache | None) -> None:
        self._cache = cache

    @classmethod
    def open(cls, match_path: str | Path, *, config, video: str | Path) -> "ScoreCache":
        return cls(
            SegmentCache(
                scores_dir(match_path), build_params(config=config, video=video), suffix=".json"
            )
        )

    def get(self, start: int, end: int) -> dict | None:
        if self._cache is None:
            return None
        path = self._path(start, end)
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, start: int, end: int, payload: dict) -> None:
        if self._cache is None:
            return
        atomic_write_json(self._path(start, end), payload)

    def _path(self, start: int, end: int) -> Path:
        return self._cache.path_for({"start_frame": start, "end_frame": end})
