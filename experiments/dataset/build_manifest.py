"""Build videos.json / videos.md from the video files on this machine.

Run this once, on the machine the experiments were run on. It walks every video
listed in sources.json, records what it is (see video_fingerprint.py), and
writes both a machine-readable manifest and a human-readable table.

    uv run python experiments/dataset/build_manifest.py
    uv run python experiments/dataset/build_manifest.py --no-sha256   # faster

Videos are expected at matches/<code>/input/<any single video file>.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import video_fingerprint as vf  # noqa: E402

SOURCES = HERE / "sources.json"
MANIFEST = HERE / "videos.json"
TABLE = HERE / "videos.md"

VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm"}


def find_video(code: str) -> Path | None:
    """The one video file under matches/<code>/input/."""
    input_dir = ROOT / "matches" / code / "input"
    if not input_dir.is_dir():
        return None
    videos = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
    return videos[0] if videos else None


def build(with_sha256: bool) -> dict:
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))

    entries = []
    for source in sources["videos"]:
        code = source["code"]
        path = find_video(code)
        if path is None:
            print(f"  {code:<16} SKIPPED (no video under matches/{code}/input/)")
            continue

        print(f"  {code:<16} reading {path.name} ...", flush=True)
        facts = vf.describe(path, with_sha256=with_sha256)
        entries.append({
            "code": code,
            "split": source["split"],
            "title": source["title"],
            "youtube_url": f"https://www.youtube.com/watch?v={source['youtube_id']}",
            **vf.facts_as_dict(facts),
        })

    return {
        "built_on": date.today().isoformat(),
        "fingerprint": {
            "method": "dHash 8x8 of the greyscale frame, 16 hex chars per frame",
            "frames": "step//2, step*3//2, ... ; step = 5000 frames for videos of "
                      "30000+ frames, else 1000",
            "match_rule": "a frame hash counts as equal when at most 6 of its 64 "
                          "bits differ; the video passes when at least 90% of its "
                          "frames match",
        },
        "videos": entries,
    }


def write_table(manifest: dict) -> None:
    lines = [
        "# 影片對照表",
        "",
        f"產生日期：{manifest['built_on']}",
        "",
        "本專題的真值全部以**影格編號**記錄，因此你手上的影片必須和標記時用的是",
        "同一支、且影格數與 fps 完全一致，真值才適用。下載後請用 `verify_video.py`",
        "確認（用法見 [README.md](./README.md)）。",
        "",
        "| 代號 | 切分 | 比賽 | YouTube | 解析度 | fps | 影格數 | 長度(秒) | 編碼 | 檔案大小 | SHA-256 (前16碼) |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- |",
    ]
    for v in manifest["videos"]:
        size_mb = f"{v['file_size'] / 1024 / 1024:.0f} MB"
        sha = v["sha256"][:16] if v["sha256"] else "(未計算)"
        # 標題裡的 | 會被當成表格的欄位分隔，要先跳脫。
        v = {**v, "title": v["title"].replace("|", "\\|")}
        lines.append(
            f"| `{v['code']}` | {v['split']} | {v['title']} | "
            f"[連結]({v['youtube_url']}) | {v['width']}x{v['height']} | {v['fps']:g} | "
            f"{v['frame_count']} | {v['duration_sec']:.1f} | {v['codec']} | {size_mb} | `{sha}` |"
        )

    dev = sum(1 for v in manifest["videos"] if v["split"] == "dev")
    test = sum(1 for v in manifest["videos"] if v["split"] == "test")
    lines += [
        "",
        f"合計 {len(manifest['videos'])} 支：開發集 {dev} 支、測試集 {test} 支。",
        "",
        "完整欄位（含每支影片的影格指紋）見 [videos.json](./videos.json)。",
        "",
    ]
    TABLE.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-sha256", dest="sha256", action="store_false",
                        help="skip the whole-file hash (much faster, slightly weaker)")
    parser.add_argument("--table-only", action="store_true",
                        help="rebuild videos.md from the existing videos.json")
    args = parser.parse_args()

    if args.table_only:
        write_table(json.loads(MANIFEST.read_text(encoding="utf-8")))
        print(f"wrote {TABLE.relative_to(ROOT)} from the existing manifest")
        return 0

    print("building manifest ...")
    manifest = build(with_sha256=args.sha256)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    write_table(manifest)
    print(f"\nwrote {MANIFEST.relative_to(ROOT)} and {TABLE.relative_to(ROOT)} "
          f"({len(manifest['videos'])} videos)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
