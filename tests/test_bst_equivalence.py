"""The port did not change what BST predicts.

``modules.common.bst`` is a rewrite of ``bst_infer_standalone.py`` (still in the repo
root, along with ``bst_dense_scan.py`` and ``derive_side.py``, as the reference the two
new stages are being built against). The rewrite drops the CSV plumbing and generalizes
the windowing, but the numbers coming out of the model must not move by so much as a
float: ``event_detection``'s thresholds — side margins, guard widths, lock regions — were
all tuned against *these* probabilities, and a silently altered normalization would make
every one of those numbers wrong in a way no test of the stage itself could catch.

So this compares the two implementations on the same weights and the same geometry, and
demands they agree. It is a migration test: **delete it when the root scripts go**, and
drop the ``pandas`` dev dependency with it.
"""

from __future__ import annotations

import numpy as np
import pytest
import torch

from modules.common.bst import SegmentFeatures, build_bst_model, predict_windows
from modules.common.bst.classes import NUM_KEYPOINTS, STROKE_CLASSES

pytest.importorskip("pandas", reason="bst_infer_standalone imports pandas at module level")
import bst_infer_standalone as reference  # noqa: E402  (root script, needs the conftest path)


def reference_probabilities(model, features: SegmentFeatures, start: int, end: int) -> np.ndarray:
    """The reference's probability vector for one window, in STROKE_CLASSES order.

    ``predict_segment`` returns ranked ``(name, probability)`` pairs rather than a vector,
    so asking it for all 25 and re-indexing by name reconstructs one.
    """
    ranked = reference.predict_segment(
        model,
        features.joints[start:end],
        features.positions[start:end],
        features.shuttle[start:end],
        device="cpu",
        topk=len(STROKE_CLASSES),
    )
    by_name = dict(ranked)
    return np.array([by_name[name] for name in STROKE_CLASSES], dtype=np.float32)


@pytest.fixture(scope="module")
def model():
    # Random weights, but the *same* random weights on both sides — which is what makes
    # this a test of the feature pipeline (where the port could actually have gone wrong)
    # rather than of the checkpoint. The architecture is byte-identical by construction.
    torch.manual_seed(0)
    return build_bst_model().eval()


@pytest.fixture(scope="module")
def features():
    rng = np.random.default_rng(1)
    n = 300
    joints = rng.random((n, 2, NUM_KEYPOINTS, 2), dtype=np.float32)
    joints[joints < 0.15] = 0.0                     # missing joints, the interesting case
    positions = rng.random((n, 2, 2), dtype=np.float32)
    shuttle = rng.random((n, 2), dtype=np.float32)
    shuttle[::7] = 0.0                              # frames where the shuttle is not visible
    return SegmentFeatures(joints=joints, positions=positions, shuttle=shuttle, start_frame=0)


@pytest.mark.parametrize(
    "start, end",
    [
        (0, 30),         # short: zero-padded up to SEQ_LEN
        (0, 100),        # exactly SEQ_LEN
        (10, 147),       # long enough to be strided, and padded after striding
        (40, 290),       # 2.5x SEQ_LEN
    ],
    ids=["padded", "exact", "strided_and_padded", "strided"],
)
def test_a_window_predicts_what_the_reference_predicts(model, features, start, end):
    ours = predict_windows(model, features, [(start, end)], device="cpu")[0]
    theirs = reference_probabilities(model, features, start, end)
    assert np.allclose(ours, theirs, atol=1e-5)


def test_a_dense_scan_predicts_what_the_reference_predicts_frame_by_frame(model, features):
    """The batched path, against the reference run one window at a time.

    This is the shape ``event_detection`` uses — a window centred on every frame — and the
    batching is the part the reference does not have, so it is the part that could differ.
    """
    half, n = 15, 40
    windows = [(max(0, f - half), min(n, f + half + 1)) for f in range(n)]
    scanned = predict_windows(model, features, windows, device="cpu", batch_size=16)

    for i, (start, end) in enumerate(windows):
        assert np.allclose(scanned[i], reference_probabilities(model, features, start, end),
                           atol=1e-5), f"frame {i} diverged"
