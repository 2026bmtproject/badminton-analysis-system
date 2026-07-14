"""BST's label space and the skeleton layout its features are built on.

The weight this project ships is the *merged* 25-class head::

    0        未知球種
    1..12    Top_<球種>
    13..24   Bottom_<球種>

so a single prediction carries **both** which stroke it was and **who hit it** — the
side is not a separate model output, it is baked into the class name. That is why
``event_detection`` can fuse ``p_top``/``p_bottom`` out of the same forward pass that
``stroke_classification`` uses for the stroke type, and why both stages want the index
groups below rather than re-deriving them from string prefixes.

The 12 base strokes are reported to users merged down to :data:`CLASSES_8` (the pairs
BST cannot reliably separate — 長球/挑球, 發短球/發長球 — collapse). Stage artifacts
store the Chinese 8-class name; see ``modules.contracts.StrokeLabel``.
"""

from __future__ import annotations

from modules.contracts import COCO_KEYPOINTS

#: The 12 strokes BST distinguishes, in the order the trained head expects. Do not
#: reorder: the index of a name here *is* its class index in the checkpoint.
BASE_STROKES = (
    "放小球", "擋小球", "殺球", "挑球", "長球", "平球",
    "切球", "推球", "撲球", "勾球", "發短球", "發長球",
)

UNKNOWN_CLASS = "未知球種"

#: All 25 class names, index-aligned to the model's output logits.
STROKE_CLASSES: tuple[str, ...] = (
    (UNKNOWN_CLASS,)
    + tuple(f"Top_{s}" for s in BASE_STROKES)
    + tuple(f"Bottom_{s}" for s in BASE_STROKES)
)

N_CLASSES = len(STROKE_CLASSES)          # 25
SEQ_LEN = 100                            # frames per window, fixed by the checkpoint

#: Which logits belong to each side. ``event_detection`` sums these into the
#: p_top / p_bottom evidence it fuses a hitter's side from.
TOP_INDICES = tuple(i for i, c in enumerate(STROKE_CLASSES) if c.startswith("Top_"))
BOTTOM_INDICES = tuple(i for i, c in enumerate(STROKE_CLASSES) if c.startswith("Bottom_"))
UNKNOWN_INDEX = STROKE_CLASSES.index(UNKNOWN_CLASS)

#: The 8 strokes users actually see. 長球/挑球 both fly high to the back, and 發短球/
#: 發長球 differ only in depth — BST confuses each pair often enough that reporting them
#: separately would be reporting noise, so each pair is merged.
BASE12_TO_8 = {
    "放小球": "小球", "擋小球": "小球",
    "挑球": "高遠球", "長球": "高遠球",
    "平球": "平快球", "推球": "平快球",
    "發短球": "發球", "發長球": "發球",
    "殺球": "殺球", "切球": "切球", "撲球": "撲球", "勾球": "勾球",
}
CLASSES_8 = ("高遠球", "發球", "小球", "平快球", "殺球", "切球", "撲球", "勾球")


def to_base(class_name: str) -> str | None:
    """``"Top_殺球"`` -> ``"殺球"``. None for :data:`UNKNOWN_CLASS`, which has no side."""
    if class_name.startswith("Top_"):
        return class_name[len("Top_"):]
    if class_name.startswith("Bottom_"):
        return class_name[len("Bottom_"):]
    return None


def to_side(class_name: str) -> str | None:
    """Who hit it: ``"top"`` / ``"bottom"`` (matching ``contracts.POSE_PLAYERS``), or None.

    Lowercased on purpose — the checkpoint's class names are capitalized, but every
    artifact in this pipeline names the two players in lowercase, and a stage writing
    ``"Top"`` into ``HitEvent.player`` would silently not match anything downstream.
    """
    if class_name.startswith("Top_"):
        return "top"
    if class_name.startswith("Bottom_"):
        return "bottom"
    return None


def to8(base: str | None) -> str | None:
    """One of the 12 base strokes -> one of :data:`CLASSES_8`. None passes through."""
    if base is None:
        return None
    return BASE12_TO_8.get(base, base)


# --------------------------------------------------------------------------- #
# Skeleton layout
# --------------------------------------------------------------------------- #

#: BST was trained on COCO-17 skeletons, which is exactly what ``pose`` emits — so the
#: joint order is taken from the contract rather than re-typed here, and the bone pairs
#: below index straight into ``PoseFrame.keypoints``.
NUM_KEYPOINTS = len(COCO_KEYPOINTS)      # 17

#: The 19 bones, as COCO-17 index pairs. A "bone" is the vector between two joints, and
#: feeding it alongside the joints (the JnB in the checkpoint's name) is what lets the
#: model see limb *orientation* without having to infer it from absolute positions.
BONE_PAIRS = (
    (0, 1), (0, 2), (1, 2), (1, 3), (2, 4),      # head
    (3, 5), (4, 6),                              # ears to shoulders
    (5, 7), (7, 9), (6, 8), (8, 10),             # arms
    (5, 6), (5, 11), (6, 12), (11, 12),          # torso
    (11, 13), (13, 15), (12, 14), (14, 16),      # legs
)

NUM_BONES = len(BONE_PAIRS)              # 19

#: Per-player feature width fed to the model: (17 joints + 19 bones) x (x, y).
IN_DIM = (NUM_KEYPOINTS + NUM_BONES) * 2  # 72

#: COCO indices of the ankles — the joints a player's court position is read from.
L_ANKLE, R_ANKLE = 15, 16
