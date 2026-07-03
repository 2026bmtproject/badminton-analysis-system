"""Read badminton scoreboards per rally segment via the Google Gemini API.

For each segment we sample frames straight from the source match video (no
pre-cut segments — see ``modules.common.frame_composite.extract_frames_in_range``),
composite them to average away moving players, and ask Gemini to transcribe the
scoreboard. Several composite methods are tried in turn until both scores are
read (or the list is exhausted); the best attempt wins.

This is the pure logic behind the ``score_recognition`` stage — the
``ScoreRecognitionModule`` wrapper (see ``module.py``) adapts it to the pipeline.

CLI usage:
    # API key: $env:GEMINI_API_KEY (PowerShell) or 'gemini_api_key' in config.yaml
    python -m modules.score_recognition matches/MK_vs_CT_2019
"""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from modules.common.frame_composite import (
    composite_dominant_cluster,
    composite_max,
    composite_mean,
    composite_median,
    composite_sigma_clip,
    extract_frames_in_range,
)
from modules.contracts import RallyScore

# ── Config ────────────────────────────────────────────────────────────────────

# Composite methods tried in order until both scores are read. dominant_cluster
# first: it survives overlays that appear in <50% of frames (see frame_composite).
FALLBACK_METHODS = [
    ("dominant_cluster", composite_dominant_cluster),
    ("sigma_clip", composite_sigma_clip),
    ("median", composite_median),
    ("mean", composite_mean),
    ("max", composite_max),
]

DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_RPM = 8.0
DEFAULT_CONCURRENCY = 2
DEFAULT_N_FRAMES = 30
DEFAULT_MAX_FRAMES = 120
DEFAULT_SIGMA_CLIP_K = 2.0
DEFAULT_SIGMA_CLIP_ITER = 3
DEFAULT_MAX_RETRIES = 3
DEFAULT_MIN_SCAN_HEIGHT = 480

ProgressCallback = Callable[[float], None]

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

SCORE_PROMPT_SINGLE = """\
This is a screenshot of a badminton match. Read the scoreboard.

The scoreboard has two rows of numbers, one per team:
- "all_a" / "score_a" = the player/team listed FIRST, on the TOP (upper) row of the scoreboard.
- "all_b" / "score_b" = the player/team listed SECOND, on the BOTTOM (lower) row of the scoreboard.

Each team's row may contain 1 to 3 numbers:
- If there is only one number, that is the current score.
- If there are several numbers, they are the scores of each game played, listed left to right
  (game 1, game 2, game 3). The matches may already be in game 2 or 3, so DO NOT assume the
  first number is the current score.

Do NOT pick a number yourself. Instead, transcribe EVERY number you can see for each team,
exactly in left-to-right order. List the same count of numbers for both teams.

Return ONLY this JSON, without any other text or markdown:
{
  "all_a": [<all of the TOP team's numbers, left to right>],
  "all_b": [<all of the BOTTOM team's numbers, left to right>]
}

If the scoreboard is not visible at all, return {"all_a": [], "all_b": []}.\
"""


@dataclass
class ScoreRecognitionConfig:
    """Tunable parameters for the score-recognition pipeline."""

    model: str = DEFAULT_MODEL
    # Max Gemini requests per minute, shared across workers (free tier is ~10;
    # keep headroom). This — not frame extraction — is the stage's real
    # bottleneck, so reading frames from the source video is plenty fast.
    rpm: float = DEFAULT_RPM
    concurrency: int = DEFAULT_CONCURRENCY
    # Max image width sent to Gemini; None keeps source resolution. Scoreboards
    # are small, so the default preserves legibility.
    resize_width: int | None = None
    n_frames: int = DEFAULT_N_FRAMES
    max_frames: int = DEFAULT_MAX_FRAMES
    sigma_clip_k: float = DEFAULT_SIGMA_CLIP_K
    sigma_clip_iter: int = DEFAULT_SIGMA_CLIP_ITER
    max_retries: int = DEFAULT_MAX_RETRIES
    # Prefer the lightest cached downscale that is still >= this height; a lower
    # resolution is cheaper to decode without hurting Gemini's scoreboard read.
    # Nothing in cache -> read the source as-is (never generate a downscale).
    min_scan_height: int = DEFAULT_MIN_SCAN_HEIGHT


