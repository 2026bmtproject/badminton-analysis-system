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


# --------------------------------------------------------------------------- #
# Frame timeline integrity
# --------------------------------------------------------------------------- #

#: How far a file's average frame rate may sit from its nominal one before its decode
#: order stops tracking its timeline. A healthy file whose container rounds the duration
#: lands within ~1e-7 of nominal; a download that lost fragments is off by ~1e-2. 0.1%
#: sits between them by three orders of magnitude, and already means ~3.6s of drift per
#: hour -- well past the point where a frame index still names one moment.
MAX_FRAME_RATE_DRIFT = 0.001


def _rational(text: str) -> float:
    """Parse an ffprobe rational such as ``25/1``; 0.0 when it is N/A or malformed."""
    numerator, _, denominator = text.strip().partition("/")
    try:
        return float(numerator) / float(denominator or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def frame_timeline_fault(
    nominal_fps: float,
    average_fps: float,
    nb_frames: int = 0,
    duration: float = 0.0,
) -> str | None:
    """Describe a hole in a video's frame timeline, or None when it is intact.

    A sound broadcast file holds one frame per slot of its nominal frame rate, so the
    Nth decoded frame sits at N/fps seconds. Every stage here leans on that twice over:
    it counts frames while decoding forward, and it seeks back to them with OpenCV's
    ``CAP_PROP_POS_FRAMES``, which is a *timestamp* seek underneath. A download that
    lost fragments keeps the timestamps of the frames that survived, so the timeline
    grows holes and those two addressings drift apart -- silently, since every frame
    still decodes fine. Boundaries then land beside their cuts, and a later stage
    handed frame N reads whatever moment N/fps now points at, hundreds of frames away.

    ffprobe sees this without decoding anything: ``avg_frame_rate`` is frames over
    duration, so it falls below the nominal ``r_frame_rate`` by exactly the fraction of
    the timeline that is missing.
    """
    if nominal_fps <= 0 or average_fps <= 0:
        return None
    drift = (nominal_fps - average_fps) / nominal_fps
    if drift <= MAX_FRAME_RATE_DRIFT:
        return None

    expected = round(duration * nominal_fps) if duration > 0 else 0
    if expected > 0 and nb_frames > 0:
        shortfall = (
            f"its timeline is {expected} frames long but the file carries {nb_frames}, "
            f"so {expected - nb_frames} are missing"
        )
    else:
        shortfall = (
            f"it averages {average_fps:.4f} fps against a nominal {nominal_fps:g}, "
            "so frames are missing from its timeline"
        )
    return (
        f"{shortfall} ({drift:.2%} of the match).\n"
        "Decode order no longer tracks the timestamps every stage seeks by, so frame "
        "indices\nwould mean a different moment to each reader -- by hundreds of frames "
        "later in the file.\n"
        "Re-download the source. Failing that, re-encode a constant-rate copy, which "
        "freezes\nthe missing spans rather than dropping them, and re-run every stage:\n"
        f"    ffmpeg -i IN.mp4 -fps_mode cfr -r {nominal_fps:g} -c:v libx264 -crf 20 OUT.mp4"
    )


def probe_frame_timeline_fault(video_path: str) -> str | None:
    """:func:`frame_timeline_fault` for a file on disk, as ffprobe reads it.

    None also when ffprobe cannot judge -- it is not installed, or the file exposes no
    video stream. This is a guard against a plausible file that misreports its frames,
    not the gate deciding a file is readable at all; an unopenable one fails loudly at
    the first stage that tries.
    """
    if not has_tool("ffprobe"):
        return None
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,avg_frame_rate,nb_frames,duration",
            "-of", "default=noprint_wrappers=1",
            video_path,
        ],
        capture_output=True,
        **_DECODE,
    )
    if result.returncode != 0:
        return None

    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        fields[key.strip()] = value.strip()

    try:
        nb_frames = int(fields.get("nb_frames", ""))
    except ValueError:
        nb_frames = 0
    try:
        duration = float(fields.get("duration", ""))
    except ValueError:
        duration = 0.0

    return frame_timeline_fault(
        _rational(fields.get("r_frame_rate", "")),
        _rational(fields.get("avg_frame_rate", "")),
        nb_frames,
        duration,
    )
