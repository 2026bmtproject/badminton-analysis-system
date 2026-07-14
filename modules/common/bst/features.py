"""Turning one rally's raw geometry into the tensors BST eats.

A :class:`SegmentFeatures` holds one rally segment, already normalized, as three
parallel per-frame arrays (both players' joints, both players' court positions, the
shuttle). It is *not* a model input by itself: BST reads a fixed-length **window**, and
what a window is depends on the caller —

* ``event_detection`` asks "was there a hit at frame f?" for every f, so its windows are
  ±0.5 s around each frame (:func:`centered_windows`);
* ``stroke_classification`` already knows where the hits are, so its windows run between
  consecutive hits — the segmentation BST was trained on.

Both end up in :func:`build_window`, which is why the expensive part (extracting a
rally's geometry once) is separated from the cheap part (slicing windows out of it).

Everything here is normalization-critical: these are the exact transforms the checkpoint
was trained with, and a plausible-looking "improvement" to any of them silently degrades
every prediction rather than failing. **A zero means "not there"** — a player who was not
found, a court position that could not be read, an invisible shuttle are all zeros, and
the model was trained to read them that way. (The one place that rule is *not* literally
true after normalization is an individual missing joint; see :func:`normalize_joints`.)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from modules.common.bst.classes import BONE_PAIRS, IN_DIM, NUM_KEYPOINTS, SEQ_LEN


@dataclass(frozen=True)
class SegmentFeatures:
    """One rally segment's geometry, normalized, indexed by frames from its start.

    ``start_frame`` is the absolute video frame that local index 0 corresponds to, so a
    caller holding an absolute hit frame can convert with ``f - features.start_frame``.
    """

    joints: np.ndarray       # (N, 2, 17, 2) — bbox-normalized, player-centred
    positions: np.ndarray    # (N, 2, 2)     — court coords, 0..1 (see adapter.to_court)
    shuttle: np.ndarray      # (N, 2)        — image coords / (width, height)
    start_frame: int

    def __len__(self) -> int:
        return len(self.shuttle)

    def __post_init__(self) -> None:
        n = len(self.shuttle)
        if self.joints.shape != (n, 2, NUM_KEYPOINTS, 2) or self.positions.shape != (n, 2, 2):
            raise ValueError(
                f"inconsistent segment features: joints {self.joints.shape}, "
                f"positions {self.positions.shape}, shuttle {self.shuttle.shape}"
            )


# --------------------------------------------------------------------------- #
# Normalization — must match training exactly
# --------------------------------------------------------------------------- #


def normalize_joints(keypoints: np.ndarray, bboxes: np.ndarray, center_align: bool = True) -> np.ndarray:
    """``(n, 17, 2)`` pixel keypoints -> bbox-relative, scale-free, box-centred.

    Divided by the bounding box's diagonal, so a player at the back of the court and the
    same player at the net produce the same numbers for the same pose — the model reads
    *shape*, and their court position is fed in separately.

    A **missing joint** (a zero) is exempted from the bbox subtraction but not from the
    centre alignment, so it comes out at ``-centre`` rather than at zero. That is a quirk
    of the reference implementation, not a design: it is preserved because the weights
    were fitted on data with that quirk in it, and "fixing" it would move every joint the
    model has learned to treat as absent. Missing joints therefore land on one consistent
    value that is nowhere near any real joint, which is the property that actually matters.
    """
    dist = np.linalg.norm(bboxes[:, 2:] - bboxes[:, :2], axis=-1, keepdims=True)
    dist = np.where(dist == 0, 1.0, dist)
    ax, ay = keypoints[:, :, 0], keypoints[:, :, 1]
    xn = np.where(ax != 0.0, (ax - bboxes[:, None, 0]) / dist, 0.0)
    yn = np.where(ay != 0.0, (ay - bboxes[:, None, 1]) / dist, 0.0)
    if center_align:
        centre = (bboxes[:, :2] + bboxes[:, 2:]) / 2
        offset = (centre - bboxes[:, :2]) / dist
        xn -= offset[:, None, 0]
        yn -= offset[:, None, 1]
    return np.stack((xn, yn), axis=-1)


def normalize_shuttle(coords: np.ndarray, width: int, height: int) -> np.ndarray:
    """``(n, 2)`` shuttle pixels -> fractions of the frame. Invisible frames stay (0, 0)."""
    out = np.zeros_like(coords, dtype=np.float32)
    out[:, 0] = coords[:, 0] / width
    out[:, 1] = coords[:, 1] / height
    return out


def create_bones(joints: np.ndarray, pairs=BONE_PAIRS) -> np.ndarray:
    """``(t, 2, 17, 2)`` joints -> ``(t, 2, 19, 2)`` bone vectors.

    A bone with a zeroed endpoint is zero rather than a vector pointing at the origin,
    which is why this cannot be a plain subtraction. Given the centre-align quirk in
    :func:`normalize_joints`, in practice that guard fires for a player who is missing
    entirely (an all-zero row) rather than for one absent joint of a player who is there.
    """
    bones = []
    for start, end in pairs:
        start_j = joints[:, :, start, :]
        end_j = joints[:, :, end, :]
        bones.append(np.where((start_j != 0.0) & (end_j != 0.0), end_j - start_j, 0.0))
    return np.stack(bones, axis=-2)


def make_seq_len_same(target_len: int, joints: np.ndarray, pos: np.ndarray, shuttle: np.ndarray):
    """Force a window to exactly ``target_len`` frames; return it plus its true length.

    Longer windows are **strided** (not cropped — the whole stroke has to stay in view,
    just sampled more coarsely) and shorter ones are zero-padded at the end. The returned
    ``video_len`` tells the model how much of the window is real, and it attends only to
    that much.
    """
    video_len = len(pos)
    if video_len > target_len:
        need_padding = (video_len % target_len) > (target_len // 2)
        stride = video_len // target_len + int(need_padding)
        joints = joints[::stride][:target_len]
        pos = pos[::stride][:target_len]
        shuttle = shuttle[::stride][:target_len]
        new_video_len = len(pos)
        if need_padding:
            pad_len = target_len - new_video_len
            joints = np.pad(joints, ((0, pad_len), *([(0, 0)] * 3)))
            pos = np.pad(pos, ((0, pad_len), *([(0, 0)] * 2)))
            shuttle = np.pad(shuttle, ((0, pad_len), (0, 0)))
    else:
        new_video_len = video_len
        pad_len = target_len - new_video_len
        joints = np.pad(joints, ((0, pad_len), *([(0, 0)] * 3)))
        pos = np.pad(pos, ((0, pad_len), *([(0, 0)] * 2)))
        shuttle = np.pad(shuttle, ((0, pad_len), (0, 0)))
    return joints, pos, shuttle, new_video_len


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #

#: A window into a segment: local ``[start, end)`` frame indices.
Window = tuple[int, int]


def centered_windows(n_frames: int, half: int, stride: int = 1) -> list[Window]:
    """One window per frame, ``[f - half, f + half]``, clipped to the segment.

    ``half`` is normally ``int(fps // 2)`` — the half-second on either side of a candidate
    hit. Windows near the ends of a rally are shorter rather than shifted: padding is
    something the model understands (see :func:`make_seq_len_same`), a window centred
    somewhere other than the frame being asked about is not.
    """
    return [
        (max(0, f - half), min(n_frames, f + half + 1))
        for f in range(0, n_frames, stride)
    ]


def build_window(features: SegmentFeatures, start: int, end: int):
    """Slice ``[start, end)`` out of a segment and shape it for the model.

    Returns ``(jnb, positions, shuttle, video_len)`` where ``jnb`` is
    ``(SEQ_LEN, 2, IN_DIM)`` — each player's 17 joints and 19 bones flattened together,
    which is the layout ``BST_CG_AP.forward`` unpacks.
    """
    joints, positions, shuttle, video_len = make_seq_len_same(
        SEQ_LEN,
        features.joints[start:end].copy(),
        features.positions[start:end].copy(),
        features.shuttle[start:end].copy(),
    )
    bones = create_bones(joints)
    jnb = np.concatenate([joints, bones], axis=-2)      # (SEQ_LEN, 2, 36, 2)
    return (
        jnb.reshape(SEQ_LEN, 2, IN_DIM).astype(np.float32),
        positions.astype(np.float32),
        shuttle.astype(np.float32),
        video_len,
    )
