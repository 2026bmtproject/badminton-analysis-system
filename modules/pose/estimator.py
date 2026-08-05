"""Two-stage pose inference: a person detector (YOLOX) feeding RTMPose, both ONNX.

Top-down, which is why it is worth two models: the detector finds each person, and
RTMPose then re-estimates the skeleton on a *crop* blown back up to 192x256, so a
player 150 px tall in a broadcast frame is posed at full model resolution. RTMPose
un-warps its output, so every coordinate this module returns is already in original
video pixels.

Both models are ONNX and are fetched and cached by rtmlib on first use — there is
nothing to put in ``models/``.

GPU
---
onnxruntime reaches the GPU only if it can load CUDA and cuDNN DLLs, and it will
*silently run on the CPU* when it cannot (twice: once when the provider fails to
build, and again if a kernel fails at run time and the session falls back). Both
failures cost a few times the runtime and look exactly like success. So this module

* points onnxruntime at the CUDA DLLs torch already ships (:func:`enable_cuda_dlls`),
* checks the session's actual providers rather than what was asked for, and
* is explicit about what it ended up on — falling back to the CPU with a loud warning
  when the device was chosen automatically, and refusing to start when the caller
  demanded ``cuda`` and did not get it.

Keeping the GPU busy
--------------------
The pre- and post-processing around the two ``session.run`` calls is all CPU, and on a
fast card it outweighs the inference it feeds — so :meth:`TwoStagePoseEstimator._pose`
batches a frame's crops into one run instead of one per person, normalizes in float32
rather than letting rtmlib promote every crop to float64, and ``__call__`` is safe to
call from several threads at once (:mod:`modules.pose.module` is what uses that).

Both change the order and precision of the floats entering the model, so under 1% of
keypoint coordinates shift by a SimCC bin or two (~0.5 px, the model's own resolution).
Measured over four rallies the stage still picked the same person in all 2446
(frame, player) slots, which is why the cache key is left alone — bumping it would
discard every existing GPU pass.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Callable

import numpy as np

from modules.common import console

#: Set this to any non-empty value to let onnxruntime's own warnings through. They are
#: silenced by default -- they land in the middle of the stage's report and none of them
#: is actionable on a working machine -- but on one where the CUDA provider half-loads
#: they name the DLL that failed, which nothing in this system can work out for itself.
ORT_VERBOSE_ENV = "BADMINTON_ORT_VERBOSE"

#: Maps the detected boxes to a mask of which ones deserve a skeleton. See
#: ``modules.pose.select.candidate_mask``.
KeepFn = Callable[[np.ndarray], np.ndarray]


def ort_verbose() -> bool:
    """Whether the user asked to see onnxruntime's own diagnostics."""
    return bool(os.environ.get(ORT_VERBOSE_ENV, "").strip())

#: RTMPose ONNX checkpoints (body7-trained, so they generalize to broadcast footage).
#: mode -> (url, (width, height)). Bigger input = more accurate, slower.
POSE_MODELS = {
    "lightweight": (
        "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
        "rtmpose-t_simcc-body7_pt-body7_420e-256x192-026a1439_20230504.zip",
        (192, 256),
    ),
    "balanced": (
        "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
        "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504.zip",
        (192, 256),
    ),
    "performance": (
        "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
        "rtmpose-l_simcc-body7_pt-body7_420e-384x288-3f5a1437_20230504.zip",
        (288, 384),
    ),
}
POSE_MODES = tuple(POSE_MODELS)

#: rtmlib's bundled person detector (YOLOX-m, Human-Art trained).
DET_MODEL = (
    "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
    "yolox_m_8xb8-300e_humanart-c2c7a14a.zip"
)
DET_INPUT = (640, 640)

NUM_KEYPOINTS = 17

#: Properties of the published checkpoints, not choices; they match rtmlib, and are
#: named here only because ``_pose`` does its own pre- and post-processing to batch.
BBOX_PADDING = 1.25
SIMCC_SPLIT_RATIO = 2.0
POSE_MEAN = np.array((123.675, 116.28, 103.53), np.float32)
POSE_STD = np.array((58.395, 57.12, 57.375), np.float32)

