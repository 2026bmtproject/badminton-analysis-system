"""用「答案的擊球幀」代替系統偵測的擊球幀，重跑一次球種分類。

為什麼要跑這個
--------------
  平常球種分類讀的是 event_detection 抓到的擊球幀，BST 的窗口從上一拍切到下一拍之後。
  如果擊球幀抓偏或漏掉，窗口也跟著切歪，球種就可能判錯——這時錯的其實是上游。

  這支程式把擊球幀換成 ShuttleSet 答案的影格，其他（骨架、球軌跡、場地、BST 權重）
  完全不變，重跑一次。eval_strokes.py 會拿兩次的結果比較，拆出：
    - 換成正確的擊球幀就判對了  -> 錯在上游（擊球時刻）
    - 換成正確的擊球幀仍然判錯  -> 錯在 BST 本身

  只有落在系統片段（segments.json）內的答案拍會被分類，片段外沒有骨架與軌跡可讀。

需要 GPU（沒有也能跑，比較慢）和 matches/<代號>/ 底下既有的管線輸出。

用法
----
    uv run python experiments/stroke_classification/run_oracle.py            # 全部有真值的場次
    uv run python experiments/stroke_classification/run_oracle.py test5 test8

結果寫入 results/oracle.csv，一拍一列：match, frame, stroke_type, player。
指定部分場次時，只會替換那幾場的列，其他場次的列保留。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUTPUT = HERE / "results" / "oracle.csv"

# 專案的模組與 event_detection 的評分程式都要 import 得到
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments" / "event_detection"))

import eval_events                                                    # noqa: E402
from modules.artifacts import read_segments                           # noqa: E402
from modules.contracts import SegmentIndex                            # noqa: E402
from modules.stroke_classification.module import StrokeClassificationModule   # noqa: E402


def classify_answer_frames(code: str, module: StrokeClassificationModule) -> list[dict]:
    """把一場的答案擊球幀丟給球種分類，回傳 [{"match", "frame", "stroke_type", "player"}, ...]。"""
    match_path = ROOT / "matches" / code
    segments, fps = read_segments(match_path)
    index = SegmentIndex(segments)

    # 球種分類要的格式：{片段編號: [(編號, 片段內的影格), ...]}
    hits = {}
    answer_number = 0
    for answer in eval_events.load_answers(code):
        located = index.locate(answer["frame"])
        if located is None:
            continue                                # 片段外，沒有東西可讀
        segment_index, local_frame = located
        if segment_index not in hits:
            hits[segment_index] = []
        hits[segment_index].append((answer_number, local_frame))
        answer_number += 1

    predictions = module.classify(match_path, segments, fps, hits)

    rows = []
    for prediction in predictions:
        label = prediction.label
        rows.append({
            "match": code,
            "frame": label.frame,
            "stroke_type": label.stroke_type,
            "player": label.player or "",
        })
    return rows


def read_existing() -> list[dict]:
    if not OUTPUT.is_file():
        return []
    with OUTPUT.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    parser = argparse.ArgumentParser(description="用答案的擊球幀重跑球種分類")
    parser.add_argument("matches", nargs="*", help="只跑這幾場（預設：全部有真值的場次）")
    args = parser.parse_args()

    codes = args.matches
    if not codes:
        codes = []
        for code in eval_events.load_splits():
            if (eval_events.ANS_DIR / code).is_dir():
                codes.append(code)

    # 沒被指定的場次，保留舊的結果
    rows = []
    for row in read_existing():
        if row["match"] not in codes:
            rows.append(row)

    module = StrokeClassificationModule()
    for code in codes:
        print(f"== {code}")
        rows.extend(classify_answer_frames(code, module))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["match", "frame", "stroke_type", "player"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n寫入 {OUTPUT.relative_to(ROOT)}（{len(rows)} 拍）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
