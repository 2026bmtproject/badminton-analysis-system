"""場地偵測：把系統偵測的四個外角和人工真值比對，算出報告要的數字。

主指標是像素誤差，和命中率。

名詞
----
  外角            球場外緣四個角，順時針命名：
                      TL 遠端左   TR 遠端右
                      BL 近端左   BR 近端右
                  TL、TR 在畫面上方，離鏡頭遠，稱「遠端」；
                  BL、BR 在畫面下方，離鏡頭近，稱「近端」。

  角區 (zone)     球場外緣是兩條有寬度的白線（邊線與底線）。兩線在外角交叉，
                  交疊處是一個小四邊形就是角區。

                        底線  ___________________
                             |   |
                             | ■ |  <- ■ 是角區：邊線與底線重疊的那一小塊
                             |___|__________________
                             |   |
                        邊線 |   |

  偵測角點        系統輸出的四個外角座標（像素）。
                  來源：results/detected/<代號>.json

量了哪些距離（單位都是像素）
--------------------------------------
  到中心距離      偵測角點到角區中心的直線距離。角區中心 = 4 個頂點座標的平均，
                  是真實角落的點估計。這就是「像素誤差」，每個角都量。
  dx, dy          偵測角點減角區中心。dx 正 = 偏右，dy 正 = 偏下（畫面座標）。
  到角區距離      偵測角點到角區邊緣最近處的距離；落在角區內記 0。

  遠端的白線在畫面上只有幾個像素寬，近端則寬得多，所以遠端、近端分開統計。

怎麼判斷這個角有對到
------------------------------------
  角區只有 5–7px 寬，偵測角點常常就落在角落上、卻差一兩像素在角區外緣，
  這種其實已經對準。故命中判準在角區之外再給 TOLERANCE_PX 的容差：

      到角區距離 <= TOLERANCE_PX  ->  命中

  角區內的角到角區距離為 0，一律命中。純粹「落在線寬內」的嚴格命中可由
  corners.csv 的 inside_zone 欄自行統計。

用法
----
    uv run python experiments/court_detection/eval_court.py
    uv run python experiments/court_detection/eval_court.py test1 test2

  結果都會印在畫面上。寫進 results/ 的時機：跑全部場次時會寫
  只指定部分場次時預設不寫（避免用部分結果蓋掉全體報表），要寫再加 --write。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ANS_DIR = HERE / "ans"
RESULTS_DIR = HERE / "results"
DETECTED_DIR = RESULTS_DIR / "detected"   # detect_court.py
SOURCES = ROOT / "experiments" / "dataset" / "sources.json"

# court.json 的 corners 依序是 TL、TR、BR、BL。
CORNERS = ["TL", "TR", "BR", "BL"]
FAR_CORNERS = ["TL", "TR"]
NEAR_CORNERS = ["BR", "BL"]

# 命中容差：偵測角點在角區外緣這麼多像素內，仍算命中。
TOLERANCE_PX = 2.0


# ------------------------------------------------------------------
# 幾何。點一律寫成 (x, y)，角區是 4 個頂點依序繞一圈。
# ------------------------------------------------------------------

def distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    """兩點的直線距離。"""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def center_of(zone: list[tuple[float, float]]) -> tuple[float, float]:
    """角區中心：4 個頂點座標的平均。"""
    x = sum(p[0] for p in zone) / len(zone)
    y = sum(p[1] for p in zone) / len(zone)
    return (x, y)


def cross(a, b, p) -> float:
    """外積 (b - a) x (p - a)。正負號說明 p 在 a->b 這條邊的哪一側。"""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def is_inside(point, zone) -> bool:
    """點是否在角區內（含邊緣）。

    角區是凸四邊形。沿著它的 4 條邊繞一圈，點若在裡面，
    就會永遠在每條邊的同一側（外積全部 >= 0 或全部 <= 0）。
    """
    sides = []
    for i in range(len(zone)):
        a = zone[i]
        b = zone[(i + 1) % len(zone)]      # 最後一個頂點接回第一個
        sides.append(cross(a, b, point))
    all_left = all(s >= 0 for s in sides)
    all_right = all(s <= 0 for s in sides)
    return all_left or all_right


def distance_to_edge(point, a, b) -> float:
    """點到線段 a-b 的最短距離。

    先找線段上離點最近的位置：把點投影到 a->b 方向上，
    投影比例 t 夾在 0 到 1 之間（超出就取端點），再量距離。
    """
    ab_x, ab_y = b[0] - a[0], b[1] - a[1]
    length_squared = ab_x * ab_x + ab_y * ab_y
    if length_squared == 0:
        return distance(point, a)
    t = ((point[0] - a[0]) * ab_x + (point[1] - a[1]) * ab_y) / length_squared
    t = max(0.0, min(1.0, t))
    nearest = (a[0] + t * ab_x, a[1] + t * ab_y)
    return distance(point, nearest)


def distance_to_zone(point, zone) -> float:
    """點到角區邊緣的最短距離：4 條邊各量一次，取最小。"""
    return min(
        distance_to_edge(point, zone[i], zone[(i + 1) % len(zone)])
        for i in range(len(zone))
    )


# ------------------------------------------------------------------
# 讀檔
# ------------------------------------------------------------------

def load_splits() -> dict[str, str]:
    """代號 -> "dev" 或 "test"。"""
    data = json.loads(SOURCES.read_text(encoding="utf-8"))
    return {v["code"]: v["split"] for v in data["videos"]}


def load_truth(code: str) -> dict[str, list[tuple[float, float]]]:
    """真值：角名 -> 角區 4 個頂點。"""
    data = json.loads((ANS_DIR / f"{code}.json").read_text(encoding="utf-8"))
    return {name: [tuple(p) for p in data["corners"][name]] for name in CORNERS}


def load_output(code: str) -> tuple[dict[str, tuple[float, float]], bool] | None:
    """系統輸出：(角名 -> 偵測角點, 偵測是否失敗)。沒有輸出檔回傳 None。

    偵測失敗時系統會退回畫面四角當佔位，這些點不是偵測結果，
    所以另外回報「偵測失敗」，不拿來算距離。
    """
    path = DETECTED_DIR / f"{code}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    corners = data["courts"][0]["corners"]
    points = {name: tuple(corners[i]) for i, name in enumerate(CORNERS)}
    return points, bool(data.get("detection_failed", False))


# ------------------------------------------------------------------
# 評分
# ------------------------------------------------------------------

def evaluate_match(code: str, split: str) -> list[dict] | None:
    """評一場比賽，回傳 4 列（一個角一列）。沒有系統輸出回傳 None。"""
    truth = load_truth(code)
    loaded = load_output(code)
    if loaded is None:
        return None
    output, detection_failed = loaded

    rows = []
    for name in CORNERS:
        zone = truth[name]
        row = {
            "split": split,
            "match": code,
            "corner": name,
            "position": "far" if name in FAR_CORNERS else "near",
            "hit": False,           # 到角區距離 <= 容差
            "inside_zone": False,   # 嚴格落在線寬內
            "det_x": "", "det_y": "",
            "dist_center": "", "dx": "", "dy": "",
            "dist_outside": "",
            "note": "",
        }
        if detection_failed:
            row["note"] = "偵測失敗"
            rows.append(row)
            continue

        point = output[name]
        center = center_of(zone)
        inside = is_inside(point, zone)
        dist_outside = 0.0 if inside else distance_to_zone(point, zone)
        row["det_x"] = round(point[0], 2)
        row["det_y"] = round(point[1], 2)
        row["inside_zone"] = inside
        row["hit"] = dist_outside <= TOLERANCE_PX
        row["dist_center"] = round(distance(point, center), 2)
        row["dx"] = round(point[0] - center[0], 2)
        row["dy"] = round(point[1] - center[1], 2)
        row["dist_outside"] = round(dist_outside, 2)
        rows.append(row)
    return rows


# ------------------------------------------------------------------
# 統計與報表
# ------------------------------------------------------------------

def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.2f}" if whole else "-"


def hit_line(label: str, rows: list[dict]) -> str:
    """命中率表的一列：遠端、近端、全部的命中數與命中率，以及四角全中的場數。"""
    far = [r for r in rows if r["position"] == "far"]
    near = [r for r in rows if r["position"] == "near"]
    far_hits = sum(r["hit"] for r in far)
    near_hits = sum(r["hit"] for r in near)
    all_hits = far_hits + near_hits
    matches = sorted({r["match"] for r in rows})
    perfect = sum(all(r["hit"] for r in rows if r["match"] == m) for m in matches)
    return (
        f"| {label} | {len(matches)} "
        f"| {far_hits}/{len(far)} | {percent(far_hits, len(far))} "
        f"| {near_hits}/{len(near)} | {percent(near_hits, len(near))} "
        f"| {all_hits}/{len(rows)} | {percent(all_hits, len(rows))} "
        f"| {perfect}/{len(matches)} |"
    )


def distance_line(label: str, rows: list[dict]) -> str:
    """到中心距離表的一列：中位數、平均、最大。偵測失敗的角沒有距離，不列入。"""
    values = [r["dist_center"] for r in rows if r["dist_center"] != ""]
    if not values:
        return f"| {label} | 0 | - | - | - |"
    return (
        f"| {label} | {len(values)} | {statistics.median(values):.2f} "
        f"| {statistics.mean(values):.2f} | {max(values):.2f} |"
    )


def build_report(rows: list[dict], missing: list[str]) -> str:
    groups = [
        ("開發集", [r for r in rows if r["split"] == "dev"]),
        ("測試集", [r for r in rows if r["split"] == "test"]),
        ("合計", rows),
    ]
    lines = ["# 場地偵測評分結果", ""]

    # ---- 主指標：像素誤差 ----
    lines += [
        "## 角點定位誤差（px，主指標）",
        "",
        "偵測角點到人工標記角區中心（真實角落的點估計）的距離。每個角都量。",
        "遠端白線在畫面上只有幾像素寬、近端較寬，且透視尺度差異大，故分開統計。",
        "",
        "| 範圍 | 角數 | 中位數 | 平均 | 最大 |",
        "| ---- | ---: | ---: | ---: | ---: |",
    ]
    for label, group in groups:
        if not group:
            continue
        lines.append(distance_line(f"{label}・遠端", [r for r in group if r["position"] == "far"]))
        lines.append(distance_line(f"{label}・近端", [r for r in group if r["position"] == "near"]))
        lines.append(distance_line(f"{label}・全部", group))
    lines.append("")

    # ---- 輔助指標：命中率 ----
    strict = sum(r["inside_zone"] for r in rows)
    lines += [
        "## 角點命中率（輔助）",
        "",
        f"命中＝偵測角點落在角區內，或在角區外緣 {TOLERANCE_PX:g}px 容差內"
        "（角區僅 5–7px 寬，容差吸收標記緊度與次像素邊緣）。",
        f"純落在線寬內的嚴格命中為 {strict}/{len(rows)}（{percent(strict, len(rows))}%）。",
        "",
        "| 範圍 | 場數 | 遠端命中 | 遠端 % | 近端命中 | 近端 % | 全部命中 | 全部 % | 四角全中場數 |",
        "| ---- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines += [hit_line(label, group) for label, group in groups if group]
    lines.append("")

    misses = [r for r in rows if not r["hit"]]
    lines += [
        "## 失敗角點",
        "",
        "未達命中容差的角。dx 正 = 偏右、dy 正 = 偏下（相對角區中心）。",
        "到角區距離 = 離角區邊緣最近處多遠。",
        "",
        "| 資料分割 | 比賽 | 角 | 到角區距離 | 到中心距離 | dx | dy | 備註 |",
        "| -------- | ---- | -- | ---: | ---: | ---: | ---: | ---- |",
    ]
    for r in misses:
        lines.append(
            f"| {r['split']} | {r['match']} | {r['corner']} | {r['dist_outside']} "
            f"| {r['dist_center']} | {r['dx']} | {r['dy']} | {r['note']} |"
        )
    if not misses:
        lines.append("| - | 無 | | | | | | |")
    lines.append("")

    if missing:
        lines += ["## 未評分", "",
                  "以下比賽有真值但 results/detected/ 沒有偵測結果（先跑 detect_court.py），未列入統計：", ""]
        lines += [f"- {code}" for code in missing]
        lines.append("")
    return "\n".join(lines)


def write_results(rows: list[dict], report: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # 每個角一列，所有原始數字都在這裡。
    with (RESULTS_DIR / "corners.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # 每場一列的摘要。
    with (RESULTS_DIR / "per_match.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["split", "match", "hits", "TL", "TR", "BR", "BL", "max_dist_center"])
        for code in sorted({r["match"] for r in rows}):
            match_rows = [r for r in rows if r["match"] == code]
            by_corner = {r["corner"]: r for r in match_rows}
            dists = [r["dist_center"] for r in match_rows if r["dist_center"] != ""]
            writer.writerow([
                match_rows[0]["split"], code,
                sum(r["hit"] for r in match_rows),
                *("hit" if by_corner[c]["hit"] else "miss" for c in CORNERS),
                max(dists) if dists else "",
            ])

    (RESULTS_DIR / "report_tables.md").write_text(report, encoding="utf-8")
    print(f"\n已寫入 {RESULTS_DIR}")


# ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="場地偵測評分")
    parser.add_argument("codes", nargs="*", help="只評這些比賽（預設：ans/ 裡全部）")
    parser.add_argument("--write", action="store_true", help="指定場次時也寫入 results/")
    args = parser.parse_args()

    splits = load_splits()
    all_codes = sorted(p.stem for p in ANS_DIR.glob("*.json"))
    codes = args.codes or all_codes
    for code in codes:
        if code not in all_codes:
            print(f"找不到真值：{ANS_DIR / (code + '.json')}")
            return 1

    rows, missing = [], []
    for code in codes:
        match_rows = evaluate_match(code, splits.get(code, "?"))
        if match_rows is None:
            missing.append(code)
            continue
        rows.extend(match_rows)

    if not rows:
        print("沒有任何可評分的比賽。")
        return 1

    report = build_report(rows, missing)
    print(report)

    if not args.codes or args.write:
        write_results(rows, report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