#: A VRAM ceiling for the pathological frame, not a tuning knob: the candidate band
#: admits two to six people in practice.
MAX_POSE_BATCH = 16

_dlls_enabled = False


def enable_cuda_dlls() -> None:
    """Put the CUDA/cuDNN DLLs on the search path so the CUDA provider can load.

    onnxruntime-gpu does not ship CUDA itself; on Windows it needs ``cudnn64_9.dll``,
    ``cublas64_12.dll`` and friends to be *findable*, and they are not on the default
    DLL search path. Two places have them, and both are added:

    * ``torch/lib`` — torch's CUDA build bundles a complete set, which is why this
      project needs nothing extra installed: if torch sees the GPU, so does ORT.
    * ``site-packages/nvidia/*/bin`` — where the standalone ``nvidia-*`` wheels put
      them, for an environment that has those instead.

    Idempotent, and safe to call on a machine with neither (it simply finds nothing).
    """
    global _dlls_enabled
    if _dlls_enabled:
        return
    _dlls_enabled = True

    import site

    dirs: list[str] = []
    try:
        import torch

        torch_lib = Path(torch.__file__).parent / "lib"
        if torch_lib.is_dir():
            dirs.append(str(torch_lib))
    except ImportError:
        pass

    for packages in site.getsitepackages():
        dirs.extend(sorted(glob.glob(os.path.join(packages, "nvidia", "*", "bin"))))

    for directory in dirs:
        if hasattr(os, "add_dll_directory"):  # Windows only
            os.add_dll_directory(directory)
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ["PATH"]

    import onnxruntime as ort

    if hasattr(ort, "preload_dlls"):
        # Announces "Skip loading CUDA and cuDNN DLLs since torch is imported" on the way
        # through. Whether it loaded them is not what decides the device -- _resolve
        # settles that by building a session and asking what provider it got.
        with console.muted():
            ort.preload_dlls()


def cuda_available() -> bool:
    """True if onnxruntime was built with the CUDA provider (it may still fail to load)."""
    import onnxruntime as ort

    return "CUDAExecutionProvider" in ort.get_available_providers()


def _on_gpu(tool) -> bool:
    """Did this rtmlib tool's session actually get a GPU provider?"""
    session = getattr(tool, "session", None)
    if session is None:
        return False
    return any(ep.startswith(("CUDA", "Tensorrt")) for ep in session.get_providers())


