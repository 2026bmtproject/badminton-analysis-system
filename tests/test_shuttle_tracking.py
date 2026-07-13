"""Unit tests for the shuttle_tracking stage.

Everything here runs on synthetic heatmaps and synthetic candidate sets — no
video, no checkpoints, no GPU. The two neural nets are covered only through the
pure functions around them (the mask they are fed, the windows they are batched
into), which is where this stage's own logic actually lives.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from modules.shuttle_tracking import blob, heatmap_cache, track_inpaint, track_viterbi
from modules.shuttle_tracking.inference import nonoverlap_windows, sliding_windows
from modules.shuttle_tracking.module import _to_records
from modules.shuttle_tracking.track_viterbi import ViterbiConfig, scale_params

HM_W, HM_H = 512, 288
IMG_SHAPE = (1920, 1080)


def make_heatmaps(spots: list[tuple[int, int, int] | None], radius: int = 3) -> np.ndarray:
    """One heatmap per entry: a bright disc at (x, y) with peak value, or blank."""
    heatmaps = np.zeros((len(spots), HM_H, HM_W), dtype=np.uint8)
    for t, spot in enumerate(spots):
        if spot is None:
            continue
        x, y, peak = spot
        ys, xs = np.ogrid[:HM_H, :HM_W]
        disc = (xs - x) ** 2 + (ys - y) ** 2 <= radius**2
        heatmaps[t][disc] = peak
    return heatmaps


# --------------------------------------------------------------------------- #
# blob: heatmap -> one position per frame
# --------------------------------------------------------------------------- #


def test_baseline_track_scales_to_source_pixels():
    heatmaps = make_heatmaps([(256, 144, 255)])
    xy, conf = blob.baseline_track(heatmaps, IMG_SHAPE)

    # Heatmap centre -> image centre: the 512x288 grid maps onto 1920x1080.
    assert xy[0] == pytest.approx([960, 540], abs=5)
    assert conf[0] == pytest.approx(1.0)


def test_baseline_track_marks_missing_frames_with_nan():
    heatmaps = make_heatmaps([(100, 100, 255), None])
    xy, conf = blob.baseline_track(heatmaps, IMG_SHAPE)

    assert not np.isnan(xy[0, 0])
    assert np.isnan(xy[1, 0])  # NaN, not (0, 0) — the origin is a real coordinate
    assert conf[1] == 0.0


def test_baseline_track_ignores_response_below_threshold():
    heatmaps = make_heatmaps([(100, 100, 100)])  # ~0.39 confidence
    xy, _ = blob.baseline_track(heatmaps, IMG_SHAPE, threshold=0.5)
    assert np.isnan(xy[0, 0])

    xy, _ = blob.baseline_track(heatmaps, IMG_SHAPE, threshold=0.3)
    assert not np.isnan(xy[0, 0])


def test_baseline_track_picks_the_largest_blob():
    heatmaps = np.zeros((1, HM_H, HM_W), dtype=np.uint8)
    heatmaps[0, 40:44, 40:44] = 255      # small
    heatmaps[0, 200:220, 300:320] = 200  # larger, slightly dimmer -> wins on area
    xy, _ = blob.baseline_track(heatmaps, IMG_SHAPE)

    w_scale, h_scale = blob.image_scaler(IMG_SHAPE)
    assert xy[0] == pytest.approx([310 * w_scale, 210 * h_scale], abs=5)


# --------------------------------------------------------------------------- #
# track_inpaint: which gaps deserve repair
# --------------------------------------------------------------------------- #


def _track_with_gap(gap: slice, y: float, length: int = 20) -> np.ndarray:
    xy = np.column_stack([np.linspace(100, 900, length), np.full(length, y)])
    xy[gap] = np.nan
    return xy


def test_inpaint_mask_marks_a_gap_well_inside_the_frame():
    xy = _track_with_gap(slice(8, 12), y=600.0)
    mask = track_inpaint.inpaint_mask(xy, img_height=1080)

    assert mask[8:12].tolist() == [1, 1, 1, 1]
    assert mask.sum() == 4  # nothing else touched


def test_inpaint_mask_leaves_a_gap_at_the_top_of_the_frame_alone():
    # Both ends of the gap sit above 5% of frame height: the shuttle flew out of view.
    xy = _track_with_gap(slice(8, 12), y=20.0)
    mask = track_inpaint.inpaint_mask(xy, img_height=1080)

    assert mask.sum() == 0


def test_inpaint_mask_judges_a_leading_gap_by_its_right_hand_side():
    xy = _track_with_gap(slice(0, 5), y=600.0)
    assert track_inpaint.inpaint_mask(xy, img_height=1080)[0:5].tolist() == [1] * 5

    xy = _track_with_gap(slice(0, 5), y=20.0)
    assert track_inpaint.inpaint_mask(xy, img_height=1080).sum() == 0


def test_inpaint_mask_never_marks_a_trailing_gap():
    # Nothing anchors the right-hand side, so there is no flight to interpolate.
    xy = _track_with_gap(slice(15, 20), y=600.0)
    assert track_inpaint.inpaint_mask(xy, img_height=1080).sum() == 0


def test_inpaint_windows_pad_a_short_sequence_by_repeating_the_last_frame():
    windows = track_inpaint._windows(num_frames=3, seq_len=5, step=5)
    assert windows.tolist() == [[0, 1, 2, 2, 2]]


# --------------------------------------------------------------------------- #
# track_viterbi
# --------------------------------------------------------------------------- #


def test_extract_candidates_finds_several_blobs_per_frame():
    heatmaps = np.zeros((1, HM_H, HM_W), dtype=np.uint8)
    heatmaps[0, 100:104, 100:104] = 250
    heatmaps[0, 200:204, 300:304] = 60
    cfg = ViterbiConfig()

    candidates = track_viterbi.extract_candidates(heatmaps, IMG_SHAPE, cfg)[0]

    assert len(candidates) == 2
    assert candidates[0][2] > candidates[1][2]  # strongest first


def test_viterbi_prefers_a_consistent_path_over_brighter_incoherent_detections():
    # A shuttle drifting steadily right, shadowed every frame by a brighter blob that
    # teleports across the court. Picking the bright ones would need the shuttle to
    # move 1700 px between frames, so the dimmer, physically plausible path wins.
    candidates = [
        [(100.0 + 30 * t, 500.0, 0.7), (1800.0 if t % 2 else 100.0, 100.0, 0.95)]
        for t in range(10)
    ]
    cfg = scale_params(ViterbiConfig(), IMG_SHAPE[1], fps=30.0)

    track = track_viterbi.viterbi_select(candidates, cfg)

    assert len(track) == 10
    for t in range(10):
        assert track[t][0] == pytest.approx(100.0 + 30 * t)


def test_prune_drops_points_with_no_confident_detection_nearby():
    xy = np.column_stack([np.arange(30, dtype=float) * 10, np.full(30, 500.0)])
    conf = np.full(30, 0.2)
    conf[10:15] = 0.9  # the only confident stretch
    cfg = scale_params(ViterbiConfig(), IMG_SHAPE[1], fps=30.0)

    pruned, pruned_conf = track_viterbi.prune_track(xy, conf, cfg)

    kept = ~np.isnan(pruned[:, 0])
    assert kept[10:15].all()          # the anchors themselves
    assert kept[6:10].all()           # within anchor_window of one
    assert not kept[:5].any()         # far from any anchor: hallucination
    assert not kept[20:].any()
    assert pruned_conf[0] == 0.0


def test_gap_policy_fills_short_gaps_but_not_long_slow_ones():
    xy = np.column_stack([np.arange(60, dtype=float) * 2, np.full(60, 500.0)])
    xy[10:13] = np.nan   # short gap
    xy[30:50] = np.nan   # long gap, and the shuttle is crawling at 2 px/frame
    cfg = scale_params(ViterbiConfig(), IMG_SHAPE[1], fps=30.0)
    filled = track_viterbi.fill_linear(xy, np.full(60, 0.8), cfg)

    out = track_viterbi.apply_gap_policy(xy, filled, cfg)

    assert not np.isnan(out[10:13, 0]).any()  # short: filled
    assert np.isnan(out[30:50, 0]).all()      # long + slow: not a flight, erased


def test_gap_policy_keeps_a_long_gap_when_the_shuttle_is_flying():
    xy = np.column_stack([np.arange(60, dtype=float) * 60, np.full(60, 500.0)])
    xy[30:45] = np.nan  # long gap, but 60 px/frame either side: mid-flight occlusion
    cfg = scale_params(ViterbiConfig(), IMG_SHAPE[1], fps=30.0)
    filled = track_viterbi.fill_linear(xy, np.full(60, 0.8), cfg)

    out = track_viterbi.apply_gap_policy(xy, filled, cfg)

    assert not np.isnan(out[30:45, 0]).any()


def test_gap_policy_never_fills_across_the_top_of_the_frame():
    xy = np.column_stack([np.arange(30, dtype=float) * 60, np.full(30, 50.0)])
    xy[10:14] = np.nan  # short gap, but both ends are near the top edge
    cfg = scale_params(ViterbiConfig(), IMG_SHAPE[1], fps=30.0)
    filled = track_viterbi.fill_linear(xy, np.full(30, 0.8), cfg)

    out = track_viterbi.apply_gap_policy(xy, filled, cfg)

    assert np.isnan(out[10:14, 0]).all()  # the shuttle left the frame, it did not fly


def test_baseline_candidate_is_added_only_where_it_is_new():
    cfg = ViterbiConfig()
    candidates = [[(500.0, 500.0, 0.9)], [(500.0, 500.0, 0.9)]]
    xy_base = np.array([[505.0, 505.0], [900.0, 300.0]])  # duplicate, then genuinely new

    out = track_viterbi.add_baseline_candidates(candidates, xy_base, cfg)

    assert len(out[0]) == 1  # within baseline_dedupe of the existing candidate
    assert len(out[1]) == 2
    assert out[1][1] == (900.0, 300.0, cfg.baseline_conf)


# --------------------------------------------------------------------------- #
# Resolution / frame-rate normalization
# --------------------------------------------------------------------------- #


def test_scale_params_is_identity_at_the_reference_resolution_and_fps():
    cfg = ViterbiConfig()
    assert scale_params(cfg, 1080, 30.0) == cfg


def test_scale_params_shrinks_px_limits_at_lower_resolution():
    scaled = scale_params(ViterbiConfig(), img_height=540, fps=30.0)

    assert scaled.max_speed == pytest.approx(60.0)     # half the pixels, half the speed
    assert scaled.top_margin == pytest.approx(56.0)
    assert scaled.max_gap == 6                         # a frame count: time did not change


def test_scale_params_trades_speed_for_frames_at_higher_fps():
    # At 60 fps the shuttle covers half the distance between frames, but a gap of the
    # same real duration spans twice as many of them.
    scaled = scale_params(ViterbiConfig(), img_height=1080, fps=60.0)

    assert scaled.max_speed == pytest.approx(60.0)
    assert scaled.min_speed == pytest.approx(2.0)
    assert scaled.max_gap == 12
    assert scaled.long_gap == 80


# --------------------------------------------------------------------------- #
# Inference windowing
# --------------------------------------------------------------------------- #


def test_nonoverlap_windows_cover_every_frame_including_the_tail():
    windows = nonoverlap_windows(num_frames=10, seq_len=4)

    covered = {f for w in windows for f in w}
    assert covered == set(range(10))  # the reference implementation drops frames 8-9
    assert all(len(w) == 4 for w in windows)


def test_nonoverlap_windows_pad_a_segment_shorter_than_one_window():
    assert nonoverlap_windows(num_frames=3, seq_len=5) == [[0, 1, 2, 2, 2]]


def test_sliding_windows_step_one_frame_at_a_time():
    windows = sliding_windows(num_frames=6, seq_len=4)
    assert windows == [[0, 1, 2, 3], [1, 2, 3, 4], [2, 3, 4, 5]]


# --------------------------------------------------------------------------- #
# Running on someone else's machine: device choice, VRAM, RAM
# --------------------------------------------------------------------------- #


def test_auto_batch_size_scales_with_free_vram(monkeypatch):
    import torch

    from modules.shuttle_tracking import tracknet

    cuda = torch.device("cuda")
    monkeypatch.setattr(tracknet, "free_vram_gb", lambda d: 8.0)
    assert tracknet.auto_batch_size(cuda) == tracknet.MAX_AUTO_BATCH  # roomy card

    monkeypatch.setattr(tracknet, "free_vram_gb", lambda d: 4.0)
    assert tracknet.auto_batch_size(cuda) == 4  # a 4 GB card must not ask for 8

    monkeypatch.setattr(tracknet, "free_vram_gb", lambda d: 1.2)
    assert tracknet.auto_batch_size(cuda) == 1  # never below one


def test_auto_batch_size_stays_small_on_cpu():
    import torch

    from modules.shuttle_tracking import tracknet

    assert tracknet.auto_batch_size(torch.device("cpu")) == 2


class _OOMModel:
    """A model that runs out of memory above ``limit`` samples, like a small GPU."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.batches: list[int] = []

    def __call__(self, x):
        import torch

        if len(x) > self.limit:
            raise torch.cuda.OutOfMemoryError("out of memory")
        self.batches.append(len(x))
        return torch.zeros(len(x), 2, 4, 4)