# ── Image helpers ─────────────────────────────────────────────────────────────

def image_to_jpeg_bytes(image: np.ndarray) -> bytes:
    """Encode a BGR image to JPEG bytes for Gemini."""
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("failed to encode composite image")
    return buf.tobytes()


def candidate_rank(result: dict) -> int:
    """Rank a candidate by whether both scores exist."""
    return int(result.get("score_a") is not None and result.get("score_b") is not None)


def format_attempts(attempts: list[dict]) -> str:
    """Compact attempt summary for debug metadata."""
    return "; ".join(
        f"{item['method']}:{item.get('score_a')}:{item.get('score_b')}"
        for item in attempts
    )


def should_stop_retry(result: dict) -> bool:
    """Stop trying composite methods once both scores exist."""
    return result.get("score_a") is not None and result.get("score_b") is not None


# ── Rate limiting ─────────────────────────────────────────────────────────────

class RateLimiter:
    """Thread-safe token bucket: caps API calls at `rpm` requests per minute.

    Every HTTP request acquires one token before going out. Tokens refill
    continuously at rpm/60 per second, so the per-minute average never exceeds
    `rpm` regardless of how many worker threads are running or how bursty the
    per-segment fallback is.
    """

    def __init__(self, rpm: float, burst: int = 1):
        self.rate = rpm / 60.0  # tokens per second
        self.capacity = float(max(1, burst))
        self.tokens = self.capacity
        self.timestamp = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, stop_event: "threading.Event | None" = None) -> None:
        """Block until a token is available (or stop_event is set)."""
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.timestamp) * self.rate)
                self.timestamp = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rate
            if stop_event is not None and stop_event.is_set():
                return
            time.sleep(min(wait, 0.5))


# ── Gemini API ────────────────────────────────────────────────────────────────

def call_gemini(
    image_bytes: bytes,
    api_key: str,
    model: str = DEFAULT_MODEL,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_base_delay: float = 10.0,
    rate_limiter: "RateLimiter | None" = None,
    stop_event: "threading.Event | None" = None,
) -> dict:
    """Call Gemini with an image and return the parsed JSON response."""
    import urllib.error
    import urllib.request

    url = API_URL.format(model=model, key=api_key)
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                    {"text": SCORE_PROMPT_SINGLE},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            # 512 leaves headroom for a 3-game scoreboard (up to 6 numbers).
            "maxOutputTokens": 512,
            # gemini-2.5-* enables "thinking" by default, and thinking tokens are
            # billed against maxOutputTokens. On this pure-transcription task the
            # model would sometimes spend the whole budget thinking and get cut
            # off mid-JSON (finishReason=MAX_TOKENS) — the "parse error" failures
            # seen on a handful of rallies. Disabling thinking gives the full
            # budget to the answer and also makes every call noticeably faster.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    for attempt in range(1, max_retries + 1):
        if rate_limiter is not None:
            rate_limiter.acquire(stop_event)
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            if e.code == 429 or e.code >= 500:
                wait = retry_base_delay * attempt
                print(f"    HTTP {e.code}, retry {attempt}/{max_retries} in {wait:.0f}s...")
                time.sleep(wait)
            else:
                print(f"    HTTP {e.code}: {err_body}", file=sys.stderr)
                raise
        except Exception as e:
            wait = retry_base_delay * attempt
            print(f"    Error: {e}, retry {attempt}/{max_retries} in {wait:.0f}s...")
            time.sleep(wait)

    raise RuntimeError(f"failed after {max_retries} retries")


