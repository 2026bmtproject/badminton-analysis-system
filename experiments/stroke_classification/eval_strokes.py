"""球種分類：把系統判的球種與擊球者，和 ShuttleSet 答案比對，算出報告要的數字。

答案和擊球時刻偵測是同一份（experiments/event_detection/ans/），
讀檔、影片版本差、評分範圍、配對規則也都直接沿用 eval_events.py，
所以這裡評的「拍」就是擊球時刻偵測那一節配對成功的拍。

名詞
----
  答案球種   ans/<代號>/setN.csv 的 type 欄（ShuttleSet 的 18 種），先併成 8 類再比。
  答案擊球者 同一檔的 player 欄是 A/B，要換成「上方/下方」才能和系統比，見下面。
  系統球種   matches/<代號>/stages/stroke_classification/strokes.json 的 stroke_type
  系統擊球者 同一檔的 player（top / bottom）

18 種併成 8 類
--------------
  分兩步，第一步照 BST 訓練時的併法（BST repo 的 ShuttleSet_merged/class_total.xlsx），
  第二步照系統輸出的 8 類（modules/common/bst/classes.py 的 BASE12_TO_8）：

    點扣 -> 殺球                            -> 殺球
    防守回挑 -> 挑球                        -> 高遠球
    小平球、後場抽平球、防守回抽 -> 平球      -> 平快球
    過度切球（BST 寫作過渡切球）-> 切球       -> 切球
    其餘 12 種原名                          -> 見 TO_8

  答案是「未知球種」的拍不計分。系統判成「未知球種」算錯。

答案擊球者：A/B 換成上方/下方
------------------------------
  ShuttleSet 每一拍都記了擊球者與對手在畫面上的位置（player_location_y、
  opponent_location_y，數字越小越上面）。擊球者比對手高，就是上方。

  少數拍沒有位置資料。同一回合裡 A、B 各站哪一邊不會變，
  所以用同一回合其他有資料的拍投票，決定 A 在上方還是下方。

  對照：BST 自己的做法是查 match.csv 的 downcourt 欄再加上換場規則，
  兩種做法在 14 場裡有 99.8% 的拍一致。

哪些比賽 BST 看過
-----------------
  BST 的權重是用 ShuttleSet 訓練的，14 場裡有 11 場在它的訓練集，
  在這些場上量到的準確率偏樂觀。只有 3 場 BST 沒看過（見 BST_UNSEEN），
  報告的主要數字用這 3 場。

四個數字
--------
  球種準確率   配對成功、答案不是未知球種的拍裡，系統 8 類判對的比例。
  Macro-F1     8 類各自的 F1 取平均，少數類和多數類一樣重。
  端到端準確率 分母換成範圍內所有可計分的答案拍，漏抓的也算錯
               （= 擊球時刻 Recall × 球種準確率，大約）。
  擊球者準確率 配對成功的拍裡，上方/下方判對的比例。

  另外附「全部猜最多的那一類」的準確率當基準。

用答案擊球幀重跑（run_oracle.py）
---------------------------------
  results/oracle.csv 若存在，會多算一組「擊球幀完全正確時」的準確率，
  並用來把判錯的拍分成「錯在上游」或「錯在 BST」。

用法
----
    uv run python experiments/stroke_classification/eval_strokes.py
    uv run python experiments/stroke_classification/eval_strokes.py test5 test8

  跑全部場次時會寫 results/；只指定部分場次時預設不寫，要寫再加 --write。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS_DIR = HERE / "results"
ORACLE_CSV = RESULTS_DIR / "oracle.csv"

# 讀答案、評分範圍、配對，全部沿用擊球時刻偵測的評分程式
sys.path.insert(0, str(ROOT / "experiments" / "event_detection"))
import eval_events                                                    # noqa: E402

ANS_DIR = eval_events.ANS_DIR
TOL = eval_events.MAIN_TOL

# BST 訓練、驗證都沒用過的場次（BST repo 的 ShuttleSet/gen_my_dataset.py 第 222-224 行）
#   PC_vs_PVS_2020  ShuttleSet id 42，BST 的測試集
#   test5           id 10，BST 整場排除（flaw_shot_records.csv："whole labeling incorrect"）
#   test8           id 9， 同上
# 其他 11 場都在 BST 的訓練集。
BST_UNSEEN = {"PC_vs_PVS_2020", "test5", "test8"}

UNKNOWN = "未知球種"

# 答案 18 種 -> 8 類
TO_8 = {
    "放小球": "小球",
    "擋小球": "小球",
    "殺球": "殺球",
    "點扣": "殺球",
    "挑球": "高遠球",
    "防守回挑": "高遠球",
    "長球": "高遠球",
    "平球": "平快球",
    "小平球": "平快球",
    "後場抽平球": "平快球",
    "防守回抽": "平快球",
    "推球": "平快球",
    "切球": "切球",
    "過度切球": "切球",
    "過渡切球": "切球",
    "撲球": "撲球",
    "勾球": "勾球",
    "發短球": "發球",
    "發長球": "發球",
}

CLASSES_8 = ["高遠球", "發球", "小球", "平快球", "殺球", "切球", "撲球", "勾球"]


# ------------------------------------------------------------------
# 讀檔
# ------------------------------------------------------------------

def load_answers(code: str) -> list[dict]:
    """讀答案，每一拍回傳：

        {"frame", "set", "rally", "stroke"（8 類，未知球種為 None）, "side"（top/bottom/None）}

    影格的讀法和 eval_events.load_answers 完全一樣（含影片版本差）。
    """
    video_offset = eval_events.VIDEO_OFFSETS.get(code, 0)
    rows = []
    for set_number in (1, 2, 3):
        path = ANS_DIR / code / f"set{set_number}.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                rows.append({
                    "frame": int(round(float(row["frame_num"]))) + video_offset,
                    "set": set_number,
                    "rally": int(float(row["rally"])),
                    "raw_type": row["type"],
                    "player": row["player"],              # A 或 B
                    "player_y": to_number(row["player_location_y"]),
                    "opponent_y": to_number(row["opponent_location_y"]),
                })

    a_side = side_of_a_in_each_rally(rows)

    answers = []
    for row in rows:
        stroke = None
        if row["raw_type"] != UNKNOWN:
            stroke = TO_8[row["raw_type"]]      # 不在表裡會直接報錯，代表併類表有缺口

        side = None
        a_is = a_side.get((row["set"], row["rally"]))
        if a_is is not None:
            if row["player"] == "A":
                side = a_is
            else:
                side = other_side(a_is)

        answers.append({
            "frame": row["frame"],
            "set": row["set"],
            "rally": row["rally"],
            "stroke": stroke,
            "side": side,
        })
    answers.sort(key=eval_events.frame_of)
    return answers


def to_number(text: str) -> float | None:
    if text == "":
        return None
    return float(text)


def other_side(side: str) -> str:
    if side == "top":
        return "bottom"
    return "top"


def side_of_a_in_each_rally(rows: list[dict]) -> dict:
    """每個回合裡 A 站在上方還是下方，回傳 {(set, rally): "top" / "bottom"}。

    每一拍如果有位置資料，就投一票：擊球者比對手高（y 小）就是上方。
    這一票換算成「A 在哪一邊」，同一回合的票加起來，多的那邊贏。
    """
    votes = {}          # (set, rally) -> [A 在上方的票, A 在下方的票]
    for row in rows:
        if row["player_y"] is None or row["opponent_y"] is None:
            continue
        hitter_is_top = row["player_y"] < row["opponent_y"]
        if row["player"] == "A":
            a_is_top = hitter_is_top
        else:
            a_is_top = not hitter_is_top

        key = (row["set"], row["rally"])
        if key not in votes:
            votes[key] = [0, 0]
        if a_is_top:
            votes[key][0] += 1
        else:
            votes[key][1] += 1

    result = {}
    for key, (top_votes, bottom_votes) in votes.items():
        if top_votes > bottom_votes:
            result[key] = "top"
        elif bottom_votes > top_votes:
            result[key] = "bottom"
    return result


def load_predictions(code: str) -> dict[int, dict]:
    """讀系統輸出 strokes.json，回傳 {影格: {"stroke", "side"}}。"""
    path = ROOT / "matches" / code / "stages" / "stroke_classification" / "strokes.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    predictions = {}
    for item in data["strokes"]:
        predictions[int(item["frame"])] = {
            "stroke": item["stroke_type"],
            "side": item.get("player"),
        }
    return predictions


def load_oracle() -> dict[str, dict[int, dict]]:
    """讀 run_oracle.py 的結果，回傳 {代號: {影格: {"stroke", "side"}}}。沒有檔案就回傳空的。"""
    oracle = {}
    if not ORACLE_CSV.is_file():
        return oracle
    with ORACLE_CSV.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["match"] not in oracle:
                oracle[row["match"]] = {}
            oracle[row["match"]][int(row["frame"])] = {
                "stroke": row["stroke_type"],
                "side": row["player"] or None,
            }
    return oracle


# ------------------------------------------------------------------
# 評分一場比賽
# ------------------------------------------------------------------

def evaluate(code: str, split: str, oracle: dict[int, dict] | None) -> dict:
    """回傳這場的所有評分紀錄：

        {"code", "split", "seen", "fps",
         "scorable": 範圍內可計分（球種不是未知）的答案拍數,
         "pairs":  配對成功的拍，一拍一個 dict,
         "oracle": 用答案擊球幀重跑的結果，一拍一個 dict（沒跑過就是空的）}
    """
    answers = load_answers(code)
    detections, fps = eval_events.load_detections(code)
    predictions = load_predictions(code)
    segments = eval_events.load_segments(code)
    spans = eval_events.rally_spans(answers)
    windows = eval_events.scoring_windows(spans, segments, TOL)

    # 只留評分範圍內的答案與偵測（和 eval_events.evaluate 一樣）
    kept_answers = []
    for answer in answers:
        if eval_events.inside(answer["frame"], windows):
            kept_answers.append(answer)
    kept_detections = []
    for frame in detections:
        if eval_events.inside(frame, windows):
            kept_detections.append(frame)

    scorable = 0
    for answer in kept_answers:
        if answer["stroke"] is not None:
            scorable += 1

    # 一個評分範圍一個範圍地配對（和 eval_events.evaluate 一樣）
    pairs = []
    for start, end in windows:
        window_answers = []
        for answer in kept_answers:
            if start <= answer["frame"] <= end:
                window_answers.append(answer)
        window_detections = []
        for frame in kept_detections:
            if start <= frame <= end:
                window_detections.append(frame)

        answer_frames = []
        for answer in window_answers:
            answer_frames.append(answer["frame"])

        for a_index, d_index in eval_events.pair_up(answer_frames, window_detections, TOL):
            answer = window_answers[a_index]
            detection = window_detections[d_index]
            predicted = predictions[detection]
            pairs.append({
                "answer_frame": answer["frame"],
                "detection_frame": detection,
                "rally": f"{answer['set']}-{answer['rally']}",
                "answer_stroke": answer["stroke"],
                "answer_side": answer["side"],
                "predicted_stroke": predicted["stroke"],
                "predicted_side": predicted["side"],
            })
    pairs.sort(key=answer_frame_of)

    # 用答案擊球幀重跑的結果：範圍內的每一拍都比
    oracle_rows = []
    if oracle:
        for answer in kept_answers:
            predicted = oracle.get(answer["frame"])
            if predicted is None:
                continue
            oracle_rows.append({
                "answer_frame": answer["frame"],
                "answer_stroke": answer["stroke"],
                "answer_side": answer["side"],
                "predicted_stroke": predicted["stroke"],
                "predicted_side": predicted["side"],
            })

    return {
        "code": code,
        "split": split,
        "seen": code not in BST_UNSEEN,
        "fps": fps,
        "scorable": scorable,
        "pairs": pairs,
        "oracle": oracle_rows,
    }


def answer_frame_of(item: dict) -> int:
    return item["answer_frame"]


# ------------------------------------------------------------------
# 算數字
# ------------------------------------------------------------------

def stroke_scores(rows: list[dict]) -> dict:
    """一串拍（配對成功的，或 oracle 的）的球種數字。答案是未知球種的拍不算。"""
    total = 0
    correct = 0
    predicted_unknown = 0
    confusion = {}                  # (答案, 系統) -> 拍數
    for row in rows:
        truth = row["answer_stroke"]
        if truth is None:
            continue
        guess = row["predicted_stroke"]
        total += 1
        if guess == truth:
            correct += 1
        if guess == UNKNOWN:
            predicted_unknown += 1
        key = (truth, guess)
        confusion[key] = confusion.get(key, 0) + 1

    per_class = {}
    f1_sum = 0.0
    for name in CLASSES_8:
        hit = confusion.get((name, name), 0)
        support = 0                 # 答案是這類的拍數
        predicted = 0               # 系統判成這類的拍數
        for (truth, guess), n in confusion.items():
            if truth == name:
                support += n
            if guess == name:
                predicted += n
        precision = hit / predicted if predicted else 0.0
        recall = hit / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_class[name] = {"support": support, "predicted": predicted, "correct": hit,
                           "precision": precision, "recall": recall, "f1": f1}
        f1_sum += f1

    # 基準：全部猜答案裡最多的那一類
    biggest = 0
    for name in CLASSES_8:
        if per_class[name]["support"] > biggest:
            biggest = per_class[name]["support"]

    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "macro_f1": f1_sum / len(CLASSES_8),
        "baseline": biggest / total if total else 0.0,
        "predicted_unknown": predicted_unknown,
        "per_class": per_class,
        "confusion": confusion,
    }


def side_scores(rows: list[dict]) -> dict:
    """擊球者（上方/下方）判對的比例。答案沒有擊球者的拍不算；系統沒給擊球者算錯。"""
    total = 0
    correct = 0
    for row in rows:
        if row["answer_side"] is None:
            continue
        total += 1
        if row["predicted_side"] == row["answer_side"]:
            correct += 1
    return {"total": total, "correct": correct,
            "accuracy": correct / total if total else 0.0}


class Group:
    """一組比賽（BST 沒看過／看過／合計）合起來的數字。"""

    def __init__(self, label: str, matches: list[dict]):
        self.label = label
        self.matches = matches
        self.scorable = 0
        self.pairs = []
        self.oracle = []
        for m in matches:
            self.scorable += m["scorable"]
            self.pairs.extend(m["pairs"])
            self.oracle.extend(m["oracle"])
        self.stroke = stroke_scores(self.pairs)
        self.side = side_scores(self.pairs)
        self.oracle_stroke = stroke_scores(self.oracle)
        self.oracle_side = side_scores(self.oracle)

    @property
    def end_to_end(self) -> float:
        """端到端準確率：判對的拍 / 範圍內所有可計分的答案拍（漏抓也算錯）。"""
        return self.stroke["correct"] / self.scorable if self.scorable else 0.0


def make_groups(matches: list[dict]) -> list[Group]:
    unseen = []
    seen = []
    for m in matches:
        if m["seen"]:
            seen.append(m)
        else:
            unseen.append(m)
    return [Group("BST 沒看過", unseen), Group("BST 看過", seen), Group("合計", matches)]


# ------------------------------------------------------------------
# 判錯的拍：分原因
# ------------------------------------------------------------------

def wrong_strokes(matches: list[dict]) -> list[dict]:
    """每一筆球種判錯的配對拍，附上判錯的原因。

    原因依序判斷，先符合的就是它：
      判成未知       系統輸出未知球種
      錯在上游       換成答案的擊球幀重跑，就判對了
      錯在 BST       換成答案的擊球幀重跑，還是錯
      沒有重跑資料   results/oracle.csv 裡找不到這拍
    """
    rows = []
    for m in matches:
        oracle_by_frame = {}
        for row in m["oracle"]:
            oracle_by_frame[row["answer_frame"]] = row["predicted_stroke"]

        for pair in m["pairs"]:
            truth = pair["answer_stroke"]
            guess = pair["predicted_stroke"]
            if truth is None or guess == truth:
                continue

            oracle_guess = oracle_by_frame.get(pair["answer_frame"])
            if guess == UNKNOWN:
                reason = "判成未知"
            elif oracle_guess is None:
                reason = "沒有重跑資料"
            elif oracle_guess == truth:
                reason = "錯在上游"
            else:
                reason = "錯在 BST"

            rows.append({
                "match": m["code"],
                "bst_seen": "看過" if m["seen"] else "沒看過",
                "answer_frame": pair["answer_frame"],
                "detection_frame": pair["detection_frame"],
                "offset_frames": pair["detection_frame"] - pair["answer_frame"],
                "rally": pair["rally"],
                "answer": truth,
                "predicted": guess,
                "oracle": oracle_guess or "",
                "reason": reason,
            })
    return rows


def count_by(rows: list[dict], column: str) -> dict[str, int]:
    counts = {}
    for row in rows:
        counts[row[column]] = counts.get(row[column], 0) + 1
    return counts


def top_confusions(confusion: dict, how_many: int) -> list[tuple[str, str, int]]:
    """判錯的組合裡最常見的幾組，回傳 [(答案, 系統, 拍數), ...]。"""
    wrong = []
    for (truth, guess), n in confusion.items():
        if truth != guess:
            wrong.append((n, truth, guess))
    wrong.sort(reverse=True)
    result = []
    for n, truth, guess in wrong[:how_many]:
        result.append((truth, guess, n))
    return result


# ------------------------------------------------------------------
# 印到畫面
# ------------------------------------------------------------------

def print_group(group: Group) -> None:
    s = group.stroke
    print(f"\n--- {group.label}（{len(group.matches)} 場）---")
    print(f"  球種  {s['correct']}/{s['total']} = {s['accuracy']:.2%}   "
          f"Macro-F1 {s['macro_f1']:.2%}   基準 {s['baseline']:.2%}   "
          f"判成未知 {s['predicted_unknown']}")
    print(f"  端到端 {s['correct']}/{group.scorable} = {group.end_to_end:.2%}")
    print(f"  擊球者 {group.side['correct']}/{group.side['total']} = {group.side['accuracy']:.2%}")
    if group.oracle:
        o = group.oracle_stroke
        print(f"  用答案擊球幀重跑  球種 {o['correct']}/{o['total']} = {o['accuracy']:.2%}   "
              f"擊球者 {group.oracle_side['accuracy']:.2%}")


# ------------------------------------------------------------------
# 寫檔
# ------------------------------------------------------------------

def pct(x: float) -> str:
    return f"{x * 100:.2f}"


def write_per_match(matches: list[dict]) -> None:
    rows = []
    for m in matches:
        s = stroke_scores(m["pairs"])
        side = side_scores(m["pairs"])
        o = stroke_scores(m["oracle"])
        rows.append({
            "match": m["code"],
            "split": m["split"],
            "bst_seen": "看過" if m["seen"] else "沒看過",
            "scorable_answers": m["scorable"],
            "scored": s["total"],
            "correct": s["correct"],
            "accuracy": pct(s["accuracy"]),
            "macro_f1": pct(s["macro_f1"]),
            "end_to_end": pct(s["correct"] / m["scorable"]) if m["scorable"] else "",
            "predicted_unknown": s["predicted_unknown"],
            "side_scored": side["total"],
            "side_accuracy": pct(side["accuracy"]),
            "oracle_scored": o["total"],
            "oracle_accuracy": pct(o["accuracy"]) if o["total"] else "",
        })
    eval_events.write_csv(RESULTS_DIR / "per_match.csv", rows)


def write_per_class(groups: list[Group]) -> None:
    rows = []
    for group in groups:
        for name in CLASSES_8:
            c = group.stroke["per_class"][name]
            rows.append({
                "scope": group.label, "class": name,
                "support": c["support"], "predicted": c["predicted"], "correct": c["correct"],
                "precision": pct(c["precision"]), "recall": pct(c["recall"]), "f1": pct(c["f1"]),
            })
    eval_events.write_csv(RESULTS_DIR / "per_class.csv", rows)


def write_confusion(groups: list[Group]) -> None:
    """列 = 答案，欄 = 系統。系統那邊多一欄未知球種。"""
    rows = []
    for group in groups:
        for truth in CLASSES_8:
            row = {"scope": group.label, "answer": truth}
            for guess in CLASSES_8 + [UNKNOWN]:
                row[guess] = group.stroke["confusion"].get((truth, guess), 0)
            rows.append(row)
    eval_events.write_csv(RESULTS_DIR / "confusion.csv", rows)


def write_report_tables(matches: list[dict], groups: list[Group], wrong: list[dict]) -> None:
    """產生可以直接貼進 docs/report.md 的表格。"""
    unseen = groups[0]
    lines = ["# 球種分類：實驗結果表", "",
             "由 `eval_strokes.py` 自動產生，請勿手改。", "",
             f"只看擊球時刻偵測配對成功的拍（tol = {TOL}），答案是未知球種的拍不計分。", "",
             "## 分類準確率", "",
             "| 範圍 | 場數 | 計分拍數 | 球種 Acc | Macro-F1 | 基準 | 端到端 Acc | 擊球者 Acc |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for g in groups:
        lines.append(f"| {g.label} | {len(g.matches)} | {g.stroke['total']} | "
                     f"{pct(g.stroke['accuracy'])} | {pct(g.stroke['macro_f1'])} | "
                     f"{pct(g.stroke['baseline'])} | {pct(g.end_to_end)} | "
                     f"{pct(g.side['accuracy'])} |")

    lines += ["", "基準 = 全部猜答案裡最多的那一類。"
              "端到端 = 分母換成範圍內所有可計分答案拍，漏抓也算錯。", "",
              "### 逐場", "",
              "| 比賽 | BST | 計分拍數 | 球種 Acc | Macro-F1 | 擊球者 Acc | 判成未知 |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for m in sorted(matches, key=seen_then_code):
        s = stroke_scores(m["pairs"])
        side = side_scores(m["pairs"])
        lines.append(f"| {m['code']} | {'看過' if m['seen'] else '沒看過'} | {s['total']} | "
                     f"{pct(s['accuracy'])} | {pct(s['macro_f1'])} | "
                     f"{pct(side['accuracy'])} | {s['predicted_unknown']} |")

    lines += ["", f"## 各球種（{unseen.label}）", "",
              "| 球種 | 答案拍數 | 系統判成 | 判對 | Precision | Recall | F1 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name in CLASSES_8:
        c = unseen.stroke["per_class"][name]
        lines.append(f"| {name} | {c['support']} | {c['predicted']} | {c['correct']} | "
                     f"{pct(c['precision'])} | {pct(c['recall'])} | {pct(c['f1'])} |")

    lines += ["", f"## 混淆矩陣（{unseen.label}）", "",
              "列 = 答案，欄 = 系統。", "",
              "| 答案 \\ 系統 | " + " | ".join(CLASSES_8) + f" | {UNKNOWN} |",
              "| --- |" + " ---: |" * (len(CLASSES_8) + 1)]
    for truth in CLASSES_8:
        cells = []
        for guess in CLASSES_8 + [UNKNOWN]:
            cells.append(str(unseen.stroke["confusion"].get((truth, guess), 0)))
        lines.append(f"| {truth} | " + " | ".join(cells) + " |")

    lines += ["", f"### 最常見的判錯組合（{unseen.label}）", "",
              "| 答案 | 系統 | 拍數 |", "| --- | --- | ---: |"]
    for truth, guess, n in top_confusions(unseen.stroke["confusion"], 8):
        lines.append(f"| {truth} | {guess} | {n} |")

    if unseen.oracle:
        lines += ["", "## 擊球幀換成答案之後", "",
                  "run_oracle.py 用答案的擊球幀重跑球種分類，其他輸入不變。"
                  "範圍內所有可計分答案拍都算（不限配對成功的拍）。", "",
                  "| 範圍 | 擊球幀 | 計分拍數 | 球種 Acc | Macro-F1 | 擊球者 Acc |",
                  "| --- | --- | ---: | ---: | ---: | ---: |"]
        for g in groups:
            lines.append(f"| {g.label} | 系統偵測 | {g.stroke['total']} | "
                         f"{pct(g.stroke['accuracy'])} | {pct(g.stroke['macro_f1'])} | "
                         f"{pct(g.side['accuracy'])} |")
            lines.append(f"| {g.label} | 答案 | {g.oracle_stroke['total']} | "
                         f"{pct(g.oracle_stroke['accuracy'])} | "
                         f"{pct(g.oracle_stroke['macro_f1'])} | "
                         f"{pct(g.oracle_side['accuracy'])} |")

    lines += ["", "## 判錯的原因", "",
              "逐筆明細見 `errors.csv`。", "",
              "| 原因 | BST 沒看過 | BST 看過 |", "| --- | ---: | ---: |"]
    unseen_wrong = []
    seen_wrong = []
    for row in wrong:
        if row["bst_seen"] == "沒看過":
            unseen_wrong.append(row)
        else:
            seen_wrong.append(row)
    unseen_counts = count_by(unseen_wrong, "reason")
    seen_counts = count_by(seen_wrong, "reason")
    for reason in ("錯在上游", "錯在 BST", "判成未知", "沒有重跑資料"):
        u = unseen_counts.get(reason, 0)
        s = seen_counts.get(reason, 0)
        if u or s:
            lines.append(f"| {reason} | {u} | {s} |")
    lines.append(f"| **合計** | **{len(unseen_wrong)}** | **{len(seen_wrong)}** |")

    lines += ["", f"### 錯在上游的拍，擊球幀差了多少（{unseen.label}）", "",
              "| 偵測與答案相差 | 拍數 |", "| --- | ---: |"]
    near = 0
    far = 0
    for row in unseen_wrong:
        if row["reason"] != "錯在上游":
            continue
        if abs(row["offset_frames"]) <= 2:
            near += 1
        else:
            far += 1
    lines.append(f"| 0–2 影格 | {near} |")
    lines.append(f"| 3–{TOL} 影格 | {far} |")
    lines.append("")

    (RESULTS_DIR / "report_tables.md").write_text("\n".join(lines), encoding="utf-8")


def seen_then_code(m: dict) -> tuple:
    return (m["seen"], m["code"])


# ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="球種分類的準確率評分")
    parser.add_argument("matches", nargs="*", help="只評這幾場（預設：全部）")
    parser.add_argument("-q", "--quiet", action="store_true", help="不印每場的細節")
    parser.add_argument("--no-write", action="store_true", help="不寫 results/")
    parser.add_argument("--write", action="store_true",
                        help="指定場次時仍強制覆寫 results/")
    args = parser.parse_args()

    splits = eval_events.load_splits()
    codes = args.matches
    if not codes:
        codes = []
        for code in splits:
            if (ANS_DIR / code).is_dir():
                codes.append(code)

    oracle = load_oracle()
    if not oracle:
        print("（沒有 results/oracle.csv，略過「用答案擊球幀重跑」的比較；"
              "要的話先跑 run_oracle.py）")

    matches = []
    for code in codes:
        matches.append(evaluate(code, splits.get(code, "?"), oracle.get(code)))

    if not args.quiet:
        for m in matches:
            s = stroke_scores(m["pairs"])
            side = side_scores(m["pairs"])
            print(f"{m['code']:16} {'看過' if m['seen'] else '沒看過':4}  "
                  f"球種 {s['correct']:4}/{s['total']:<4} = {s['accuracy']:6.2%}   "
                  f"擊球者 {side['accuracy']:6.2%}")

    groups = make_groups(matches)
    print("\n" + "=" * 66)
    print(f"tol = {TOL} 影格")
    for group in groups:
        if group.matches:
            print_group(group)

    wrong = wrong_strokes(matches)

    partial = bool(args.matches)
    if args.no_write:
        pass
    elif partial and not args.write:
        print("\n（只評了指定場次，未覆寫 results/；要覆寫請加 --write，或改跑全部場次）")
    else:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        write_per_match(matches)
        write_per_class(groups)
        write_confusion(groups)
        eval_events.write_csv(RESULTS_DIR / "errors.csv", wrong)
        write_report_tables(matches, groups, wrong)
        print(f"\n結果已寫入 {RESULTS_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