def _fake_net(model):
    import torch

    from modules.shuttle_tracking.tracknet import LoadedTrackNet

    return LoadedTrackNet(model=model, seq_len=2, bg_mode="concat", device=torch.device("cpu"))


def test_forward_halves_a_batch_that_runs_out_of_vram():
    import torch

    from modules.shuttle_tracking.inference import forward

    model = _OOMModel(limit=2)  # a GPU that can only hold 2 samples
    out = forward(_fake_net(model), torch.zeros(8, 3, 4, 4))

    assert out.shape[0] == 8          # every sample still came back
    assert max(model.batches) <= 2    # after halving down to what fits
    assert sum(model.batches) == 8


def test_forward_explains_itself_when_even_one_sample_will_not_fit():
    import torch

    from modules.shuttle_tracking.inference import forward

    model = _OOMModel(limit=0)  # nothing fits at all

    with pytest.raises(RuntimeError, match="--device cpu"):
        forward(_fake_net(model), torch.zeros(4, 3, 4, 4))


def test_median_samples_a_long_segment_instead_of_copying_all_of_it():
    from modules.shuttle_tracking.inference import MEDIAN_SAMPLES, compute_median

    # A static background with a bright object drifting through it: the median must
    # recover the background, and must do so without medianing every frame.
    frames = np.full((MEDIAN_SAMPLES * 4, 288, 512, 3), 40, dtype=np.uint8)
    for t in range(len(frames)):
        frames[t, t % 200, t % 300] = 255

    median = compute_median(frames, "concat")

    assert median.shape == (3, 288, 512)
    assert (median == 40).all()


