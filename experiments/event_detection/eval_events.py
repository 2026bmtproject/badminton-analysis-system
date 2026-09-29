"""擊球時刻偵測：把系統偵測到的擊球幀和 ShuttleSet 答案比對，算出報告要的數字。

名詞
----
  答案 (answer)     ShuttleSet 標記的擊球幀，一拍一個影格編號。
                    來源：ans/<代號>/setN.csv 的 frame_num 欄
  偵測 (detection)  系統輸出的擊球幀。
                    來源：matches/<代號>/stages/event_detection/events.json
  片段 (segment)    賽事影片分段的輸出，也就是系統認定「正在比賽」的區段。
                    來源：matches/<代號>/stages/match_segmentation/segments.json
  回合 (rally)      答案 csv 裡同一個 set、同一個 rally 編號的所有拍。

影片版本差
----------
  test5、test8 兩場的影片版本和 ShuttleSet 的不同，影格編號整體差了
  整數分鐘，讀答案時先補回去（見 VIDEO_OFFSETS）。其他場次的答案原樣使用。

只看重疊的時間
--------------
  答案和偵測只在「兩邊都有涵蓋」的時間比對，其他時間兩邊都不計。

      答案的回合      |----------------------|        第一拍 ~ 最後一拍
      系統的片段          |------------------------|
      評分範圍            |------------------|        <- 只看這一段

  落在評分範圍外的答案幀，不算漏抓；落在範圍外的偵測幀，不算誤抓。

  唯一的調整：回合範圍前後各放寬 tol 幀再取交集。
  不放寬的話，一個比發球答案早幾幀的偵測會被剪掉，
  發球答案就變成漏抓，這不公平。

  這個規則的代價：回合最後一拍之後超過 tol 幀的偵測（例如球落地後的彈跳）
  不在評分範圍內，所以這類誤抓不會被算到。

怎麼判斷「這拍有對到」
----------------------
  偵測幀和答案幀相差不超過 tol 幀，就可以配對。
  一個答案最多配一個偵測，反之亦然（一對一）。
  所有可以配的組合列出來，從相差最小的開始配，已經配掉的不再參與。

  報告的主要數字用 tol = 5 幀（25fps 約 0.2 秒），另外列出 3 與 10 幀的結果。

三種結果
--------
  matched  配對成功         系統正確抓到的擊球
  FP       多抓的（誤報）   偵測裡沒配到任何答案的拍
  FN       漏掉的（漏報）   答案裡沒配到任何偵測的拍

  Precision（精確率）= matched / 範圍內偵測數    系統說有的，有幾成是真的
  Recall  （召回率）  = matched / 範圍內答案數    真的有的，抓到幾成
  F1                  = 兩者的調和平均

時間誤差
--------
  只看配對成功的拍，定義成「偵測減答案」，單位是影格。
  正數 = 系統抓得比答案晚，負數 = 系統抓得比答案早。
  影片有 25fps 也有 30fps，所以同時換算成毫秒。

用法
----
    uv run python experiments/event_detection/eval_events.py
    uv run python experiments/event_detection/eval_events.py test2 test3
    uv run python experiments/event_detection/eval_events.py --quiet

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

# 有兩場我們下載的影片和 ShuttleSet 標記時用的版本不同：開頭多了一段賽前畫面，
# 整場的影格編號因此都往後推了一個固定量（剛好是整數分鐘）。讀答案時先把它加回去。
#   test5  +15000 幀 = 600 秒（10 分鐘）
#   test8  +12000 幀 = 480 秒（8 分鐘）
VIDEO_OFFSETS = {
    "test5": 15000,
    "test8": 12000,
}

# 報告主要數字用的配對容差（影格）。
MAIN_TOL = 5

# 另外要報的幾個容差（影格）。
TOLERANCES = (3, 5, 10)


# ------------------------------------------------------------------
# 讀檔
# ------------------------------------------------------------------

def load_answers(code: str) -> list[dict]:
    """讀 ans/<代號>/setN.csv，每一拍回傳一個 dict：

        {"frame": 影格, "set": 第幾局, "rally": 回合編號}

    影格會先加上影片版本差（VIDEO_OFFSETS，多數場次是 0）。
    """
    video_offset = VIDEO_OFFSETS.get(code, 0)
    answers = []
    for set_number in (1, 2, 3):
        path = ANS_DIR / code / f"set{set_number}.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                frame = int(round(float(row["frame_num"]))) + video_offset
                answers.append({
                    "frame": frame,
                    "set": set_number,
                    "rally": int(float(row["rally"])),
                })
    answers.sort(key=frame_of)
    return answers


def frame_of(item: dict) -> int:
    return item["frame"]


def load_detections(code: str) -> tuple[list[int], float]:
    """讀系統輸出 events.json，回傳排好的擊球幀，順便把 fps 帶出來。"""
    path = ROOT / "matches" / code / "stages" / "event_detection" / "events.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    frames = []
    for event in data["events"]:
        frames.append(int(event["frame"]))
    frames.sort()
    return frames, float(data["fps"])


def load_segments(code: str) -> list[tuple[int, int]]:
    """讀賽事影片分段的輸出，回傳 [(起, 訖), ...]，兩端都算在內。"""
    path = ROOT / "matches" / code / "stages" / "match_segmentation" / "segments.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = []
    for seg in data["segments"]:
        segments.append((int(seg["start_frame"]), int(seg["end_frame"])))
    segments.sort()
    return segments


def load_splits() -> dict[str, str]:
    """每場屬於開發集還是測試集。"""
    data = json.loads(SOURCES.read_text(encoding="utf-8"))
    splits = {}
    for video in data["videos"]:
        splits[video["code"]] = video["split"]
    return splits


# ------------------------------------------------------------------
# 回合與評分範圍
# ------------------------------------------------------------------

def rally_spans(answers: list[dict]) -> list[dict]:
    """每個回合的第一拍與最後一拍，回傳 [{"set", "rally", "first", "last", "frames"}, ...]。"""
    spans = {}
    for answer in answers:
        key = (answer["set"], answer["rally"])
        if key not in spans:
            spans[key] = {"set": answer["set"], "rally": answer["rally"], "frames": []}
        spans[key]["frames"].append(answer["frame"])

    result = []
    for span in spans.values():
        span["frames"].sort()
        span["first"] = span["frames"][0]
        span["last"] = span["frames"][-1]
        result.append(span)
    result.sort(key=first_of)
    return result


def first_of(span: dict) -> int:
    return span["first"]


def scoring_windows(spans: list[dict], segments: list[tuple[int, int]],
                    tol: int) -> list[tuple[int, int]]:
    """評分範圍 = 每個回合（前後放寬 tol 幀）和每個片段的交集。

    回傳 [(起, 訖), ...]，已排序，而且互不重疊（重疊的會併成一段）。
    """
    pieces = []
    for span in spans:
        rally_start = span["first"] - tol
        rally_end = span["last"] + tol
        for seg_start, seg_end in segments:
            start = max(rally_start, seg_start)
            end = min(rally_end, seg_end)
            if start <= end:
                pieces.append((start, end))
    pieces.sort()

    merged = []
    for start, end in pieces:
        if merged and start <= merged[-1][1]:
            last_start, last_end = merged[-1]
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def inside(frame: int, windows: list[tuple[int, int]]) -> bool:
    """這個影格有沒有落在任何一個評分範圍內。"""
    for start, end in windows:
        if start <= frame <= end:
            return True
    return False


# ------------------------------------------------------------------
# 配對
# ------------------------------------------------------------------

def pair_up(answers: list[int], detections: list[int], tol: int) -> list[tuple[int, int]]:
    """把答案幀和偵測幀配對，回傳 [(答案編號, 偵測編號), ...]。

    做法很直接：把所有相差不超過 tol 的組合列出來，從相差最小的一組開始配；
    已經配掉的就不再參與，直到沒有組合可配為止。
    """
    candidates = []
    for a_index in range(len(answers)):
        for d_index in range(len(detections)):
            distance = abs(detections[d_index] - answers[a_index])
            if distance <= tol:
                candidates.append((distance, a_index, d_index))

    candidates.sort()                       # 相差最小的排前面

    used_answers, used_detections = set(), set()
    pairs = []
    for distance, a_index, d_index in candidates:
        if a_index in used_answers or d_index in used_detections:
            continue                        # 其中一邊已經配給別人了
        used_answers.add(a_index)
        used_detections.add(d_index)
        pairs.append((a_index, d_index))
    return pairs


# ------------------------------------------------------------------
# 評分一場比賽
# ------------------------------------------------------------------

class MatchResult:
    """一場比賽在某個 tol 下的比對結果。"""

    def __init__(self, code: str, split: str, fps: float, tol: int):
        self.code = code
        self.split = split
        self.fps = fps
        self.tol = tol
        self.answers_total = 0          # 答案總拍數（含範圍外）
        self.detections_total = 0       # 偵測總拍數（含範圍外）
        self.n_answers = 0              # 評分範圍內的答案數
        self.n_detections = 0           # 評分範圍內的偵測數
        self.errors: list[int] = []     # 每個配對一筆，偵測 - 答案，單位影格
        self.false_positives: list[dict] = []
        self.false_negatives: list[dict] = []

    @property
    def n_matched(self) -> int:
        return len(self.errors)

    @property
    def precision(self) -> float:
        return self.n_matched / self.n_detections if self.n_detections else 0.0

    @property
    def recall(self) -> float:
        return self.n_matched / self.n_answers if self.n_answers else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def position_in_rally(answer: dict, spans: list[dict]) -> str:
    """漏掉的那拍是回合裡的哪一拍：發球、最後一拍，還是中段。"""
    for span in spans:
        if span["set"] == answer["set"] and span["rally"] == answer["rally"]:
            if answer["frame"] == span["first"]:
                return "發球"
            if answer["frame"] == span["last"]:
                return "最後一拍"
            return "回合中段"
    return "?"


def position_of_detection(frame: int, spans: list[dict]) -> tuple[str, str]:
    """多抓的那拍落在哪個回合的哪裡，回傳 (位置, "set-rally")。"""
    nearest = None
    nearest_distance = None
    for span in spans:
        if frame < span["first"]:
            distance = span["first"] - frame
        elif frame > span["last"]:
            distance = frame - span["last"]
        else:
            distance = 0
        if nearest is None or distance < nearest_distance:
            nearest = span
            nearest_distance = distance

    label = f"{nearest['set']}-{nearest['rally']}"
    if frame < nearest["first"]:
        return "發球之前", label
    if frame > nearest["last"]:
        return "最後一拍之後", label
    return "回合中段", label


def nearest_distance(frame: int, others: list[int]) -> int | None:
    """frame 到 others 裡最近一個的距離（影格）。"""
    best = None
    for other in others:
        distance = abs(frame - other)
        if best is None or distance < best:
            best = distance
    return best


def evaluate(code: str, split: str, tol: int) -> MatchResult:
    answers = load_answers(code)
    detections, fps = load_detections(code)
    segments = load_segments(code)
    spans = rally_spans(answers)
    windows = scoring_windows(spans, segments, tol)

    result = MatchResult(code, split, fps, tol)
    result.answers_total = len(answers)
    result.detections_total = len(detections)

    # 只留評分範圍內的答案與偵測
    kept_answers = []
    for answer in answers:
        if inside(answer["frame"], windows):
            kept_answers.append(answer)
    kept_detections = []
    for frame in detections:
        if inside(frame, windows):
            kept_detections.append(frame)
    result.n_answers = len(kept_answers)
    result.n_detections = len(kept_detections)

    # 一個評分範圍一個範圍地配對。範圍之間不重疊，所以不會跨範圍配錯。
    answer_frames = []
    for answer in kept_answers:
        answer_frames.append(answer["frame"])

    matched_answers = set()
    matched_detections = set()
    for start, end in windows:
        a_indexes = []
        for i in range(len(answer_frames)):
            if start <= answer_frames[i] <= end:
                a_indexes.append(i)
        d_indexes = []
        for i in range(len(kept_detections)):
            if start <= kept_detections[i] <= end:
                d_indexes.append(i)

        window_answers = []
        for i in a_indexes:
            window_answers.append(answer_frames[i])
        window_detections = []
        for i in d_indexes:
            window_detections.append(kept_detections[i])

        for a_local, d_local in pair_up(window_answers, window_detections, tol):
            a_index = a_indexes[a_local]
            d_index = d_indexes[d_local]
            matched_answers.add(a_index)
            matched_detections.add(d_index)
            result.errors.append(kept_detections[d_index] - answer_frames[a_index])

    # 沒配到的逐筆記下來，失敗案例分析用
    for i in range(len(kept_answers)):
        if i in matched_answers:
            continue
        answer = kept_answers[i]
        result.false_negatives.append({
            "frame": answer["frame"],
            "rally": f"{answer['set']}-{answer['rally']}",
            "position": position_in_rally(answer, spans),
            "nearest": nearest_distance(answer["frame"], kept_detections),
        })
    for i in range(len(kept_detections)):
        if i in matched_detections:
            continue
        frame = kept_detections[i]
        position, rally = position_of_detection(frame, spans)
        result.false_positives.append({
            "frame": frame,
            "rally": rally,
            "position": position,
            "nearest": nearest_distance(frame, answer_frames),
        })
    return result


# ------------------------------------------------------------------
# 把多場的結果加總
# ------------------------------------------------------------------

class Summary:
    """一組比賽（開發集／測試集／全部）的合計數字。"""

    def __init__(self, label: str, results: list[MatchResult]):
        self.label = label
        self.results = results
        self.answers_total = 0
        self.detections_total = 0
        self.n_answers = 0
        self.n_detections = 0
        self.n_matched = 0
        self.n_fp = 0
        self.n_fn = 0
        self.errors = []
        self.errors_ms = []
        for r in results:
            self.answers_total += r.answers_total
            self.detections_total += r.detections_total
            self.n_answers += r.n_answers
            self.n_detections += r.n_detections
            self.n_matched += r.n_matched
            self.n_fp += len(r.false_positives)
            self.n_fn += len(r.false_negatives)
            for e in r.errors:
                self.errors.append(e)
                self.errors_ms.append(e / r.fps * 1000)

    @property
    def precision(self) -> float:
        return self.n_matched / self.n_detections if self.n_detections else 0.0

    @property
    def recall(self) -> float:
        return self.n_matched / self.n_answers if self.n_answers else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def make_summaries(results: list[MatchResult]) -> list[Summary]:
    dev, test = [], []
    for r in results:
        if r.split == "dev":
            dev.append(r)
        elif r.split == "test":
            test.append(r)
    return [Summary("開發集", dev), Summary("測試集", test), Summary("合計", results)]


def describe_errors(errors: list) -> dict:
    """一串誤差的中位數、平均（偏早或偏晚）、平均絕對誤差、最大偏移。"""
    if not errors:
        return {"median": 0.0, "mean": 0.0, "mae": 0.0, "min": 0.0, "max": 0.0}
    absolute = []
    for e in errors:
        absolute.append(abs(e))
    return {
        "median": statistics.median(errors),
        "mean": statistics.fmean(errors),
        "mae": statistics.fmean(absolute),     # 正負不相消，看絕對偏差
        "min": min(errors),
        "max": max(errors),
    }


def within(errors: list[int], frames: int) -> float:
    """誤差落在 ±frames 影格以內的比例。"""
    if not errors:
        return 0.0
    count = 0
    for e in errors:
        if abs(e) <= frames:
            count += 1
    return count / len(errors)


def worst_of(stats: dict) -> float:
    """最大偏移：正負兩端取絕對值較大的那個，保留正負號。"""
    if abs(stats["max"]) >= abs(stats["min"]):
        return stats["max"]
    return stats["min"]


# ------------------------------------------------------------------
# 印到畫面
# ------------------------------------------------------------------

def print_match(result: MatchResult) -> None:
    stats = describe_errors(result.errors)
    print(f"\n=== {result.code}  ({result.split}, {result.fps:g}fps, tol={result.tol}) ===")
    print(f"  範圍內  答案 {result.n_answers}/{result.answers_total} 拍、"
          f"偵測 {result.n_detections}/{result.detections_total} 拍")
    print(f"  配對 {result.n_matched}、多抓 {len(result.false_positives)}、"
          f"漏掉 {len(result.false_negatives)}")
    print(f"  P={result.precision:7.2%}  R={result.recall:7.2%}  F1={result.f1:7.2%}")
    print(f"  時間誤差  中位數 {stats['median']:+.1f}  平均 {stats['mean']:+.2f}  "
          f"MAE {stats['mae']:.2f}  範圍 {stats['min']:+.0f} ~ {stats['max']:+.0f} 影格")


def print_summary(summary: Summary) -> None:
    stats = describe_errors(summary.errors)
    print(f"\n--- {summary.label} ({len(summary.results)} 場) ---")
    print(f"  範圍內  答案 {summary.n_answers}/{summary.answers_total} 拍、"
          f"偵測 {summary.n_detections}/{summary.detections_total} 拍")
    print(f"  配對 {summary.n_matched}、多抓 {summary.n_fp}、漏掉 {summary.n_fn}")
    print(f"  P={summary.precision:7.2%}  R={summary.recall:7.2%}  F1={summary.f1:7.2%}")
    print(f"  時間誤差  中位數 {stats['median']:+.1f}  平均 {stats['mean']:+.2f}  "
          f"MAE {stats['mae']:.2f}  範圍 {stats['min']:+.0f} ~ {stats['max']:+.0f} 影格")


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
        stats = describe_errors(r.errors)
        rows.append({
            "match": r.code, "split": r.split, "fps": r.fps, "tol": r.tol,
            "answers_total": r.answers_total, "answers_scored": r.n_answers,
            "detections_total": r.detections_total, "detections_scored": r.n_detections,
            "matched": r.n_matched,
            "fp": len(r.false_positives), "fn": len(r.false_negatives),
            "precision": round(r.precision * 100, 2),
            "recall": round(r.recall * 100, 2),
            "f1": round(r.f1 * 100, 2),
            "error_median": stats["median"], "error_mean": round(stats["mean"], 2),
            "error_mae": round(stats["mae"], 2),
            "error_min": stats["min"], "error_max": stats["max"],
        })
    write_csv(RESULTS_DIR / "per_match.csv", rows)


def write_tolerance(results_by_tol: dict[int, list[MatchResult]]) -> None:
    rows = []
    for tol in TOLERANCES:
        for s in make_summaries(results_by_tol[tol]):
            rows.append({
                "tol": tol, "scope": s.label,
                "answers": s.n_answers, "detections": s.n_detections,
                "matched": s.n_matched, "fp": s.n_fp, "fn": s.n_fn,
                "precision": round(s.precision * 100, 2),
                "recall": round(s.recall * 100, 2),
                "f1": round(s.f1 * 100, 2),
            })
    write_csv(RESULTS_DIR / "tolerance.csv", rows)


def write_mismatches(results: list[MatchResult]) -> None:
    """所有多抓和漏掉的拍，逐筆列出來，供失敗案例分析用。"""
    rows = []
    for r in results:
        for kind, items in (("FN 漏掉", r.false_negatives), ("FP 多抓", r.false_positives)):
            for item in items:
                rows.append({
                    "match": r.code, "split": r.split, "kind": kind,
                    "frame": item["frame"],
                    "sec": round(item["frame"] / r.fps, 2),
                    "rally": item["rally"],
                    "position": item["position"],
                    "nearest_other_frames": item["nearest"],
                })
    rows.sort(key=mismatch_order)
    write_csv(RESULTS_DIR / "mismatches.csv", rows)


def mismatch_order(row: dict) -> tuple:
    return (row["split"], row["match"], row["frame"])


def count_positions(results: list[MatchResult], kind: str) -> dict[str, int]:
    """把多抓或漏掉的拍，依它在回合裡的位置數一數。"""
    counts = {}
    for r in results:
        items = r.false_negatives if kind == "FN" else r.false_positives
        for item in items:
            counts[item["position"]] = counts.get(item["position"], 0) + 1
    return counts


def count_near_misses(results: list[MatchResult], kind: str) -> tuple[int, int]:
    """多抓或漏掉的拍裡，有幾拍「差一點」（對面最近一拍在 tol+1 ~ 2*tol 影格內）。

    回傳 (差一點, 真的沒有)。
    """
    near, far = 0, 0
    for r in results:
        items = r.false_negatives if kind == "FN" else r.false_positives
        for item in items:
            distance = item["nearest"]
            if distance is not None and distance <= 2 * r.tol:
                near += 1
            else:
                far += 1
    return near, far


def write_report_tables(results: list[MatchResult],
                        results_by_tol: dict[int, list[MatchResult]]) -> None:
    """產生可以直接貼進 docs/report.md 的表格。"""
    summaries = make_summaries(results)
    total = summaries[2]

    lines = ["# 擊球時刻偵測：實驗結果表", "",
             "由 `eval_events.py` 自動產生，請勿手改。", "",
             "只在「答案回合（前後放寬 tol）∩ 系統片段」內評分。", "",
             "## 偵測準確率", "",
             f"配對規則：|偵測 - 答案| <= {MAIN_TOL} 影格，一對一。", "",
             "| 比賽 | 答案 ans | 輸出 prog | matched | FP | FN | Precision | Recall | F1 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]

    def accuracy_row(label, n_answers, n_detections, matched, fp, fn, p, r, f1, bold=False):
        cells = [label, n_answers, n_detections, matched, fp, fn,
                 f"{p * 100:.2f}", f"{r * 100:.2f}", f"{f1 * 100:.2f}"]
        if bold:
            cells = [f"**{c}**" for c in cells]
        return "| " + " | ".join(str(c) for c in cells) + " |"

    def error_row(label, stats, errors, bold=False):
        cells = [label, f"{stats['median']:+.1f}", f"{stats['mean']:+.2f}",
                 f"{stats['mae']:.2f}", f"{worst_of(stats):+.0f}",
                 f"{within(errors, 1) * 100:.1f}%", f"{within(errors, 2) * 100:.1f}%"]
        if bold:
            cells = [f"**{c}**" for c in cells]
        return "| " + " | ".join(cells) + " |"

    for split_label, split_key, s in (("開發集", "dev", summaries[0]),
                                      ("測試集", "test", summaries[1])):
        for r in results:
            if r.split == split_key:
                lines.append(accuracy_row(r.code, r.n_answers, r.n_detections, r.n_matched,
                                          len(r.false_positives), len(r.false_negatives),
                                          r.precision, r.recall, r.f1))
        lines.append(accuracy_row(f"{split_label}小計", s.n_answers, s.n_detections,
                                  s.n_matched, s.n_fp, s.n_fn,
                                  s.precision, s.recall, s.f1, bold=True))
    lines.append(accuracy_row("合計", total.n_answers, total.n_detections, total.n_matched,
                              total.n_fp, total.n_fn,
                              total.precision, total.recall, total.f1, bold=True))

    lines += ["", "### 評分範圍涵蓋了多少", "",
              "| 範圍 | 答案（範圍內/總數） | 偵測（範圍內/總數） |",
              "| --- | ---: | ---: |"]
    for s in summaries:
        lines.append(f"| {s.label} | {s.n_answers}/{s.answers_total} "
                     f"({s.n_answers / s.answers_total * 100:.1f}%) | "
                     f"{s.n_detections}/{s.detections_total} "
                     f"({s.n_detections / s.detections_total * 100:.1f}%) |")

    lines += ["", "## 時間誤差", "",
              "誤差 = 偵測 - 答案（影格），只算配對成功的拍。正數代表系統抓得比答案晚。", "",
              "| 比賽 | 中位數 | 平均 | MAE | 最大偏移 | ±1 影格內 | ±2 影格內 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for split_label, split_key, s in (("開發集", "dev", summaries[0]),
                                      ("測試集", "test", summaries[1])):
        for r in results:
            if r.split == split_key:
                lines.append(error_row(r.code, describe_errors(r.errors), r.errors))
        lines.append(error_row(f"{split_label}小計", describe_errors(s.errors),
                               s.errors, bold=True))
    lines.append(error_row("合計", describe_errors(total.errors), total.errors, bold=True))

    lines += ["", "### 以毫秒計（影片有 25 與 30 fps，跨場比較用）", "",
              "| 範圍 | 中位數 | 平均 | MAE | 最大偏移 |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for s in summaries:
        stats = describe_errors(s.errors_ms)
        lines.append(f"| {s.label} | {stats['median']:+.1f} ms | {stats['mean']:+.1f} ms | "
                     f"{stats['mae']:.1f} ms | {worst_of(stats):+.0f} ms |")

    lines += ["", "## 容差敏感度", "",
              "| tol（影格） | 範圍 | Precision | Recall | F1 |",
              "| ---: | --- | ---: | ---: | ---: |"]
    for tol in TOLERANCES:
        for s in make_summaries(results_by_tol[tol]):
            lines.append(f"| {tol} | {s.label} | {s.precision * 100:.2f} | "
                         f"{s.recall * 100:.2f} | {s.f1 * 100:.2f} |")

    lines += ["", "## 失配出現在回合的哪裡", "",
              "逐筆明細見 `mismatches.csv`。", "",
              "| 種類 | 位置 | 開發集 | 測試集 | 合計 |",
              "| --- | --- | ---: | ---: | ---: |"]
    for kind, positions in (("FN 漏掉", ("發球", "回合中段", "最後一拍")),
                            ("FP 多抓", ("發球之前", "回合中段", "最後一拍之後"))):
        key = kind[:2]
        dev_counts = count_positions(summaries[0].results, key)
        test_counts = count_positions(summaries[1].results, key)
        for position in positions:
            d = dev_counts.get(position, 0)
            t = test_counts.get(position, 0)
            lines.append(f"| {kind} | {position} | {d} | {t} | {d + t} |")

    lines += ["", "## 失配離對面最近的一拍有多遠", "",
              f"「差一點」= 最近的另一邊在 {MAIN_TOL + 1}–{2 * MAIN_TOL} 影格內："
              "系統其實抓到了這拍，只是時間超出容差，所以同時記成一個 FP 和一個 FN。", "",
              "| 種類 | 範圍 | 差一點 | 真的沒有 |",
              "| --- | --- | ---: | ---: |"]
    for kind in ("FN", "FP"):
        for s in summaries:
            near, far = count_near_misses(s.results, kind)
            lines.append(f"| {kind} | {s.label} | {near} | {far} |")
    lines.append("")

    (RESULTS_DIR / "report_tables.md").write_text("\n".join(lines), encoding="utf-8")


# ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="擊球時刻偵測的準確率評分")
    parser.add_argument("matches", nargs="*", help="只評這幾場（預設：全部）")
    parser.add_argument("-q", "--quiet", action="store_true", help="不印每場的細節")
    parser.add_argument("--no-write", action="store_true", help="不寫 results/")
    parser.add_argument("--write", action="store_true",
                        help="指定場次時仍強制覆寫 results/"
                             "（預設只有評估全部場次時才寫，避免用部分結果蓋掉共用報表）")
    args = parser.parse_args()

    splits = load_splits()
    codes = args.matches
    if not codes:
        codes = []
        for code in splits:
            if (ANS_DIR / code).is_dir():
                codes.append(code)

    results_by_tol = {}
    for tol in TOLERANCES:
        results_by_tol[tol] = []
        for code in codes:
            results_by_tol[tol].append(evaluate(code, splits.get(code, "?"), tol))
    results = results_by_tol[MAIN_TOL]

    if not args.quiet:
        for result in results:
            print_match(result)

    print("\n" + "=" * 66)
    print(f"tol = {MAIN_TOL} 影格")
    for summary in make_summaries(results):
        if summary.results:
            print_summary(summary)

    print("\n容差敏感度（合計）")
    for tol in TOLERANCES:
        s = make_summaries(results_by_tol[tol])[2]
        print(f"  tol={tol:2d}  P={s.precision:7.2%}  R={s.recall:7.2%}  F1={s.f1:7.2%}")

    partial = bool(args.matches)
    if args.no_write:
        pass
    elif partial and not args.write:
        print("\n（只評了指定場次，未覆寫 results/，以免蓋掉全體報表；"
              "要覆寫請加 --write，或改跑全部場次）")
    else:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        write_per_match(results)
        write_tolerance(results_by_tol)
        write_mismatches(results)
        write_report_tables(results, results_by_tol)
        print(f"\n結果已寫入 {RESULTS_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
