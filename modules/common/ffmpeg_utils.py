"""Thin helpers around the ffmpeg / ffprobe command-line tools."""

from __future__ import annotations

import shutil
import subprocess


def has_tool(name: str) -> bool:
    """Return True if the given executable is available on PATH."""
    return shutil.which(name) is not None


def ensure_tool(name: str) -> None:
    """Raise FileNotFoundError if the given executable is not on PATH."""
    if not has_tool(name):
        raise FileNotFoundError(
            f"{name} not found; please install ffmpeg and make sure it is on PATH"
        )


def check_ffmpeg() -> bool:
    """Check that ffmpeg is installed, printing install hints if not.

    Returns True when ffmpeg is available. Intended for CLI entry points.
    """
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("[error] ffmpeg not found; please install it and add it to PATH.")
        print("  macOS:   brew install ffmpeg")
        print("  Ubuntu:  sudo apt install ffmpeg")
        print("  Windows: https://ffmpeg.org/download.html")
        return False


def get_video_duration(video_path: str) -> float:
    """Return the video duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip() or 'could not read duration'}")

    try:
        duration = float(result.stdout.strip())
    except ValueError as e:
        raise RuntimeError("ffprobe returned an invalid duration") from e

    if duration <= 0:
        raise RuntimeError("video duration is <= 0, cannot process")
    return duration


def sec_to_ts(seconds: float) -> str:
    """Convert seconds to an HH:MM:SS.mmm timestamp string."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def run_ffmpeg(args: list[str], desc: str = "") -> bool:
    """Run an ffmpeg command; return True on success."""
    if desc:
        print(f"  -> {desc}")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  [ffmpeg error]\n{result.stderr[-800:]}")
        return False
    return True
