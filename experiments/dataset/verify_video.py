"""確認你下載到的影片，和本專題實驗時用的是同一支。

為什麼要做這件事
----------------
本專題的真值全部以「影格編號」記錄。一筆標記寫著「第 3 回合是影格 23422 到
23969」，只有在你手上的影片影格排列方式完全相同時才成立。YouTube 對同一支影片
會提供多種編碼，兩個人下載「同一支影片」很容易拿到位元不同的檔案，所以不能只靠
檔案雜湊判斷。

這支程式做三層檢查
------------------
  1. 檔案雜湊 (SHA-256)   完全相同就是同一個檔案，直接通過。
  2. 影格數 / fps / 長度   影格編號能不能對上，全看這三項。
                          對不上就直接判定失敗，真值不適用。
                          （解析度不同只會提醒，不算失敗。）
  3. 影格指紋             在固定的幾個影格編號上，把畫面縮成 8x8 灰階算出一個
                          64 位元的雜湊。重新編碼幾乎不影響它，但換一支影片、
                          或影格整體位移幾格，就會完全不同。

第 3 層是實務上最有用的一層：它對「重新壓過的同一支影片」會通過，對其他東西都不會。

用法
----
    uv run python experiments/dataset/verify_video.py test1
    uv run python experiments/dataset/verify_video.py test1 D:/downloads/my_copy.mp4
    uv run python experiments/dataset/verify_video.py --all

不給路徑時，會去 matches/<代號>/input/ 底下找。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import video_fingerprint as vf  # noqa: E402

MANIFEST = HERE / "videos.json"

# 一個影格雜湊有 64 個位元，差這麼多以內還算同一張畫面。
# 重新編碼通常只差 0~2 個位元；不同畫面通常差 20 個以上。
MAX_BIT_DIFFERENCE = 6

# 至少要有這個比例的影格指紋對得上，才算同一支影片。
MIN_FINGERPRINT_AGREEMENT = 0.90

VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm"}


def load_manifest() -> dict:
    if not MANIFEST.is_file():
        raise SystemExit(f"找不到 {MANIFEST}，請先跑 build_manifest.py")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def find_local_video(code: str) -> Path | None:
    input_dir = ROOT / "matches" / code / "input"
    if not input_dir.is_dir():
        return None
    videos = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
    return videos[0] if videos else None


def compare_fingerprints(expected: list[str], actual: list[str]) -> tuple[int, int]:
    """回傳 (對得上的張數, 比對的張數)。"""
    checked = min(len(expected), len(actual))
    agreed = sum(1 for i in range(checked)
                 if actual[i] != "unreadable"
                 and vf.hamming_distance(expected[i], actual[i]) <= MAX_BIT_DIFFERENCE)
    return agreed, checked


def verify_one(entry: dict, path: Path) -> bool:
    """檢查一支影片，印出過程，回傳是否通過。"""
    print(f"\n=== {entry['code']} ===")
    print(f"  期望：{entry['width']}x{entry['height']}  {entry['fps']:g}fps  "
          f"{entry['frame_count']} 影格  {entry['duration_sec']:.1f} 秒")
    print(f"  你的：{path}")

    facts = vf.describe(path, with_sha256=bool(entry.get("sha256")))

    # --- 第 1 層：檔案雜湊 ---
    if entry.get("sha256") and facts.sha256 == entry["sha256"]:
        print("  [1/3] 檔案雜湊完全相同 -> 這就是同一個檔案。通過。")
        return True
    if entry.get("sha256"):
        print("  [1/3] 檔案雜湊不同（很正常，代表你的是另一種編碼）。繼續往下檢查。")

    # --- 第 2 層：影格編號對得上嗎 ---
    # 只有這三項會影響「第 N 影格是哪一瞬間」，所以不符就是失敗。
    hard_checks = [
        ("影格數", facts.frame_count, entry["frame_count"]),
        ("fps", round(facts.fps, 3), round(entry["fps"], 3)),
    ]
    failures = [(name, mine, want) for name, mine, want in hard_checks if mine != want]
    if abs(facts.duration_sec - entry["duration_sec"]) > 1.0:
        failures.append(("長度(秒)", round(facts.duration_sec, 1), entry["duration_sec"]))

    if failures:
        print("  [2/3] 不符：")
        for name, mine, want in failures:
            print(f"          {name}：你的 {mine}，應為 {want}")
        print("        影格編號對不上，本專題的真值不適用於這個檔案。")
        return False
    print("  [2/3] 影格數、fps、長度都相符。")

    # 解析度不影響影格編號，所以只提醒，不判定失敗。
    if (facts.width, facts.height) != (entry["width"], entry["height"]):
        print(f"        注意：解析度是 {facts.width}x{facts.height}，"
              f"實驗時用的是 {entry['width']}x{entry['height']}。"
              f"真值仍然適用，但跑出來的數字可能和報告有出入。")

    # --- 第 3 層：畫面內容 ---
    agreed, checked = compare_fingerprints(entry["fingerprint"], facts.fingerprint)
    ratio = agreed / checked if checked else 0.0
    print(f"  [3/3] 影格指紋：{checked} 張裡對上 {agreed} 張（{ratio:.0%}）")

    if ratio >= MIN_FINGERPRINT_AGREEMENT:
        print("        通過：內容相同，只是編碼不同。真值可以直接使用。")
        return True

    print("        不通過：畫面對不上。可能是不同的上傳版本，或影格整體位移了。")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="確認影片和實驗時用的是同一支")
    parser.add_argument("code", nargs="?", help="影片代號，例如 test1")
    parser.add_argument("path", nargs="?", help="你的影片檔路徑（預設去 matches/ 找）")
    parser.add_argument("--all", action="store_true", help="檢查 manifest 裡的每一支")
    args = parser.parse_args()

    manifest = load_manifest()
    by_code = {v["code"]: v for v in manifest["videos"]}

    if args.all:
        targets = [(entry, find_local_video(entry["code"])) for entry in manifest["videos"]]
    else:
        if not args.code:
            parser.error("請給影片代號，或加 --all")
        if args.code not in by_code:
            parser.error(f"manifest 裡沒有 {args.code}；有的是：{', '.join(by_code)}")
        entry = by_code[args.code]
        targets = [(entry, Path(args.path) if args.path else find_local_video(args.code))]

    passed, failed, missing = 0, 0, 0
    for entry, path in targets:
        if path is None or not path.is_file():
            print(f"\n=== {entry['code']} ===\n  找不到影片檔，略過。")
            missing += 1
            continue
        if verify_one(entry, path):
            passed += 1
        else:
            failed += 1

    print(f"\n通過 {passed}、不通過 {failed}、找不到檔案 {missing}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