def _coerce_int(val) -> int | None:
    """Best-effort convert a value to int, else None."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _coerce_int_list(val) -> list[int]:
    """Convert a list-like value into a list of ints, dropping unparseable items."""
    if not isinstance(val, list):
        return []
    out = []
    for item in val:
        n = _coerce_int(item)
        if n is not None:
            out.append(n)
    return out


def _salvage_number_arrays(text: str) -> dict:
    """Best-effort recover ``all_a``/``all_b`` from malformed/truncated JSON.

    A truncated MAX_TOKENS response can leave the closing brace off (or cut an
    array mid-way). Rather than throw the read away, pull whatever complete
    numbers each array already contains via regex — a partial ``"all_a": [1, 0``
    still yields ``[1, 0]``.
    """
    import re

    out: dict = {}
    for key in ("all_a", "all_b"):
        m = re.search(r'"' + key + r'"\s*:\s*\[([^\]]*)', text)
        if m:
            out[key] = [int(n) for n in re.findall(r"-?\d+", m.group(1))]
    return out


def parse_response(data: dict) -> dict:
    """Extract the score JSON out of a Gemini response."""
    try:
        candidate = data["candidates"][0]
    except (KeyError, IndexError):
        return {"score_a": None, "score_b": None, "note": "empty response"}

    finish = candidate.get("finishReason", "")
    try:
        text = candidate["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        note = "empty response"
        if finish and finish != "STOP":
            note = f"empty response (finishReason={finish})"
        return {"score_a": None, "score_b": None, "note": note}

    # strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    salvaged = ""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        # Salvage numbers from a truncated/partial JSON body before giving up.
        raw = _salvage_number_arrays(text)
        if not raw:
            hint = f" (finishReason={finish})" if finish and finish != "STOP" else ""
            return {"score_a": None, "score_b": None,
                    "note": f"parse error{hint}: {text[:100]}"}
        salvaged = f" [salvaged, finishReason={finish}]"

    all_a = _coerce_int_list(raw.get("all_a"))
    all_b = _coerce_int_list(raw.get("all_b"))

    # current score = rightmost number of each team's row
    score_a = all_a[-1] if all_a else None
    score_b = all_b[-1] if all_b else None

    # backward compatible: fall back to a flat score_a/score_b if the model gave one
    if score_a is None:
        score_a = _coerce_int(raw.get("score_a"))
    if score_b is None:
        score_b = _coerce_int(raw.get("score_b"))

    note = ""
    if all_a or all_b:
        note = f"all_a={all_a} all_b={all_b}"
    note += salvaged

    return {"score_a": score_a, "score_b": score_b, "all_a": all_a, "all_b": all_b, "note": note}


def call_gemini_for_bytes(
    image_bytes: bytes,
    api_key: str,
    model: str,
    max_retries: int,
    rate_limiter: "RateLimiter | None" = None,
    stop_event: "threading.Event | None" = None,
) -> dict:
    """Call Gemini and parse the returned scoreboard JSON."""
    raw = call_gemini(
        image_bytes, api_key, model=model, max_retries=max_retries,
        rate_limiter=rate_limiter, stop_event=stop_event,
    )
    return parse_response(raw)


# ── Per-segment scoring ────────────────────────────────────────────────────────

def score_segment(
    video_path: str,
    start_frame: int,
    end_frame: int,
    api_key: str,
    config: ScoreRecognitionConfig,
    rate_limiter: "RateLimiter | None" = None,
    stop_event: "threading.Event | None" = None,
) -> tuple[dict, list[dict]]:
    """Score one rally by compositing its frames and querying Gemini.

    Tries dominant_cluster, sigma_clip, median, mean, then max until both scores
    are read. Returns (best_result, all_attempts).
    """
    frames = extract_frames_in_range(
        video_path, start_frame, end_frame,
        config.n_frames, config.resize_width, config.max_frames,
    )
    if len(frames) < 3:
        raise ValueError("need at least 3 frames to build a composite")

    attempts: list[dict] = []
    best: dict | None = None

    for method_name, method_func in FALLBACK_METHODS:
        if stop_event is not None and stop_event.is_set():
            break
        if method_name == "sigma_clip":
            composite = method_func(frames, sigma=config.sigma_clip_k, iterations=config.sigma_clip_iter)
        else:
            composite = method_func(frames)

        image_bytes = image_to_jpeg_bytes(composite)
        parsed = call_gemini_for_bytes(
            image_bytes, api_key, config.model, config.max_retries, rate_limiter, stop_event,
        )
        parsed["method"] = method_name
        attempts.append(parsed)

        if best is None or candidate_rank(parsed) > candidate_rank(best):
            best = parsed

        if should_stop_retry(parsed):
            break

    if best is None:
        raise RuntimeError("no valid Gemini responses returned")
    return best, attempts


def recognize_scores(
    video_path: str,
    segments: list[dict],
    api_key: str,
    config: ScoreRecognitionConfig | None = None,
    on_progress: ProgressCallback | None = None,
    stop_event: "threading.Event | None" = None,
) -> tuple[list[RallyScore], dict]:
    """Read the scoreboard for every segment of ``video_path``.

    ``segments`` is the ``segments`` list from a segments JSON (each record has
    ``start_frame``/``end_frame``). Returns ``(rallies, meta)`` where ``rallies``
    is one :class:`RallyScore` per segment in segment order, and ``meta`` carries
    per-segment debug info (attempt trail, notes, errors).
    """
    config = config or ScoreRecognitionConfig()
    total = len(segments)
    concurrency = max(1, config.concurrency)

    # Shared limiter gates every individual API call to config.rpm across all
    # workers, so parallelism speeds throughput without exceeding the quota.
    rate_limiter = RateLimiter(config.rpm, burst=concurrency)
    print_lock = threading.Lock()
    done_count = [0]

    def process(index: int, seg: dict) -> tuple[RallyScore, dict]:
        """Worker: score one segment. Never raises — failures become a note."""
        try:
            best, attempts = score_segment(
                video_path,
                int(seg["start_frame"]),
                int(seg["end_frame"]),
                api_key,
                config,
                rate_limiter=rate_limiter,
                stop_event=stop_event,
            )
            rally = RallyScore(
                segment_index=index,
                score_a=best.get("score_a"),
                score_b=best.get("score_b"),
            )
            info = {
                "segment_index": index,
                "method": best.get("method", ""),
                "attempts": format_attempts(attempts),
                "note": best.get("note", ""),
            }
            with print_lock:
                print(f"[{index + 1}/{total}] seg {index} "
                      f"→ {rally.score_a} : {rally.score_b}  ({best.get('method')})")
        except Exception as e:
            rally = RallyScore(segment_index=index, score_a=None, score_b=None)
            info = {"segment_index": index, "method": "", "attempts": "", "note": str(e)[:200]}
            with print_lock:
                print(f"[{index + 1}/{total}] seg {index} ERROR: {e}")

        with print_lock:
            done_count[0] += 1
            if on_progress is not None:
                on_progress(done_count[0] / max(total, 1))
        return rally, info

    # Slot results by index so output stays in segment order despite
    # out-of-order completion.
    rallies: list[RallyScore | None] = [None] * total
    infos: list[dict | None] = [None] * total
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(process, i, s): i for i, s in enumerate(segments)}
        try:
            for fut in as_completed(futures):
                i = futures[fut]
                rallies[i], infos[i] = fut.result()
                if stop_event is not None and stop_event.is_set():
                    for f in futures:
                        f.cancel()
        except KeyboardInterrupt:
            if stop_event is not None:
                stop_event.set()
            for f in futures:
                f.cancel()

    kept_rallies = [r for r in rallies if r is not None]
    meta = {"attempts": [info for info in infos if info is not None]}
    return kept_rallies, meta