class TwoStagePoseEstimator:
    """YOLOX + RTMPose. ``__call__(frame_bgr)`` returns every person in the frame::

        {"kps": (n, 17, 2), "scores": (n, 17), "bboxes": (n, 4)}

    all in original-frame pixels. Selecting *which* of those people are the two
    players is a separate concern — see :mod:`modules.pose.select`.

    Immutable once built, so several threads may share one.
    """

    def __init__(
        self,
        pose_mode: str = "balanced",
        device: str | None = None,      # None / "auto" -> GPU if it really works
        backend: str = "onnxruntime",
        person_min_area: float = 0.0,
    ) -> None:
        if pose_mode not in POSE_MODELS:
            raise ValueError(f"unknown pose_mode {pose_mode!r}; expected any of {POSE_MODES}")
        self.pose_mode = pose_mode
        self.person_min_area = person_min_area  # fraction of frame area; 0 = keep all

        # Loading two ONNX models prints six lines of rtmlib and onnxruntime chatter into
        # the middle of the stage's report. None of it is actionable: a provider that
        # fails to load shows up in _on_gpu below, which then says so in this system's own
        # words. Only the *construction* is muted — _resolve's NO GPU IN USE warning is
        # the one thing here nobody may miss, so it must stay outside.
        with console.muted():
            import onnxruntime as ort

            # ORT writes from C++, so console.muted() cannot reach it and the severity
            # is the only lever. It is a process-global one, hence the escape hatch:
            # on a machine where CUDA half-works, ORT's own warning is the only thing
            # that names the DLL that failed.
            ort.set_default_logger_severity(2 if ort_verbose() else 3)  # 2 = WARNING
            from rtmlib import RTMPose, YOLOX

        pose_url, pose_input = POSE_MODELS[pose_mode]

        def build_pose(on: str):
            with console.muted():
                return RTMPose(onnx_model=pose_url, model_input_size=pose_input,
                               backend=backend, device=on)

        # Building the session is what actually loads the CUDA DLLs, and loading them is
        # where it usually fails — so the device is settled by *trying* it, on the model
        # we were going to build anyway.
        self.device, self.pose = self._resolve(device, backend, build_pose)

        with console.muted():
            self.det = YOLOX(
                onnx_model=DET_MODEL, model_input_size=DET_INPUT,
                backend=backend, device=self.device,
            )
        if self.device == "cuda":
            if not _on_gpu(self.det):
                raise RuntimeError(
                    "the pose model reached the GPU but the detector did not: "
                    f"{self.det.session.get_providers()}"
                )
            for tool in (self.pose, self.det):
                # Prefer a crash over a session that quietly re-runs on the CPU when a
                # kernel fails — that path just looks like everything being 3x slower.
                tool.session.disable_fallback()

        self._pose_input = self.pose.session.get_inputs()[0].name
        self._pose_outputs = [out.name for out in self.pose.session.get_outputs()]

    @staticmethod
    def _resolve(requested: str | None, backend: str, build_pose) -> tuple[str, object]:
        """Settle on a device, returning it with the pose model already built on it.

        ``None``/``"auto"`` means "the GPU if it genuinely works, else the CPU with a
        warning" — the point being that a machine with no working CUDA setup still
        finishes the stage. An explicit ``"cuda"`` means the caller wants to be told when
        it does not, so there a failure raises rather than quietly costing them 3x the
        runtime.
        """
        explicit_cuda = requested == "cuda"
        if requested not in (None, "auto", "cuda"):
            # "cpu", or an openvino/opencv device the caller knows about.
            return requested, build_pose(requested)

        if backend != "onnxruntime":
            if explicit_cuda:
                raise RuntimeError(f"device='cuda' needs backend='onnxruntime', not {backend!r}")
            return "cpu", build_pose("cpu")

        enable_cuda_dlls()
        if not cuda_available():
            if explicit_cuda:
                raise RuntimeError(
                    "device='cuda' but this onnxruntime has no CUDA provider — you are "
                    "probably on the CPU-only `onnxruntime` package instead of "
                    "`onnxruntime-gpu`."
                )
            _warn_cpu("onnxruntime has no CUDA execution provider")
            return "cpu", build_pose("cpu")

        pose = build_pose("cuda")
        if _on_gpu(pose):
            return "cuda", pose

        reason = (
            "the CUDA provider failed to load its DLLs (onnxruntime-gpu >= 1.23 is built "
            "for CUDA 13 while torch ships CUDA 12 — see the pin in pyproject.toml)"
        )
        if explicit_cuda:
            raise RuntimeError(f"device='cuda' but {reason}. {_ORT_HINT}")
        _warn_cpu(reason)
        return "cpu", build_pose("cpu")

    def _detect(self, frame: np.ndarray) -> np.ndarray:
        """Every person in the frame as ``(n, 4)`` xyxy boxes."""
        bboxes = self.det(frame)
        bboxes = (
            np.asarray(bboxes, np.float32).reshape(-1, 4)
            if len(bboxes) else np.zeros((0, 4), np.float32)
        )
        if self.person_min_area > 0 and len(bboxes):
            height, width = frame.shape[:2]
            area = (bboxes[:, 2] - bboxes[:, 0]) * (bboxes[:, 3] - bboxes[:, 1])
            bboxes = bboxes[area >= self.person_min_area * width * height]
        return bboxes

    def _pose(self, frame: np.ndarray, bboxes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Skeletons for ``bboxes``: ``(n, 17, 2)`` keypoints and ``(n, 17)`` scores.

        The crop and the SimCC decode are rtmlib's, step for step — only the batching and
        the dtype of the normalize differ from ``RTMPose.__call__``, which walks the boxes
        one at a time and so pays a kernel launch and a host-to-device copy per person.
        """
        from rtmlib.tools.pose_estimation.post_processings import get_simcc_maximum
        from rtmlib.tools.pose_estimation.pre_processings import bbox_xyxy2cs, top_down_affine

        model_input = self.pose.model_input_size
        crops, centers, scales = [], [], []
        for bbox in bboxes:
            center, scale = bbox_xyxy2cs(np.asarray(bbox, np.float64), padding=BBOX_PADDING)
            crop, scale = top_down_affine(model_input, scale, center, frame)
            crops.append(((crop.astype(np.float32) - POSE_MEAN) / POSE_STD).transpose(2, 0, 1))
            centers.append(center)
            scales.append(scale)

        parts_x, parts_y = [], []
        for start in range(0, len(crops), MAX_POSE_BATCH):
            batch = np.ascontiguousarray(crops[start : start + MAX_POSE_BATCH], np.float32)
            simcc_x, simcc_y = self.pose.session.run(
                self._pose_outputs, {self._pose_input: batch}
            )
            parts_x.append(simcc_x)
            parts_y.append(simcc_y)

        locations, scores = get_simcc_maximum(
            np.concatenate(parts_x), np.concatenate(parts_y)
        )
        # rtmlib's rescale, kept step for step so the arithmetic stays identical.
        centers = np.asarray(centers)[:, None, :]
        scales = np.asarray(scales)[:, None, :]
        keypoints = locations / SIMCC_SPLIT_RATIO
        keypoints = keypoints / model_input * scales
        return keypoints + centers - scales / 2, scores

    def __call__(self, frame: np.ndarray, keep: KeepFn | None = None) -> dict[str, np.ndarray]:
        """Detect everyone, then pose whoever ``keep`` says is worth posing.

        ``keep`` maps the ``(n, 4)`` boxes to a boolean mask and runs *between* the two
        models, which is the only place it can save anything: pose is charged per person,
        and a broadcast frame is mostly crowd. Without it, every spectator gets a
        skeleton nobody will ever read.

        Safe to call concurrently on one estimator.
        """
        bboxes = self._detect(frame)
        if len(bboxes) and keep is not None:
            bboxes = bboxes[keep(bboxes)]
        if len(bboxes) == 0:
            return empty_detection()
        kps, scores = self._pose(frame, bboxes)
        return {
            "kps": np.asarray(kps, np.float32).reshape(-1, NUM_KEYPOINTS, 2),
            "scores": np.asarray(scores, np.float32).reshape(-1, NUM_KEYPOINTS),
            "bboxes": bboxes.astype(np.float32),
        }


def empty_detection() -> dict[str, np.ndarray]:
    """A frame in which nobody was found."""
    return {
        "kps": np.zeros((0, NUM_KEYPOINTS, 2), np.float32),
        "scores": np.zeros((0, NUM_KEYPOINTS), np.float32),
        "bboxes": np.zeros((0, 4), np.float32),
    }


#: Appended wherever the GPU was wanted and not had, because the *reason* this system
#: can give is a guess about the usual cause -- onnxruntime knows the actual one.
_ORT_HINT = f"Set {ORT_VERBOSE_ENV}=1 and re-run to see onnxruntime's own diagnosis."


def _warn_cpu(reason: str) -> None:
    # Plain ASCII and impossible to miss: a silent CPU run is the failure that costs a
    # user an afternoon. Same reasoning as the warning in shuttle_tracking.
    console.warn(
        f"NO GPU IN USE - {reason}.\n"
        "RTMPose will run on the CPU: roughly 3x slower, but it will finish.\n"
        "Pass --device cuda to make this an error instead of a warning.\n"
        f"{_ORT_HINT}"
    )
