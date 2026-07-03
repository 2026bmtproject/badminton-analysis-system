"""Proportionally downscale a video to a target height using ffmpeg.

The smaller output is used to speed up the frame-by-frame scan performed by
the match_segmentation stage (steps 1/2). Width is derived from the height
and aligned to an even number to keep encoders happy.

CLI usage:
    python -m modules.common.downscale IN.mp4
        (downscale to 480p, writes IN_480p.mp4)
    python -m modules.common.downscale IN.mp4 --height 720
    python -m modules.common.downscale IN.mp4 OUT.mp4 --height 720 --crf 26
    python -m modules.common.downscale IN.mp4 --gpu
        (NVIDIA NVENC hardware encoding)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import cv2

from modules.common.ffmpeg_utils import ensure_tool, run_ffmpeg


def get_video_height(input_path: str) -> int:
    """Return the pixel height of ``input_path`` via OpenCV (no ffmpeg needed)."""
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {input_path}")
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return height


def scaled_video_name(stem: str, height: int, with_audio: bool = False) -> str:
    """Cache filename for a downscaled copy, keyed by height and audio track."""
    return f"{stem}_{height}p{'_audio' if with_audio else ''}.mp4"


def ensure_max_height(
    input_path: str,
    max_height: int = 480,
    workdir: str | None = None,
    *,
    crf: int = 23,
    preset: str = "veryfast",
    gpu: bool = False,
    no_audio: bool = True,
    verbose: bool = True,
) -> tuple[str, bool]:
    """Return a path to a video no taller than ``max_height``.

    If the source is already <= ``max_height`` (or ``max_height`` <= 0), the
    input path is returned unchanged. Otherwise the video is downscaled to
    ``max_height`` and cached at ``<workdir>/<name>`` (see
    :func:`scaled_video_name`; reused if already present) so reruns don't
    re-encode. ``workdir`` defaults to the source's own folder.

    Returns ``(path_to_use, downscaled)``. Audio is dropped by default since the
    common consumer is a frame-by-frame scan.
    """
    if max_height <= 0:
        return input_path, False

    height = get_video_height(input_path)
    if height <= 0 or height <= max_height:
        return input_path, False

    stem = os.path.splitext(os.path.basename(input_path))[0]
    folder = workdir if workdir is not None else os.path.dirname(input_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    name = scaled_video_name(stem, max_height, with_audio=not no_audio)
    output_path = os.path.join(folder, name) if folder else name

    if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
        if verbose:
            print(f"reuse cached {max_height}p video: {output_path}")
        return output_path, True

    if verbose:
        print(f"source is {height}p (> {max_height}p); downscaling...")
    downscale_video(
        input_path, output_path, height=max_height,
        crf=crf, preset=preset, gpu=gpu, no_audio=no_audio, verbose=verbose,
    )
    return output_path, True


def scaled_video(
    project_path: str | Path,
    height: int = 480,
    with_audio: bool = False,
    *,
    crf: int = 23,
    preset: str = "veryfast",
    gpu: bool = False,
    verbose: bool = True,
) -> Path:
    """Return a downscaled copy of the match's raw video, cached under ``cache/``.

    Project-aware convenience over :func:`ensure_max_height`: any stage can call
    this to get (and transparently reuse) a downscaled copy of the input video
    without re-encoding. Returns the original video path when the source is
    already <= ``height`` (or ``height`` <= 0). Cached at
    ``matches/{match}/cache/<stem>_<height>p[_audio].mp4``, keyed by the two
    things consumers actually differ on: target height and whether audio is kept.
    """
    # Local import: keeps modules.contracts free of the cv2/ffmpeg import chain.
    from modules.contracts import cache_dir, resolve_input_video

    src = resolve_input_video(project_path)
    out, _ = ensure_max_height(
        str(src), height, workdir=str(cache_dir(project_path)),
        crf=crf, preset=preset, gpu=gpu, no_audio=not with_audio, verbose=verbose,
    )
    return Path(out)


def build_output_path(input_path: str, output_path: str | None, height: int) -> str:
    """Derive the output path, defaulting to ``<name>_<height>p.mp4``."""
    if output_path:
        return output_path
    base = os.path.splitext(os.path.basename(input_path))[0]
    folder = os.path.dirname(input_path)
    name = f"{base}_{height}p.mp4"
    return os.path.join(folder, name) if folder else name


def build_command(
    input_path: str,
    output_path: str,
    height: int,
    crf: int,
    preset: str,
    gpu: bool,
    no_audio: bool,
) -> list[str]:
    """Build the ffmpeg command for the requested downscale settings."""
    # Scale to the target height; width -2 lets ffmpeg compute it and align to even.
    scale_filter = f"scale=-2:{height}"

    cmd: list[str] = [
        "ffmpeg",
        "-y",  # overwrite existing output
        "-i", input_path,
        "-vf", scale_filter,
    ]

    if gpu:
        # NVIDIA hardware encoding; -cq is analogous to crf (higher = lower quality).
        cmd += ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
    else:
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]

    if no_audio:
        cmd += ["-an"]
    else:
        # Copy the audio track as-is to save time.
        cmd += ["-c:a", "copy"]

    cmd += [output_path]
    return cmd


def downscale_video(
    input_path: str,
    output_path: str | None = None,
    height: int = 480,
    crf: int = 23,
    preset: str = "veryfast",
    gpu: bool = False,
    no_audio: bool = False,
    verbose: bool = True,
) -> str:
    """Downscale ``input_path`` and return the written output path."""
    ensure_tool("ffmpeg")

    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"input video not found: {input_path}")
    if height <= 0:
        raise ValueError("height must be a positive integer")

    resolved_output = build_output_path(input_path, output_path, height)
    if os.path.abspath(resolved_output) == os.path.abspath(input_path):
        raise ValueError("output path equals input path; choose a different output")

    cmd = build_command(input_path, resolved_output, height, crf, preset, gpu, no_audio)

    if verbose:
        print("=" * 72)
        print("downscale: ffmpeg video downscaling")
        print("=" * 72)
        print(f"input:  {input_path}")
        print(f"output: {resolved_output}")
        print(f"height: {height}p")
        print(f"encoder: {'h264_nvenc (GPU)' if gpu else 'libx264 (CPU)'}")
        print(f"quality: {'-cq' if gpu else '-crf'} {crf}")
        print("-" * 72)
        print("command:")
        print(" ".join(cmd))
        print("-" * 72)

    if not run_ffmpeg(cmd):
        raise RuntimeError("ffmpeg failed while downscaling")

    if verbose:
        in_size = os.path.getsize(input_path)
        out_size = os.path.getsize(resolved_output)
        print("-" * 72)
        print(f"input size:  {in_size / 1024 / 1024:.1f} MB")
        print(f"output size: {out_size / 1024 / 1024:.1f} MB")
        if in_size > 0:
            print(f"ratio:       {out_size / in_size * 100:.1f}%")
        print("done")

    return resolved_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Proportionally downscale a video to a target height (default 480p) using ffmpeg",
    )
    parser.add_argument("input_path", help="input video path")
    parser.add_argument(
        "output_path",
        nargs="?",
        default=None,
        help="output video path (default <name>_<height>p.mp4)",
    )
    parser.add_argument("--height", type=int, default=480, help="target height in px (default 480; common 480/720)")
    parser.add_argument("--crf", type=int, default=23, help="quality factor 0-51 (higher = smaller/blurrier; maps to -cq on GPU)")
    parser.add_argument("--preset", default="veryfast", help="x264 speed/compression trade-off (default veryfast)")
    parser.add_argument("--gpu", action="store_true", help="use NVIDIA NVENC hardware encoding (needs an NVIDIA GPU)")
    parser.add_argument("--no-audio", action="store_true", help="drop the audio track")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        downscale_video(
            args.input_path,
            args.output_path,
            height=args.height,
            crf=args.crf,
            preset=args.preset,
            gpu=args.gpu,
            no_audio=args.no_audio,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
