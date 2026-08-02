"""Content-addressed per-segment caching, shared by every stage that has one.

A cached segment is named after a hash of everything it is a function of — the
stage's global parameters, the segment's own frame range, and a fingerprint of
whatever upstream data went into it. Two consequences follow, and they are the
whole point:

* **Position is not identity.** Inserting, deleting or reordering segments moves
  nothing, because nothing is keyed by ``segment_index``. Only segments whose
  *content* changed are recomputed.
* **Old entries stay valid.** A key that stops being current is not deleted, so
  switching back to an earlier segmentation (or an earlier checkpoint) costs
  nothing. :func:`prune` reclaims the space when that is wanted instead.

``manifest.json`` records the current view for debugging and for :func:`prune`.
It is deliberately *not* consulted when deciding what to recompute — file
existence is — so an interrupted run resumes even if it never got to commit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MANIFEST_FILENAME = "manifest.json"
LEGACY_META_FILENAME = "meta.json"
FORMAT = 2


def canonical(value: object) -> str:
    """A stable JSON rendering, so equal values always hash equal."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def digest(*parts: object) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(canonical(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def atomic_savez(path: str | Path, **arrays) -> None:
    """``np.savez_compressed`` via a temp file, so a killed run leaves no half-entry."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp.npz")
    np.savez_compressed(tmp, **arrays)
    tmp.replace(target)


def atomic_write_json(path: str | Path, payload: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(target)


@dataclass(frozen=True)
class Entry:
    """One segment's slot in the cache."""

    index: int
    start_frame: int
    end_frame: int
    key: str
    path: Path
    cached: bool

    @property
    def label(self) -> str:
        return f"seg{self.index:04d}"


@dataclass(frozen=True)
class Plan:
    """What one run has to compute, and what it can read back."""

    entries: list[Entry]

    @property
    def missing(self) -> list[Entry]:
        return [e for e in self.entries if not e.cached]

    @property
    def hits(self) -> list[Entry]:
        return [e for e in self.entries if e.cached]

    def __len__(self) -> int:
        return len(self.entries)