# --------------------------------------------------------------------------- #
# Heatmap cache
# --------------------------------------------------------------------------- #


@pytest.fixture
def checkpoint(tmp_path):
    path = tmp_path / "TrackNet_fake.pt"
    path.write_bytes(b"weights")
    return path


def _meta(checkpoint, tmp_path, segments=((0, 100),), eval_mode="nonoverlap", chunk_frames=1200):
    return heatmap_cache.build_meta(
        checkpoint=checkpoint,
        eval_mode=eval_mode,
        chunk_frames=chunk_frames,
        video=tmp_path / "match.mp4",
        segments=[{"start_frame": a, "end_frame": b} for a, b in segments],
    )


def test_cache_is_reused_when_nothing_it_depends_on_changed(tmp_path, checkpoint):
    meta = _meta(checkpoint, tmp_path)

    assert heatmap_cache.prepare(tmp_path, meta) is False  # first time: built empty
    assert heatmap_cache.prepare(tmp_path, meta) is True   # second time: reused


def test_cache_is_wiped_when_the_checkpoint_changes(tmp_path, checkpoint):
    heatmap_cache.prepare(tmp_path, _meta(checkpoint, tmp_path))
    stale = heatmap_cache.segment_file(tmp_path, 0)
    stale.write_bytes(b"old heatmaps")

    checkpoint.write_bytes(b"different weights")
    reused = heatmap_cache.prepare(tmp_path, _meta(checkpoint, tmp_path))

    assert reused is False
    assert not stale.exists()


