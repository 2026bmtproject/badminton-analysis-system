"""賽事影片分段：把系統輸出和人工真值比對，算出報告要的數字。

這支程式做的事，一句話講完：
    把「人工標的片段」和「系統切出來的片段」兩兩配對，
    數對了幾段、多切了幾段、漏了幾段，再看配對成功的段邊界差幾影格。

名詞
----
  真值 (truth)    人工標記的固定機位比賽片段，一段就是一組起訖影格編號。
                  來源：ans/<代號>.csv
  輸出 (output)   系統跑出來的片段。
                  來源：matches/<代號>/stages/match_segmentation/segments.json
  片段            [起始影格, 結束影格]，兩端都算在內。
                  例如 [100, 102] 是三個影格：100、101、102。

怎麼判斷「這段有對到」
----------------------
  用 IoU（Intersection over Union，交集除以聯集）：

      真值   |-------------------|
      輸出        |------------------|
      交集        |--------------|          <- 兩段都有蓋到的影格
      聯集   |-----------------------|      <- 任一段有蓋到的影格

      IoU = 交集影格數 / 聯集影格數

  IoU 是 0 到 1 的數：完全重合是 1，完全沒碰到是 0。
  本評分規定 IoU >= 0.9 才算配對成功，也就是兩段幾乎要完全重合。
  一段真值最多只能配一段輸出，反之亦然（一對一）。

三種結果
--------
  matched  配對成功         系統正確找到的片段
  FP       多切的（誤報）   輸出裡沒配到任何真值的段
  FN       漏掉的（漏報）   真值裡沒配到任何輸出的段

  Precision（精確率）= matched / 輸出總數    系統說有的，有幾成是真的
  Recall  （召回率）  = matched / 真值總數    真的有的，抓到幾成
  F1                  = 兩者的調和平均        一個兼顧兩邊的總分

邊界誤差
--------
  只看配對成功的段。定義成「輸出減真值」，單位是影格：

      起點誤差 = 輸出起點 - 真值起點    正數 = 系統晚開始（切掉了開頭）
      終點誤差 = 輸出終點 - 真值終點    負數 = 系統早結束（切掉了結尾）

  影片有 25fps 也有 30fps，影格數跨場不能直接比，所以同時換算成毫秒。

用法
----
    uv run python experiments/match_segmentation/eval_segments.py
    uv run python experiments/match_segmentation/eval_segments.py test1 test2
    uv run python experiments/match_segmentation/eval_segments.py --quiet

  結果都會印在畫面上。寫進 results/ 的時機：跑全部場次時會寫；
  只指定部分場次時預設不寫（避免用部分結果蓋掉全體報表），要寫再加 --write。
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ANS_DIR = HERE / "ans"
RESULTS_DIR = HERE / "results"
SOURCES = ROOT / "experiments" / "dataset" / "sources.json"

# 配對門檻：兩段要幾乎完全重合（IoU >= 0.9）才算同一段。
IOU_THRESHOLD = 0.9

# 容差命中率要報的幾個容差（影格）。
TOLERANCES = (1, 2, 3, 5, 10)

# 邊界誤差超過這個影格數就列為離群，另外列表檢查。
OUTLIER_FRAMES = 10


# ------------------------------------------------------------------
# 片段的基本運算。片段一律寫成 (start, end)，兩端都算在內。
# ------------------------------------------------------------------

def length_of(seg: tuple[int, int]) -> int:
    """這段有幾個影格。"""
    return seg[1] - seg[0] + 1


def overlap_of(a: tuple[int, int], b: tuple[int, int]) -> int:
    """兩段重疊了幾個影格（沒重疊就是 0）。"""
    first_shared = max(a[0], b[0])
    last_shared = min(a[1], b[1])
    return max(0, last_shared - first_shared + 1)


def iou_of(a: tuple[int, int], b: tuple[int, int]) -> float:
    """交集除以聯集。0 = 完全沒碰到，1 = 完全重合。"""
    shared = overlap_of(a, b)
    if shared == 0:
        return 0.0
    union = length_of(a) + length_of(b) - shared
    return shared / union


def pair_up(truth: list, output: list) -> list[tuple[int, int, float]]:
    """把真值和輸出配對，回傳 [(真值編號, 輸出編號, IoU), ...]。

    做法很直接：把所有 IoU 夠高的組合列出來，從最像的一組開始配；
    已經配掉的段就不再參與，直到沒有組合可配為止。
    """
    candidates = []
    for t_index, t_seg in enumerate(truth):
        for o_index, o_seg in enumerate(output):
            score = iou_of(t_seg, o_seg)
            if score >= IOU_THRESHOLD:
                candidates.append((score, t_index, o_index))

    candidates.sort(reverse=True)           # 最像的排前面

    used_truth, used_output = set(), set()
    pairs = []
    for score, t_index, o_index in candidates:
        if t_index in used_truth or o_index in used_output:
            continue                        # 其中一段已經配給別人了
        used_truth.add(t_index)
        used_output.add(o_index)
        pairs.append((t_index, o_index, score))

    pairs.sort()                            # 依真值順序排好，方便閱讀
    return pairs


# ------------------------------------------------------------------
# 讀檔
# ------------------------------------------------------------------

def load_truth(code: str) -> list[tuple[int, int]]:
    """讀人工真值 ans/<代號>.csv。"""
    path = ANS_DIR / f"{code}.csv"
    segments = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            segments.append((int(float(row["start_frame"])),
                             int(float(row["end_frame"]))))
    segments.sort()
    return segments


def load_output(code: str) -> tuple[list[tuple[int, int]], float]:
    """讀系統輸出 segments.json，順便把 fps 帶出來（換算毫秒要用）。"""
    path = ROOT / "matches" / code / "stages" / "match_segmentation" / "segments.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = [(int(s["start_frame"]), int(s["end_frame"])) for s in data["segments"]]
    segments.sort()
    if data.get("fps") is None:
        raise ValueError(f"{path} 缺少 fps 欄位；fps 為必填，請在輸出中補上")
    return segments, float(data["fps"])


def load_splits() -> dict[str, str]:
    """每場屬於開發集還是測試集。"""
    data = json.loads(SOURCES.read_text(encoding="utf-8"))
    return {v["code"]: v["split"] for v in data["videos"]}


# ------------------------------------------------------------------
# 評分一場比賽
# ------------------------------------------------------------------

class MatchResult:
    """一場比賽的比對結果。"""

    def __init__(self, code: str, split: str, fps: float):
        self.code = code
        self.split = split
        self.fps = fps
        self.n_truth = 0
        self.n_output = 0
        self.start_errors: list[int] = []   # 每個配對成功的段一筆，單位影格
        self.end_errors: list[int] = []
        self.pairs: list[dict] = []         # 寫檔用的逐段明細
        self.false_positives: list[tuple[int, int]] = []
        self.false_negatives: list[tuple[int, int]] = []

    @property
    def n_matched(self) -> int:
        return len(self.pairs)

    @property
    def precision(self) -> float:
        return self.n_matched / self.n_output if self.n_output else 0.0

    @property
    def recall(self) -> float:
        return self.n_matched / self.n_truth if self.n_truth else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def evaluate(code: str, split: str) -> MatchResult:
    truth = load_truth(code)
    output, fps = load_output(code)

    result = MatchResult(code, split, fps)
    result.n_truth = len(truth)
    result.n_output = len(output)

    pairs = pair_up(truth, output)
    matched_truth = {t for t, _, _ in pairs}
    matched_output = {o for _, o, _ in pairs}

    for t_index, o_index, score in pairs:
        t_seg, o_seg = truth[t_index], output[o_index]
        start_error = o_seg[0] - t_seg[0]
        end_error = o_seg[1] - t_seg[1]
        result.start_errors.append(start_error)
        result.end_errors.append(end_error)
        result.pairs.append({
            "match": code,
            "split": split,
            "truth_start": t_seg[0], "truth_end": t_seg[1],
            "output_start": o_seg[0], "output_end": o_seg[1],
            "iou": round(score, 4),
            "start_error_frames": start_error,
            "start_error_ms": round(start_error / fps * 1000, 1),
            "end_error_frames": end_error,
            "end_error_ms": round(end_error / fps * 1000, 1),
        })

    result.false_positives = [output[i] for i in range(len(output)) if i not in matched_output]
    result.false_negatives = [truth[i] for i in range(len(truth)) if i not in matched_truth]
    return result


# ------------------------------------------------------------------
# 把多場的結果加總
# ------------------------------------------------------------------

class Summary:
    """一組比賽（開發集／測試集／全部）的合計數字。"""

    def __init__(self, label: str, results: list[MatchResult]):
        self.label = label
        self.results = results
        self.n_truth = sum(r.n_truth for r in results)
        self.n_output = sum(r.n_output for r in results)
        self.n_matched = sum(r.n_matched for r in results)
        self.n_fp = sum(len(r.false_positives) for r in results)
        self.n_fn = sum(len(r.false_negatives) for r in results)
        self.start_errors = [e for r in results for e in r.start_errors]
        self.end_errors = [e for r in results for e in r.end_errors]
        self.start_errors_ms = [e / r.fps * 1000 for r in results for e in r.start_errors]
        self.end_errors_ms = [e / r.fps * 1000 for r in results for e in r.end_errors]

    @property
    def precision(self) -> float:
        return self.n_matched / self.n_output if self.n_output else 0.0

    @property
    def recall(self) -> float:
        return self.n_matched / self.n_truth if self.n_truth else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def describe_errors(errors: list) -> dict:
    """一串誤差的中位數、平均、平均絕對誤差、最大偏移。"""
    if not errors:
        return {"median": 0.0, "mean": 0.0, "mae": 0.0, "min": 0.0, "max": 0.0}
    return {
        "median": statistics.median(errors),
        "mean": statistics.fmean(errors),
        "mae": statistics.fmean(abs(e) for e in errors),   # 正負不相消，看絕對偏差
        "min": min(errors),
        "max": max(errors),
    }


def hit_rate(errors: list[int], tolerance: int) -> float:
    """誤差落在 ±tolerance 影格以內的比例。"""
    if not errors:
        return 0.0
    return sum(1 for e in errors if abs(e) <= tolerance) / len(errors)


def worst_of(stats: dict) -> float:
    """最大偏移：正負兩端取絕對值較大的那個，保留正負號。"""
    return stats["max"] if abs(stats["max"]) >= abs(stats["min"]) else stats["min"]


# ------------------------------------------------------------------
# 印到畫面
# ------------------------------------------------------------------

def print_match(result: MatchResult) -> None:
    start = describe_errors(result.start_errors)
    end = describe_errors(result.end_errors)
    print(f"\n=== {result.code}  ({result.split}, {result.fps:g}fps) ===")
    print(f"  真值 {result.n_truth} 段 / 輸出 {result.n_output} 段 -> "
          f"配對 {result.n_matched}、多切 {len(result.false_positives)}、"
          f"漏掉 {len(result.false_negatives)}")
    print(f"  P={result.precision:7.2%}  R={result.recall:7.2%}  F1={result.f1:7.2%}")
    print(f"  起點誤差  中位數 {start['median']:+.1f}  MAE {start['mae']:6.2f}  "
          f"範圍 {start['min']:+.0f} ~ {start['max']:+.0f} 影格")
    print(f"  終點誤差  中位數 {end['median']:+.1f}  MAE {end['mae']:6.2f}  "
          f"範圍 {end['min']:+.0f} ~ {end['max']:+.0f} 影格")

    for seg in result.false_positives:
        print(f"    [多切] 影格 {seg[0]}-{seg[1]}  "
              f"({seg[0] / result.fps:.1f}s 起, 長 {length_of(seg) / result.fps:.1f}s)")
    for seg in result.false_negatives:
        print(f"    [漏掉] 影格 {seg[0]}-{seg[1]}  "
              f"({seg[0] / result.fps:.1f}s 起, 長 {length_of(seg) / result.fps:.1f}s)")


def print_summary(summary: Summary) -> None:
    start = describe_errors(summary.start_errors)
    end = describe_errors(summary.end_errors)
    print(f"\n--- {summary.label} ({len(summary.results)} 場) ---")
    print(f"  真值 {summary.n_truth} / 輸出 {summary.n_output} -> "
          f"配對 {summary.n_matched}、多切 {summary.n_fp}、漏掉 {summary.n_fn}")
    print(f"  P={summary.precision:7.2%}  R={summary.recall:7.2%}  F1={summary.f1:7.2%}")
    print(f"  起點誤差  中位數 {start['median']:+.1f}  平均 {start['mean']:+.2f}  "
          f"MAE {start['mae']:.2f}  範圍 {start['min']:+.0f} ~ {start['max']:+.0f} 影格")
    print(f"  終點誤差  中位數 {end['median']:+.1f}  平均 {end['mean']:+.2f}  "
          f"MAE {end['mae']:.2f}  範圍 {end['min']:+.0f} ~ {end['max']:+.0f} 影格")
    hits = "  ".join(f"±{t}: {hit_rate(summary.start_errors, t):.1%}" for t in TOLERANCES)
    print(f"  起點容差命中率  {hits}")
    hits = "  ".join(f"±{t}: {hit_rate(summary.end_errors, t):.1%}" for t in TOLERANCES)
    print(f"  終點容差命中率  {hits}")


# ------------------------------------------------------------------
# 寫檔
# ------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.unlink(missing_ok=True)
        return
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_per_match(results: list[MatchResult]) -> None:
    rows = []
    for r in results:
        start, end = describe_errors(r.start_errors), describe_errors(r.end_errors)
        rows.append({
            "match": r.code, "split": r.split, "fps": r.fps,
            "truth": r.n_truth, "output": r.n_output, "matched": r.n_matched,
            "fp": len(r.false_positives), "fn": len(r.false_negatives),
            "precision": round(r.precision * 100, 2),
            "recall": round(r.recall * 100, 2),
            "f1": round(r.f1 * 100, 2),
            "start_median": start["median"], "start_mean": round(start["mean"], 2),
            "start_mae": round(start["mae"], 2),
            "start_min": start["min"], "start_max": start["max"],
            "end_median": end["median"], "end_mean": round(end["mean"], 2),
            "end_mae": round(end["mae"], 2),
            "end_min": end["min"], "end_max": end["max"],
        })
    write_csv(RESULTS_DIR / "per_match.csv", rows)


def write_boundary_errors(results: list[MatchResult]) -> None:
    write_csv(RESULTS_DIR / "boundary_errors.csv",
              [pair for r in results for pair in r.pairs])


def write_mismatches(results: list[MatchResult]) -> None:
    """所有多切和漏掉的段，逐筆列出來，供失敗案例分析用。"""
    rows = []
    for r in results:
        for kind, segments in (("FP 多切", r.false_positives),
                               ("FN 漏掉", r.false_negatives)):
            for seg in segments:
                rows.append({
                    "match": r.code, "split": r.split, "kind": kind,
                    "start_frame": seg[0], "end_frame": seg[1],
                    "start_sec": round(seg[0] / r.fps, 2),
                    "end_sec": round(seg[1] / r.fps, 2),
                    "duration_sec": round(length_of(seg) / r.fps, 2),
                })
    rows.sort(key=lambda row: (row["split"], row["match"], row["start_frame"]))
    write_csv(RESULTS_DIR / "mismatches.csv", rows)


def write_outliers(results: list[MatchResult]) -> None:
    """邊界誤差特別大的段，逐筆列出來。"""
    rows = []
    for r in results:
        for pair in r.pairs:
            worst = max(abs(pair["start_error_frames"]), abs(pair["end_error_frames"]))
            if worst > OUTLIER_FRAMES:
                rows.append({**pair, "worst_error_frames": worst})
    rows.sort(key=lambda row: -row["worst_error_frames"])
    write_csv(RESULTS_DIR / "boundary_outliers.csv", rows)


def write_report_tables(results: list[MatchResult], summaries: list[Summary]) -> None:
    """產生可以直接貼進 docs/report.md 的表格。"""
    lines = ["# 賽事影片分段：實驗結果表", "",
             "由 `eval_segments.py` 自動產生，請勿手改。", "",
             "## 偵測準確率", "",
             f"配對規則：IoU >= {IOU_THRESHOLD}，一對一。", "",
             "| 比賽 | 真值 ans | 輸出 prog | matched | FP | FN | Precision | Recall | F1 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]

    def accuracy_row(label, n_truth, n_output, matched, fp, fn, p, r, f1, bold=False):
        cells = [label, n_truth, n_output, matched, fp, fn,
                 f"{p * 100:.2f}", f"{r * 100:.2f}", f"{f1 * 100:.2f}"]
        if bold:
            cells = [f"**{c}**" for c in cells]
        return "| " + " | ".join(str(c) for c in cells) + " |"

    def boundary_row(label, start, end, bold=False):
        cells = [label,
                 f"{start['median']:+.1f}", f"{start['mae']:.2f}", f"{worst_of(start):+.0f}",
                 f"{end['median']:+.1f}", f"{end['mae']:.2f}", f"{worst_of(end):+.0f}"]
        if bold:
            cells = [f"**{c}**" for c in cells]
        return "| " + " | ".join(cells) + " |"

    total = next(s for s in summaries if s.label == "合計")

    for split_label, split_key in (("開發集", "dev"), ("測試集", "test")):
        for r in [r for r in results if r.split == split_key]:
            lines.append(accuracy_row(r.code, r.n_truth, r.n_output, r.n_matched,
                                      len(r.false_positives), len(r.false_negatives),
                                      r.precision, r.recall, r.f1))
        s = next(s for s in summaries if s.label == split_label)
        lines.append(accuracy_row(f"{split_label}小計", s.n_truth, s.n_output,
                                  s.n_matched, s.n_fp, s.n_fn,
                                  s.precision, s.recall, s.f1, bold=True))
    lines.append(accuracy_row("合計", total.n_truth, total.n_output, total.n_matched,
                              total.n_fp, total.n_fn,
                              total.precision, total.recall, total.f1, bold=True))

    lines += ["", "## 邊界精度", "",
              "誤差 = 輸出 - 真值。起點為正代表系統晚開始，終點為負代表系統早結束。", "",
              "| 比賽 | 起點中位數 | 起點 MAE | 起點最大偏移 | 終點中位數 | 終點 MAE | 終點最大偏移 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]

    for split_label, split_key in (("開發集", "dev"), ("測試集", "test")):
        for r in [r for r in results if r.split == split_key]:
            lines.append(boundary_row(r.code, describe_errors(r.start_errors),
                                      describe_errors(r.end_errors)))
        s = next(s for s in summaries if s.label == split_label)
        lines.append(boundary_row(f"{split_label}小計", describe_errors(s.start_errors),
                                  describe_errors(s.end_errors), bold=True))
    lines.append(boundary_row("合計", describe_errors(total.start_errors),
                              describe_errors(total.end_errors), bold=True))

    lines += ["", "### 容差命中率（誤差在 ±N 影格以內的比例）", "",
              "| 範圍 | 邊界 | " + " | ".join(f"±{t} 影格" for t in TOLERANCES) + " |",
              "| --- | --- |" + " ---: |" * len(TOLERANCES)]
    for s in summaries:
        for name, errors in (("起點", s.start_errors), ("終點", s.end_errors)):
            cells = " | ".join(f"{hit_rate(errors, t) * 100:.1f}%" for t in TOLERANCES)
            lines.append(f"| {s.label} | {name} | {cells} |")

    lines += ["", "### 以毫秒計（影片有 25 與 30 fps，跨場比較用）", "",
              "| 範圍 | 邊界 | 中位數 | MAE | 最大偏移 |",
              "| --- | --- | ---: | ---: | ---: |"]
    for s in summaries:
        for name, errors in (("起點", s.start_errors_ms), ("終點", s.end_errors_ms)):
            stats = describe_errors(errors)
            lines.append(f"| {s.label} | {name} | {stats['median']:+.1f} ms | "
                         f"{stats['mae']:.1f} ms | {worst_of(stats):+.0f} ms |")

    n_outliers = sum(1 for r in results for pair in r.pairs
                     if max(abs(pair["start_error_frames"]),
                            abs(pair["end_error_frames"])) > OUTLIER_FRAMES)
    note = (f"離群段（起點或終點誤差超過 {OUTLIER_FRAMES} 影格）：{n_outliers} 段，"
            f"逐筆見 `boundary_outliers.csv`。") if n_outliers else \
           (f"沒有任何配對段的邊界誤差超過 {OUTLIER_FRAMES} 影格。")
    lines += ["", note, ""]

    (RESULTS_DIR / "report_tables.md").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="賽事影片分段的準確率評分")
    parser.add_argument("matches", nargs="*", help="只評這幾場（預設：全部）")
    parser.add_argument("-q", "--quiet", action="store_true", help="不印每場的細節")
    parser.add_argument("--no-write", action="store_true", help="不寫 results/")
    parser.add_argument("--write", action="store_true",
                        help="指定場次時仍強制覆寫 results/"
                             "（預設只有評估全部場次時才寫，避免用部分結果蓋掉共用報表）")
    args = parser.parse_args()

    splits = load_splits()
    codes = args.matches or [c for c in splits if (ANS_DIR / f"{c}.csv").is_file()]

    results = []
    for code in codes:
        result = evaluate(code, splits.get(code, "?"))
        results.append(result)
        if not args.quiet:
            print_match(result)

    summaries = [
        Summary("開發集", [r for r in results if r.split == "dev"]),
        Summary("測試集", [r for r in results if r.split == "test"]),
        Summary("合計", results),
    ]
    print("\n" + "=" * 66)
    for summary in summaries:
        if summary.results:
            print_summary(summary)

    partial = bool(args.matches)
    if args.no_write:
        pass
    elif partial and not args.write:
        print("\n（只評了指定場次，未覆寫 results/，以免蓋掉全體報表；"
              "要覆寫請加 --write，或改跑全部場次）")
    else:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        write_per_match(results)
        write_boundary_errors(results)
        write_mismatches(results)
        write_outliers(results)
        write_report_tables(results, summaries)
        print(f"\n結果已寫入 {RESULTS_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
