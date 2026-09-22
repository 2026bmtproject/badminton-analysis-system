"""Describe a video precisely enough to tell whether two copies are the same one.

Why this exists
---------------
Every ground-truth file in this repo is indexed by FRAME NUMBER. A label that
says "rally 3 runs from frame 23422 to 23969" is only meaningful if your copy of
the video has its frames in exactly the same places as the copy the labels were
made on. YouTube hands out several encodings of the same upload, so two people
downloading "the same video" can easily end up with byte-different files.

So we record three things, from strict to loose:

  1. sha256      - byte-identical file. If this matches, you are done.
  2. frame count + fps + duration
                 - the properties the frame numbering depends on. These MUST
                   match, otherwise the labels do not apply to your file.
                 - resolution does not affect frame numbering; it may differ,
                   though analysis results can vary slightly.
  3. frame fingerprint
                 - a tiny hash of the PICTURE at a handful of fixed frame
                   numbers. Survives re-encoding, and catches both "wrong video"
                   and "right video but shifted by a few frames".

Check 3 is the useful one in practice: it passes for a re-encoded copy of the
right video and fails for anything else.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np

# Frames we look at for the picture fingerprint. The spacing only depends on how
# long the video is (coarsely), so two copies of the same video always sample the
# very same frame numbers -- even if their frame counts differ by one or two.
LONG_VIDEO_FRAMES = 30000       # ~17 min at 30 fps
STEP_LONG = 5000
STEP_SHORT = 1000


def fingerprint_frame_numbers(frame_count: int) -> list[int]:
    """Which frames we fingerprint. Roughly 12-40 of them, spread over the video."""
    step = STEP_LONG if frame_count >= LONG_VIDEO_FRAMES else STEP_SHORT
    return list(range(step // 2, frame_count, step))


@dataclass
class VideoFacts:
    """Everything we record about one video file."""

    file_name: str
    file_size: int
    sha256: str
    codec: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_sec: float
    fingerprint: list[str]


def sha256_of_file(path: Path) -> str:
    """Hash the whole file, 8 MiB at a time so a 2 GB video fits in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def probe_with_ffprobe(path: Path) -> dict:
    """Ask ffprobe for the video stream's basic properties."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,width,height,r_frame_rate,nb_frames",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout

    facts = {}
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            facts[key] = value
    return facts


def frame_hash(frame: np.ndarray) -> str:
    """Turn one picture into 16 hex characters (a 64-bit "dHash").

    Shrink the picture to 9x8 grey pixels, then ask 64 yes/no questions of the
    form "is this pixel brighter than the one to its right?". The answers barely
    change when the video is re-encoded, but are completely different for a
    different picture.
    """
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(grey, (9, 8), interpolation=cv2.INTER_AREA)
    brighter_than_right = small[:, :-1] > small[:, 1:]   # 8x8 booleans

    bits = 0
    for bit in brighter_than_right.flatten():
        bits = (bits << 1) | int(bit)
    return f"{bits:016x}"


def fingerprint_frames(path: Path, frame_count: int) -> list[str]:
    """Hash the picture at each of the fingerprint frame numbers."""
    wanted = fingerprint_frame_numbers(frame_count)

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {path}")

    hashes = []
    for frame_no in wanted:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
        ok, frame = cap.read()
        hashes.append(frame_hash(frame) if ok else "unreadable")
    cap.release()
    return hashes


def describe(path: Path, with_sha256: bool = True) -> VideoFacts:
    """Collect every fact we record about one video file."""
    probed = probe_with_ffprobe(path)

    numerator, _, denominator = probed.get("r_frame_rate", "0/1").partition("/")
    fps = float(numerator) / float(denominator or 1)

    # cv2's frame count is what the analysis pipeline actually indexes against,
    # so prefer it; fall back to ffprobe's when cv2 cannot tell.
    cap = cv2.VideoCapture(str(path))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if frame_count <= 0:
        frame_count = int(probed.get("nb_frames", 0))

    return VideoFacts(
        file_name=path.name,
        file_size=path.stat().st_size,
        sha256=sha256_of_file(path) if with_sha256 else "",
        codec=probed.get("codec_name", "?"),
        width=int(probed.get("width", 0)),
        height=int(probed.get("height", 0)),
        fps=round(fps, 6),
        frame_count=frame_count,
        duration_sec=round(float(probed.get("duration", 0.0)), 3),
        fingerprint=fingerprint_frames(path, frame_count),
    )


def hamming_distance(hex_a: str, hex_b: str) -> int:
    """How many of the 64 bits differ between two frame hashes."""
    return bin(int(hex_a, 16) ^ int(hex_b, 16)).count("1")


def facts_as_dict(facts: VideoFacts) -> dict:
    return asdict(facts)