def test_cache_is_wiped_when_the_segments_change(tmp_path, checkpoint):
    heatmap_cache.prepare(tmp_path, _meta(checkpoint, tmp_path, segments=((0, 100),)))
    reused = heatmap_cache.prepare(
        tmp_path, _meta(checkpoint, tmp_path, segments=((0, 100), (200, 300)))
    )
    assert reused is False


def test_cache_is_wiped_when_the_eval_mode_changes(tmp_path, checkpoint):
    heatmap_cache.prepare(tmp_path, _meta(checkpoint, tmp_path))
    reused = heatmap_cache.prepare(tmp_path, _meta(checkpoint, tmp_path, eval_mode="weight"))
    assert reused is False


def test_cache_is_wiped_when_the_chunk_size_changes(tmp_path, checkpoint):
    # Windows cannot span a chunk boundary, so the chunk size shows through in the
    # heatmaps of any segment long enough to be split.
    heatmap_cache.prepare(tmp_path, _meta(checkpoint, tmp_path))
    reused = heatmap_cache.prepare(tmp_path, _meta(checkpoint, tmp_path, chunk_frames=600))
    assert reused is False


def test_meta_records_the_store_threshold_so_a_lower_one_invalidates(tmp_path, checkpoint):
    heatmap_cache.prepare(tmp_path, _meta(checkpoint, tmp_path))
    written = json.loads(
        (heatmap_cache.heatmap_dir(tmp_path) / heatmap_cache.META_FILENAME).read_text()
    )
    assert written["store_threshold"] == heatmap_cache.STORE_THRESHOLD