class SegmentCache:
    """A directory of per-segment files keyed by content rather than position."""

    def __init__(
        self,
        directory: str | Path,
        params: dict,
        *,
        suffix: str = ".npz",
        notes: dict | None = None,
    ) -> None:
        self.dir = Path(directory)
        self.params = params
        self.params_key = digest(params)
        self.suffix = suffix
        # Recorded in the manifest but never hashed: things a caller wants to notice a
        # change in without paying a full rebuild for it. See ``stale_notes``.
        self.notes = notes

    def key_for(self, segment: dict, upstream: str = "") -> str:
        return digest(
            self.params_key,
            [int(segment["start_frame"]), int(segment["end_frame"])],
            upstream,
        )

    def path_for(self, segment: dict, upstream: str = "") -> Path:
        return self._path(
            int(segment["start_frame"]), int(segment["end_frame"]), self.key_for(segment, upstream)
        )

    def _path(self, start: int, end: int, key: str) -> Path:
        return self.dir / f"s{start:07d}-{end:07d}-{key}{self.suffix}"

    def plan(
        self,
        segments: list[dict],
        upstream: list[str] | None = None,
        *,
        force: bool = False,
    ) -> Plan:
        """Resolve every segment to a path, and say which ones need computing.

        ``upstream`` is one fingerprint per segment of the input data this cache is
        derived from; pass it wherever a stage reads more than the frame range.
        ``force`` marks everything missing so it is recomputed in place.
        """
        if upstream is not None and len(upstream) != len(segments):
            raise ValueError(
                f"upstream has {len(upstream)} fingerprint(s) for {len(segments)} segment(s)"
            )

        entries = []
        for index, segment in enumerate(segments):
            fingerprint = upstream[index] if upstream is not None else ""
            key = self.key_for(segment, fingerprint)
            path = self._path(
                int(segment["start_frame"]), int(segment["end_frame"]), key
            )
            entries.append(
                Entry(
                    index=index,
                    start_frame=int(segment["start_frame"]),
                    end_frame=int(segment["end_frame"]),
                    key=key,
                    path=path,
                    cached=(not force) and path.is_file(),
                )
            )
        return Plan(entries)

    def commit(self, plan: Plan) -> None:
        """Write ``manifest.json`` describing the current view of the cache."""
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "format": FORMAT,
            "params": self.params,
            "params_key": self.params_key,
            "notes": self.notes or {},
            "entries": [
                {
                    "index": e.index,
                    "start_frame": e.start_frame,
                    "end_frame": e.end_frame,
                    "key": e.key,
                    "file": e.path.name,
                }
                for e in plan.entries
            ],
        }
        atomic_write_json(self.dir / MANIFEST_FILENAME, manifest)

    def stale_notes(self) -> list[str]:
        """Note keys whose value has changed since the cache was last committed.

        Notes are for inputs that *do* affect the entries but whose drift is usually
        harmless and whose rebuild is expensive — the caller gets to warn instead of
        silently reusing or blindly recomputing. An empty result also covers "no
        manifest yet", which is not a change.
        """
        manifest = self.read_manifest()
        if manifest is None:
            return []
        previous = manifest.get("notes") or {}
        current = self.notes or {}
        return sorted(k for k in set(previous) | set(current)
                      if previous.get(k) != current.get(k))

    def read_manifest(self) -> dict | None:
        path = self.dir / MANIFEST_FILENAME
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------ legacy
    def migrate_legacy(
        self,
        segments: list[dict],
        upstream: list[str] | None = None,
    ) -> int:
        """Rename a pre-``manifest.json`` cache into content-addressed names.

        The old ``meta.json`` held the global parameters *and* the segment list in
        one dict, which is why any frame changing wiped everything. Its global half
        is compared against :attr:`params`; when they agree, the segments it lists
        are matched by frame range against ``segments`` and the files are renamed.
        Nothing is recomputed and nothing is read — a migration is pure renames.

        Legacy entries whose frame range is no longer in ``segments`` cannot be
        keyed (their upstream fingerprint is unknown) and are removed. Returns how
        many files were adopted.
        """
        legacy_path = self.dir / LEGACY_META_FILENAME
        if (self.dir / MANIFEST_FILENAME).is_file() or not legacy_path.is_file():
            return 0
        try:
            with legacy_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            return 0
        if not isinstance(meta, dict):
            return 0

        legacy_segments = meta.pop("segments", None)
        if not isinstance(legacy_segments, list) or meta != self.params:
            return 0

        fingerprints = {
            (int(s["start_frame"]), int(s["end_frame"])): (
                upstream[i] if upstream is not None else ""
            )
            for i, s in enumerate(segments)
        }
        adopted = 0
        for index, bounds in enumerate(legacy_segments):
            source = self.dir / f"seg{index:04d}{self.suffix}"
            if not source.is_file():
                continue
            start, end = int(bounds[0]), int(bounds[1])
            fingerprint = fingerprints.get((start, end))
            if fingerprint is None:
                source.unlink()
                continue
            key = digest(self.params_key, [start, end], fingerprint)
            source.replace(self._path(start, end, key))
            adopted += 1

        legacy_path.unlink()
        return adopted

    def discard_legacy(self) -> int:
        """Delete a pre-``manifest.json`` cache outright. Returns files removed."""
        legacy_path = self.dir / LEGACY_META_FILENAME
        if not legacy_path.is_file():
            return 0
        removed = 0
        for path in sorted(self.dir.glob(f"seg[0-9][0-9][0-9][0-9]{self.suffix}")):
            path.unlink()
            removed += 1
        legacy_path.unlink()
        return removed


def prune(directory: str | Path) -> tuple[list[Path], int]:
    """Files in ``directory`` that ``manifest.json`` no longer references.

    Returns the paths and their total size *without* deleting anything — the
    caller decides, because an unreferenced entry is a previous segmentation that
    may well be switched back to.
    """
    directory = Path(directory)
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return [], 0
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError):
        return [], 0

    referenced = {entry["file"] for entry in manifest.get("entries", [])}
    referenced.add(MANIFEST_FILENAME)
    stale = [
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name not in referenced
    ]
    return stale, sum(path.stat().st_size for path in stale)
