"""Cut an MP4 video according to a segments JSON using ffmpeg.

Supports three modes: per-segment output, merged output, and inverse merge
(delete the JSON segments and keep the complement).

CLI usage:
    python -m modules.common.video_cutter -v test.mp4 -s r.json -m merge -o merged.mp4
    python -m modules.common.video_cutter -v match.mp4 -s match.json -m separate -o ./segments
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import shutil
from pathlib import Path

from modules.artifacts import read_records
from modules.common.ffmpeg_utils import (
    check_ffmpeg,
    get_video_duration,
    run_ffmpeg,
    sec_to_ts,
)
from modules.contracts import PIPELINE


def parse_segments(json_path: str) -> list[dict]:
    """Read the segments JSON and return the list of valid segments."""
    records = read_records(PIPELINE["match_segmentation"], json_path)
    segments = []

    # index is 0-based to match segments.json position and scores.json's
    # segment_index everywhere else in the pipeline (so segment seg0000 == segment_index 0).
    for i, record in enumerate(records):
        try:
            start_sec = float(record["start_sec"])
            end_sec = float(record["end_sec"])
        except (KeyError, TypeError, ValueError) as e:
            print(f"  [warn] segment {i} failed to parse ({e}), skipped.")
            continue

        duration_sec = record.get("duration_sec")
        seg = {
            "index":        i,
            "start_sec":    start_sec,
            "end_sec":      end_sec,
            "duration_sec": float(duration_sec) if duration_sec is not None else end_sec - start_sec,
            "start_frame":  record.get("start_frame", ""),
            "end_frame":    record.get("end_frame", ""),
        }
        if seg["end_sec"] <= seg["start_sec"]:
            print(f"  [warn] segment {i}: end_sec ({seg['end_sec']}) <= start_sec ({seg['start_sec']}), skipped.")
            continue
        segments.append(seg)

    return segments


def compute_keep_ranges(
    segments: list[dict],
    video_duration: float,
) -> list[tuple[float, float]]:
    """Return the complement of the given segments within the video duration.

    Segments are clamped to ``[0, video_duration]``, overlapping/adjacent
    removals are merged, and the remaining gaps are returned as keep ranges.
    """
    normalized = []
    for seg in segments:
        start = max(0.0, seg["start_sec"])
        end = min(video_duration, seg["end_sec"])
        if end > start:
            normalized.append((start, end))
    normalized.sort(key=lambda x: x[0])

    merged_remove: list[list[float]] = []
    for start, end in normalized:
        if merged_remove and start <= merged_remove[-1][1]:
            merged_remove[-1][1] = max(merged_remove[-1][1], end)
        else:
            merged_remove.append([start, end])

    keep_ranges: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in merged_remove:
        if start > cursor:
            keep_ranges.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < video_duration:
        keep_ranges.append((cursor, video_duration))

    return keep_ranges


def _cut_segment_cmd(video_path: str, start: float, end: float, out: str) -> list[str]:
    """Build the ffmpeg command that extracts a single [start, end] segment."""
    return [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-to", str(end),
        "-i", video_path,
        "-c:v", "libx264",
        "-c:a", "aac",
        "-avoid_negative_ts", "make_zero",
        out,
    ]


def _concat_files(tmp_files: list[str], tmp_dir: str, output_path: str) -> bool:
    """Concatenate the given temp segments into ``output_path`` via ffmpeg concat."""
    list_file = os.path.join(tmp_dir, "concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in tmp_files:
            f.write(f"file '{p}'\n")

    print(f"\n  merging {len(tmp_files)} segment(s)...")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_path,
    ]
    return run_ffmpeg(cmd, f"merge -> {output_path}")


def mode_separate(video_path: str, segments: list[dict], output_dir: str) -> None:
    """Write every segment as an independent MP4 file."""
    os.makedirs(output_dir, exist_ok=True)
    video_stem = Path(video_path).stem
    total = len(segments)
    success = 0

    print(f"\n[mode A] separate output -> folder: {output_dir}")
    print(f"{total} segment(s)\n")

    for pos, seg in enumerate(segments, start=1):
        idx, start, end, dur = seg["index"], seg["start_sec"], seg["end_sec"], seg["duration_sec"]
        out = os.path.join(output_dir, f"{video_stem}_seg{idx:04d}.mp4")

        print(f"  [{pos}/{total}] seg {idx}: {sec_to_ts(start)} -> {sec_to_ts(end)}  ({dur:.3f}s)")
        if run_ffmpeg(_cut_segment_cmd(video_path, start, end, out), f"write {os.path.basename(out)}"):
            print(f"    ok: {out}")
            success += 1
        else:
            print(f"    fail: segment {idx}")

    print(f"\ndone: {success}/{total} segment(s) written.")


def mode_merge(video_path: str, segments: list[dict], output_path: str) -> None:
    """Concatenate all segments into a single MP4 file."""
    total = len(segments)
    print(f"\n[mode B] merged output -> {output_path}")
    print(f"{total} segment(s), staging then concatenating...\n")

    tmp_dir = tempfile.mkdtemp(prefix="ffmpeg_merge_")
    tmp_files: list[str] = []
    try:
        for pos, seg in enumerate(segments, start=1):
            idx, start, end, dur = seg["index"], seg["start_sec"], seg["end_sec"], seg["duration_sec"]
            tmp = os.path.join(tmp_dir, f"seg{idx:04d}.mp4")
            print(f"  [{pos}/{total}] seg {idx}: {sec_to_ts(start)} -> {sec_to_ts(end)}  ({dur:.3f}s)")
            if run_ffmpeg(_cut_segment_cmd(video_path, start, end, tmp), f"stage segment {idx}"):
                tmp_files.append(tmp)
                print("    ok")
            else:
                print(f"    fail: skip segment {idx}")

        if not tmp_files:
            print("\n[error] no segment staged successfully, cannot merge.")
            return

        if _concat_files(tmp_files, tmp_dir, output_path):
            print(f"\n  ok: merged -> {output_path}")
        else:
            print("\n  fail: merge.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def mode_inverse_merge(video_path: str, segments: list[dict], output_path: str) -> None:
    """Delete the given segments and merge only the remaining (kept) ranges."""
    print(f"\n[mode C] inverse merge (delete segments) -> {output_path}")

    try:
        video_duration = get_video_duration(video_path)
    except Exception as e:
        print(f"[error] failed to read video duration: {e}")
        return

    keep_ranges = compute_keep_ranges(segments, video_duration)
    if not keep_ranges:
        print("\n[error] segments cover the whole video, nothing to keep.")
        return

    print(f"  video duration: {video_duration:.3f}s")
    print(f"  keep ranges: {len(keep_ranges)}, staging then concatenating...\n")

    tmp_dir = tempfile.mkdtemp(prefix="ffmpeg_inverse_merge_")
    tmp_files: list[str] = []
    try:
        for idx, (start, end) in enumerate(keep_ranges, start=1):
            dur = end - start
            tmp = os.path.join(tmp_dir, f"keep{idx:04d}.mp4")
            print(f"  [{idx}/{len(keep_ranges)}] {sec_to_ts(start)} -> {sec_to_ts(end)}  ({dur:.3f}s)")
            if run_ffmpeg(_cut_segment_cmd(video_path, start, end, tmp), f"stage keep range {idx}"):
                tmp_files.append(tmp)
                print("    ok")
            else:
                print(f"    fail: skip range {idx}")

        if not tmp_files:
            print("\n[error] no keep range staged successfully, cannot merge.")
            return

        if _concat_files(tmp_files, tmp_dir, output_path):
            print(f"\n  ok: inverse merge -> {output_path}")
        else:
            print("\n  fail: inverse merge.")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def interactive_mode() -> None:
    """Prompt-driven flow used when no CLI arguments are given."""
    print("=" * 52)
    print("   FFmpeg video cutter  v1.0")
    print("=" * 52)

    while True:
        video = input("\nMP4 video path: ").strip().strip("'\"")
        if os.path.isfile(video):
            break
        print("  [error] file not found, try again.")

    while True:
        segments_path = input("segments JSON path: ").strip().strip("'\"")
        if os.path.isfile(segments_path):
            break
        print("  [error] file not found, try again.")

    try:
        segments = parse_segments(segments_path)
    except Exception as e:
        print(f"\n[error] failed to read segments JSON: {e}")
        sys.exit(1)

    if not segments:
        print("\n[error] no valid segment in the JSON.")
        sys.exit(1)

    print(f"\n  read {len(segments)} valid segment(s).")

    print("\nchoose output mode:")
    print("  1. separate (one MP4 per segment)")
    print("  2. merge (all segments into one MP4)")
    print("  3. inverse merge (delete segments, keep the rest)")

    while True:
        choice = input("enter 1, 2 or 3: ").strip()
        if choice in ("1", "2", "3"):
            break
        print("  please enter 1, 2 or 3.")

    if choice == "1":
        default_dir = Path(video).stem + "_segments"
        out = input(f"output folder (default: {default_dir}): ").strip() or default_dir
        mode_separate(video, segments, out)
    elif choice == "2":
        default_out = Path(video).stem + "_merged.mp4"
        out = input(f"output file (default: {default_out}): ").strip() or default_out
        mode_merge(video, segments, out)
    else:
        default_out = Path(video).stem + "_inverse_merged.mp4"
        out = input(f"output file (default: {default_out}): ").strip() or default_out
        mode_inverse_merge(video, segments, out)


def _default_output(match_path: Path, mode: str) -> str:
    """Default output location under the match path for a given mode."""
    if mode == "separate":
        return str(match_path / "segments")
    if mode == "merge":
        return str(match_path / "merged.mp4")
    return str(match_path / "inverse_merged.mp4")


def resolve_io(args: argparse.Namespace) -> tuple[str, str, str]:
    """Resolve (video, segments, output) from the args.

    When a match path is given, anything left unset is filled from the
    match layout: the raw video under ``input/``, ``match_segmentation``'s
    ``segments.json``, and a mode-appropriate output under the match path.
    Explicit ``-v/-s/-o`` always win.
    """
    video, segments, output = args.video, args.segments, args.output

    if args.match:
        # Imported here so the standalone tool has no import cost when unused.
        from modules.contracts import artifact_path, resolve_input_video

        match_path = Path(args.match)
        if not match_path.is_dir():
            print(f"[error] match path not found: {match_path}")
            sys.exit(1)
        if video is None:
            try:
                video = str(resolve_input_video(match_path))
            except FileNotFoundError as e:
                print(f"[error] {e}")
                sys.exit(1)
        if segments is None:
            segments = str(artifact_path(match_path, "match_segmentation"))
        if output is None:
            output = _default_output(match_path, args.mode)
    else:
        missing = [f for f, v in (("-v/--video", video), ("-s/--segments", segments),
                                  ("-o/--output", output)) if v is None]
        if missing:
            print(f"[error] without a match path, {', '.join(missing)} are required.")
            sys.exit(1)

    return video, segments, output


def cli_mode() -> None:
    """Argument-driven flow."""
    parser = argparse.ArgumentParser(
        description="FFmpeg video cutter - cut an MP4 according to a segments JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
segments JSON schema (required per record: start_sec, end_sec):
  {"fps": 30.0, "segments": [{"start_frame":.., "end_frame":.., "start_sec":.., "end_sec":.., "duration_sec":..}]}

examples:
  match (auto-resolve video/segments/output from the match layout):
                 python -m modules.common.video_cutter matches/MK_vs_CT_2019
                 python -m modules.common.video_cutter matches/MK_vs_CT_2019 -m merge
  explicit paths:
  separate:      python -m modules.common.video_cutter -v in.mp4 -s segments.json -m separate -o ./segments
  merge:         python -m modules.common.video_cutter -v in.mp4 -s segments.json -m merge -o merged.mp4
  inverse-merge: python -m modules.common.video_cutter -v in.mp4 -s segments.json -m inverse-merge -o inv.mp4
""",
    )
    parser.add_argument("match", nargs="?", default=None,
                        help="match path (e.g. matches/MK_vs_CT_2019); "
                             "auto-resolves video/segments/output when given")
    parser.add_argument("-v", "--video", default=None, help="MP4 video path (overrides match input)")
    parser.add_argument("-s", "--segments", default=None, help="segments JSON path (overrides match output)")
    parser.add_argument(
        "-m", "--mode", default="separate",
        choices=["separate", "merge", "inverse-merge"],
        help="output mode: separate (default), merge, or inverse-merge",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="separate mode: output folder; merge modes: output MP4 path",
    )
    args = parser.parse_args()

    video, segments_path, output = resolve_io(args)

    if not os.path.isfile(video):
        print(f"[error] video not found: {video}")
        sys.exit(1)
    if not os.path.isfile(segments_path):
        print(f"[error] segments JSON not found: {segments_path}")
        sys.exit(1)

    try:
        segments = parse_segments(segments_path)
    except Exception as e:
        print(f"[error] failed to read segments JSON: {e}")
        sys.exit(1)

    if not segments:
        print("[error] no valid segment in the JSON.")
        sys.exit(1)

    print(f"  video:    {video}")
    print(f"  segments: {segments_path}")
    print(f"  output:   {output}  (mode: {args.mode})")
    print(f"  read {len(segments)} valid segment(s).")

    if args.mode == "separate":
        mode_separate(video, segments, output)
    elif args.mode == "merge":
        mode_merge(video, segments, output)
    else:
        mode_inverse_merge(video, segments, output)


def main() -> None:
    if not check_ffmpeg():
        sys.exit(1)

    if len(sys.argv) > 1:
        cli_mode()
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