def test_saved_heatmaps_are_sparsified_but_keep_everything_readable(tmp_path):
    heatmaps = np.zeros((2, HM_H, HM_W), dtype=np.uint8)
    heatmaps[0, 10, 10] = heatmap_cache.STORE_THRESHOLD - 1  # noise floor
    heatmaps[0, 20, 20] = 200                                # a real response
    path = tmp_path / "seg0000.npz"

    heatmap_cache.save_segment(path, heatmaps, IMG_SHAPE)
    loaded, img_shape = heatmap_cache.load_segment(path)

    assert img_shape == IMG_SHAPE
    assert loaded[0, 10, 10] == 0    # dropped: no consumer reads below the threshold
    assert loaded[0, 20, 20] == 200  # kept exactly


def test_sparsifying_makes_a_noisy_heatmap_compress(tmp_path):
    rng = np.random.default_rng(0)
    # The raw sigmoid response is noisy everywhere, which is what makes a dense
    # heatmap so expensive to store.
    heatmaps = rng.integers(0, heatmap_cache.STORE_THRESHOLD, (30, HM_H, HM_W), dtype=np.uint8)
    heatmaps[:, 100:105, 100:105] = 255
    path = tmp_path / "seg0000.npz"

    heatmap_cache.save_segment(path, heatmaps, IMG_SHAPE)

    dense_bytes = heatmaps.nbytes
    assert path.stat().st_size < dense_bytes / 50


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


def test_only_heatmap_leaves_the_stage_pending(tmp_path, monkeypatch):
    """Warming the cache is not finishing the stage: no artifact, so not COMPLETED.

    Marking it complete would make the runner skip it forever, and the match would
    never get a shuttle.json.
    """
    from modules.base import StageStatus, read_status
    from modules.contracts import stage_path
    from modules.shuttle_tracking.module import ShuttleTrackingModule

    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "match.mp4").write_bytes(b"")
    segments_dir = stage_path(tmp_path, "match_segmentation")
    segments_dir.mkdir(parents=True)
    (segments_dir / "segments.json").write_text(
        json.dumps({"fps": 25.0, "segments": [{"start_frame": 0, "end_frame": 10}]})
    )

    module = ShuttleTrackingModule()
    monkeypatch.setattr(module, "build_heatmaps", lambda *a, **k: None)

    module.run(tmp_path, only_heatmap=True)

    state = read_status(stage_path(tmp_path, "shuttle_tracking"))
    assert state.status == StageStatus.PENDING
    assert not module.get_output_path(tmp_path).exists()


def test_records_use_absolute_frame_indices():
    xy = np.array([[10.0, 20.0], [np.nan, np.nan]])
    conf = np.array([0.8, 0.0])

    records = _to_records(xy, conf, start_frame=1000, segment_index=3, method="viterbi")

    assert [r.frame for r in records] == [1000, 1001]
    assert records[0].segment_index == 3
    assert records[0].method == "viterbi"
    assert (records[0].x, records[0].y, records[0].visible) == (10.0, 20.0, True)
    assert (records[1].x, records[1].y, records[1].visible) == (None, None, False)
