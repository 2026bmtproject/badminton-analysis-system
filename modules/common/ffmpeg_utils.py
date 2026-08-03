"""Thin helpers around the ffmpeg / ffprobe command-line tools."""

from __future__ import annotations

import shutil
import subprocess

from modules.common import console

#: How to decode what ffmpeg and ffprobe write. Not the platform default: ffmpeg emits
#: UTF-8, and on a zh-TW Windows ``text=True`` decodes it as cp950 instead -- a single
#: byte it cannot map raises UnicodeDecodeError inside subprocess's reader *thread*,
#: which dumps a traceback into the middle of a stage and loses the output entirely.
_DECODE = {"text": True, "encoding": "utf-8", "errors": "replace"}


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
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, **_DECODE)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.error(
            "ffmpeg not found; please install it and add it to PATH.\n"
            "macOS:   brew install ffmpeg\n"
            "Ubuntu:  sudo apt install ffmpeg\n"
            "Windows: https://ffmpeg.org/download.html"
        )
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
        **_DECODE,
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
        console.note(f"-> {desc}")
    result = subprocess.run(args, capture_output=True, **_DECODE)
    if result.returncode != 0:
        console.error(f"ffmpeg failed:\n{result.stderr[-800:]}")
        return False
    return True
