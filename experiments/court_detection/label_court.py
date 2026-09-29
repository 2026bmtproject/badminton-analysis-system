"""手動標記場地偵測的真值：球場四個外角的「角區」。

概念
----
球場外緣是兩條有寬度的白線（邊線、底線）。兩線在外角交叉，交疊處是一個
小四邊形，稱為「角區」。本實驗的判定是：系統偵測的角點落在角區內就算成功。

角區不直接點，而是由「線的邊緣」擬合出來，因為在清楚的長直線上取點，比
在模糊的角上猜頂點準得多。球場外框有 4 條線，每條線有外緣、內緣兩條邊，
共 8 條邊緣：

    遠端底線：外緣、內緣        近端底線：外緣、內緣
    左邊線：  外緣、內緣        右邊線：  外緣、內緣

每條邊緣沿線點 2 點以上，程式用最小平方擬合成一條直線。四個角區各由其中
4 條邊緣相交而成：

    TL = 遠端底線 ∩ 左邊線      TR = 遠端底線 ∩ 右邊線
    BL = 近端底線 ∩ 左邊線      BR = 近端底線 ∩ 右邊線

角區就是兩條白線實際塗漆重疊的那一小塊四邊形，寬度自動等於真正的線寬。
四個角共用同一組線擬合，彼此幾何一致。

四個角的名稱與 court.json 相同（順時針）：
    TL 遠端左（畫面左上）   TR 遠端右（畫面右上）
    BR 近端右（畫面右下）   BL 近端左（畫面左下）

底圖
----
取 experiments/match_segmentation/ans/<代號>.csv 裡最長的 3 段真值片段，
每段均勻抽 20 格，取中位數合成，把移動中的球員濾掉。底圖只依賴人工真值，
不依賴系統的分段輸出。合成結果快取在 matches/<代號>/ans/court_base.png。
標記時「不顯示」系統的偵測結果，避免標記者受影響。

操作
----
  依畫面提示，逐條標完 8 條邊緣。每條邊緣：
    總覽畫面   在邊緣某處點一下 -> 進入放大畫面
    放大畫面   點一個精確的點，放完自動回總覽。u 復原   Esc 取消這一點
    總覽畫面   繼續點下一點（同一條邊緣至少 2 點，越多擬合越穩）
               Enter/空白：這條邊緣完成，換下一條
  8 條都標完後進入檢查畫面，顯示算出的 4 個角區。
    c 切換底圖 / 比賽前段影格 / 比賽後段影格（確認整場機位沒有偏移）
    1-8 重標某條邊緣（畫面上有對照）   s 存檔並結束   q 不存檔結束

輸出
----
    experiments/court_detection/ans/<代號>.json
    corners 為算出的角區（評分程式讀這個），lines 為原始邊緣點。

用法
----
    uv run python experiments/court_detection/label_court.py test1
    uv run python experiments/court_detection/label_court.py test1 --scale 0.5
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.common.frame_composite import composite_median, extract_frames_in_range  # noqa: E402
from modules.contracts import resolve_input_video  # noqa: E402

HERE = Path(__file__).resolve().parent
ANS_DIR = HERE / "ans"
SEGMENT_ANS_DIR = ROOT / "experiments" / "match_segmentation" / "ans"

CORNERS = ["TL", "TR", "BR", "BL"]
CORNER_NAMES = {"TL": "far-left", "TR": "far-right", "BR": "near-right", "BL": "near-left"}

# 8 條邊緣的標記順序。每條寫成 (線名, 外緣/內緣)。
EDGE_SEQUENCE = [
    ("far_baseline", "outer"), ("far_baseline", "inner"),
    ("near_baseline", "outer"), ("near_baseline", "inner"),
    ("left_sideline", "outer"), ("left_sideline", "inner"),
    ("right_sideline", "outer"), ("right_sideline", "inner"),
]
LINE_LABEL = {
    "far_baseline": "FAR baseline", "near_baseline": "NEAR baseline",
    "left_sideline": "LEFT sideline", "right_sideline": "RIGHT sideline",
}
# 每個角用哪一條底線、哪一條邊線。
CORNER_LINES = {
    "TL": ("far_baseline", "left_sideline"),
    "TR": ("far_baseline", "right_sideline"),
    "BR": ("near_baseline", "right_sideline"),
    "BL": ("near_baseline", "left_sideline"),
}

NUM_SEGMENTS = 3          # 底圖取幾段最長的真值片段
FRAMES_PER_SEGMENT = 20   # 每段抽幾格
ZOOM = 8                  # 放大倍率
ZOOM_HALF = 40            # 放大畫面涵蓋原圖 (2*ZOOM_HALF) x (2*ZOOM_HALF) 像素

WINDOW = "label_court"


# --------------------------------------------------------------------------- #
# 底圖
# --------------------------------------------------------------------------- #
def read_truth_segments(code: str) -> list[tuple[int, int]]:
    path = SEGMENT_ANS_DIR / f"{code}.csv"
    if not path.is_file():
        raise FileNotFoundError(f"找不到分段真值：{path}")
    with path.open(encoding="utf-8") as f:
        return [(int(r["start_frame"]), int(r["end_frame"])) for r in csv.DictReader(f)]


def longest_segments(segments: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted(segments, key=lambda s: s[1] - s[0], reverse=True)
    return sorted(ordered[:NUM_SEGMENTS])


def build_base_image(video: Path, picked: list[tuple[int, int]], cache: Path) -> np.ndarray:
    if cache.is_file():
        print(f"使用快取底圖：{cache}")
        return cv2.imread(str(cache))
    print("合成底圖中（只有第一次需要）...")
    frames = []
    for start, end in picked:
        frames.extend(extract_frames_in_range(str(video), start, end, FRAMES_PER_SEGMENT))
    base = composite_median(frames)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(cache), base)
    return base


def read_frame(video: Path, index: int) -> np.ndarray:
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, index)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"讀不到影格 {index}")
    return frame


# --------------------------------------------------------------------------- #
# 幾何：擬合邊緣直線，交出角區
# --------------------------------------------------------------------------- #
def fit_line(points: list[list[float]]) -> tuple[list[float], list[float]]:
    """把一串點擬合成一條直線，回傳線上兩點 (P, Q)。

    用主成分分析：直線方向 = 點雲的主方向（第一個奇異向量），
    通過點雲重心。只點 2 點時，這條線就剛好通過那 2 點。
    近垂直的邊線也適用（不像 y = m x + b 會在垂直時失效）。
    """
    pts = np.asarray(points, dtype=float)
    center = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - center)
    direction = vt[0]
    return center.tolist(), (center + direction).tolist()


def intersect(line_a: tuple[list[float], list[float]],
              line_b: tuple[list[float], list[float]]) -> list[float]:
    """兩條直線的交點（各以線上兩點表示，用齊次座標外積求解）。"""
    (a1, a2), (b1, b2) = line_a, line_b
    la = np.cross([a1[0], a1[1], 1.0], [a2[0], a2[1], 1.0])
    lb = np.cross([b1[0], b1[1], 1.0], [b2[0], b2[1], 1.0])
    x, y, w = np.cross(la, lb)
    if abs(w) < 1e-9:
        raise ValueError("兩條邊緣近乎平行，交不出角")
    return [float(x / w), float(y / w)]


def order_around_center(points: list[list[float]]) -> list[list[float]]:
    """把 4 點依繞中心的角度排成一圈，保證是不自交的四邊形。"""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


def zones_from_lines(lines: dict[str, dict[str, list[list[float]]]]) -> dict[str, list[list[float]]]:
    """8 條邊緣點 -> 4 個角區。每個角區是底線兩邊緣 x 邊線兩邊緣的 4 個交點。"""
    fitted = {name: {edge: fit_line(pts) for edge, pts in edges.items()}
              for name, edges in lines.items()}
    zones = {}
    for corner, (baseline, sideline) in CORNER_LINES.items():
        pts = [
            intersect(fitted[baseline][be], fitted[sideline][se])
            for be in ("outer", "inner")
            for se in ("outer", "inner")
        ]
        zones[corner] = order_around_center(pts)
    return zones


# --------------------------------------------------------------------------- #
# 互動
# --------------------------------------------------------------------------- #
class Labeler:
    def __init__(self, base: np.ndarray, check_frames: list[tuple[str, np.ndarray]],
                 scale: float, existing: dict | None):
        self.base = base
        self.backgrounds = [("median composite", base)] + check_frames
        self.bg_index = 0
        self.scale = scale
        self.h, self.w = base.shape[:2]
        # lines[線名][外緣/內緣] = [[x, y], ...]
        self.lines: dict[str, dict[str, list[list[float]]]] = {
            name: {} for name in LINE_LABEL
        }
        if existing:
            self.lines = {n: dict(e) for n, e in existing.items()}
        self.edge_index = self._first_unlabeled()   # 目前在標第幾條邊緣（0..7）
        self.mode = "review" if self.edge_index is None else "overview"
        self.center = (0, 0)         # 放大畫面中心（原圖座標）
        self.cursor: list[float] | None = None
        self.result: str | None = None

    def _first_unlabeled(self) -> int | None:
        for i, (line, edge) in enumerate(EDGE_SEQUENCE):
            if edge not in self.lines[line]:
                return i
        return None

    # ---- 目前這條邊緣 ----
    @property
    def edge(self) -> tuple[str, str]:
        return EDGE_SEQUENCE[self.edge_index]

    def edge_points(self) -> list[list[float]]:
        line, edge = self.edge
        return self.lines[line].setdefault(edge, [])

    def edge_text(self, index: int) -> str:
        line, edge = EDGE_SEQUENCE[index]
        return f"{LINE_LABEL[line]} - {edge.upper()} edge"

    # ---- 放大畫面的座標換算 ----
    def zoom_origin(self) -> tuple[int, int]:
        cx, cy = self.center
        x0 = min(max(cx - ZOOM_HALF, 0), self.w - 2 * ZOOM_HALF)
        y0 = min(max(cy - ZOOM_HALF, 0), self.h - 2 * ZOOM_HALF)
        return x0, y0

    def zoom_to_image(self, mx: int, my: int) -> list[float]:
        # 放大後原圖像素 i 佔 [i*ZOOM, (i+1)*ZOOM)，它的中心才是座標 i。
        x0, y0 = self.zoom_origin()
        return [x0 + (mx + 0.5) / ZOOM - 0.5, y0 + (my + 0.5) / ZOOM - 0.5]

    def image_to_zoom(self, x: float, y: float) -> tuple[int, int]:
        x0, y0 = self.zoom_origin()
        return round((x - x0 + 0.5) * ZOOM - 0.5), round((y - y0 + 0.5) * ZOOM - 0.5)

    def zones(self) -> dict[str, list[list[float]]]:
        """目前 8 條邊緣都有 >=2 點時算得出角區，否則回空。"""
        if any(len(self.lines[l].get(e, [])) < 2 for l, e in EDGE_SEQUENCE):
            return {}
        try:
            return zones_from_lines(self.lines)
        except ValueError:
            return {}

    # ---- 畫面 ----
    def _draw_all_edges(self, img: np.ndarray) -> None:
        """在總覽圖上畫已標的邊緣點與擬合線。"""
        for line, edges in self.lines.items():
            for edge, pts in edges.items():
                for p in pts:
                    cv2.circle(img, (round(p[0] * self.scale), round(p[1] * self.scale)),
                               2, (0, 255, 0), -1)
                if len(pts) >= 2:
                    (a, b) = fit_line(pts)
                    d = np.subtract(b, a)
                    p1 = (np.array(a) - d * 2000) * self.scale
                    p2 = (np.array(a) + d * 2000) * self.scale
                    cv2.line(img, tuple(p1.round().astype(int)), tuple(p2.round().astype(int)),
                             (0, 180, 0), 1)

    def render(self) -> np.ndarray:
        if self.mode == "zoom":
            x0, y0 = self.zoom_origin()
            crop = self.base[y0:y0 + 2 * ZOOM_HALF, x0:x0 + 2 * ZOOM_HALF]
            img = cv2.resize(crop, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST)
            for p in self.edge_points():
                zx, zy = self.image_to_zoom(*p)
                cv2.circle(img, (zx, zy), 3, (0, 255, 0), -1)
            if self.cursor is not None:
                cx, cy = self.image_to_zoom(*self.cursor)
                cv2.drawMarker(img, (cx, cy), (0, 255, 255), cv2.MARKER_CROSS, 20, 1)
            cv2.putText(img, "click the point   u=undo   Esc=cancel", (8, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
            return img

        label, bg = self.backgrounds[self.bg_index] if self.mode == "review" else self.backgrounds[0]
        img = cv2.resize(bg, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        self._draw_all_edges(img)
        for name, zone in self.zones().items():
            pts = np.array([[p[0] * self.scale, p[1] * self.scale] for p in zone], np.float32)
            cv2.polylines(img, [pts.round().astype(np.int32)], True, (0, 0, 255), 1)
            x, y = pts.mean(axis=0)
            cv2.putText(img, name, (int(x) + 8, int(y) - 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 0, 255), 2)

        if self.mode == "overview":
            n = len(self.edge_points())
            enough = " (Enter=next)" if n >= 2 else ""
            cv2.putText(img, f"[{self.edge_index + 1}/8] trace {self.edge_text(self.edge_index)}",
                        (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.putText(img, f"click along the edge ({n} pts){enough}   u=undo",
                        (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        else:
            cv2.putText(img, f"[{label}]  c=bg  1-8=redo edge  s=save  q=quit",
                        (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        return img

    # ---- 輸入 ----
    def on_mouse(self, event, mx, my, _flags, _param):
        if self.mode == "zoom" and event == cv2.EVENT_MOUSEMOVE:
            self.cursor = self.zoom_to_image(mx, my)
            return
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        if self.mode == "overview":
            self.center = (round(mx / self.scale), round(my / self.scale))
            self.cursor = None
            self.mode = "zoom"
        elif self.mode == "zoom":
            self.edge_points().append(self.zoom_to_image(mx, my))
            self.mode = "overview"

    def _finish_edge(self):
        self.edge_index += 1
        if self.edge_index >= len(EDGE_SEQUENCE):
            self.edge_index = len(EDGE_SEQUENCE) - 1  # 停在最後，實際切 review
            self.mode = "review"
        else:
            self.mode = "overview"

    def on_key(self, key: int):
        if self.mode == "zoom":
            if key == 27:                                    # Esc：取消這一點
                self.mode = "overview"
            elif key == ord("u") and self.edge_points():     # 復原上一點
                self.edge_points().pop()
        elif self.mode == "overview":
            if key == ord("u") and self.edge_points():
                self.edge_points().pop()
            elif key in (13, 32) and len(self.edge_points()) >= 2:  # Enter/空白：換下一條
                self._finish_edge()
            elif key == ord("q"):
                self.result = "quit"
        elif self.mode == "review":
            if key == ord("c"):
                self.bg_index = (self.bg_index + 1) % len(self.backgrounds)
            elif ord("1") <= key <= ord("8"):
                self.edge_index = key - ord("1")
                line, edge = self.edge
                self.lines[line][edge] = []
                self.mode = "overview"
            elif key == ord("s"):
                self.result = "save"
            elif key == ord("q"):
                self.result = "quit"

    def run(self) -> str:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW, self.on_mouse)
        while self.result is None:
            cv2.imshow(WINDOW, self.render())
            key = cv2.waitKey(30) & 0xFF
            if key != 255:
                self.on_key(key)
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                self.result = "quit"
        cv2.destroyAllWindows()
        return self.result


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="標記球場四個外角的角區")
    parser.add_argument("code", help="比賽代號，例如 test1")
    parser.add_argument("--scale", type=float, default=0.6, help="總覽畫面縮放，預設 0.6")
    args = parser.parse_args()

    match_path = ROOT / "matches" / args.code
    video = resolve_input_video(match_path)
    segments = read_truth_segments(args.code)
    picked = longest_segments(segments)
    base = build_base_image(video, picked, match_path / "ans" / "court_base.png")

    # 比賽前段、後段各取一格原始影格，用來確認整場機位沒有偏移。
    first, last = segments[0], segments[-1]
    check_frames = [
        (f"early frame {(first[0] + first[1]) // 2}", read_frame(video, (first[0] + first[1]) // 2)),
        (f"late frame {(last[0] + last[1]) // 2}", read_frame(video, (last[0] + last[1]) // 2)),
    ]

    out_path = ANS_DIR / f"{args.code}.json"
    existing = None
    if out_path.is_file():
        existing = json.loads(out_path.read_text(encoding="utf-8")).get("lines")
        print(f"載入既有標記：{out_path}（進入檢查畫面，按 1-8 重標某條邊緣）")

    labeler = Labeler(base, check_frames, args.scale, existing)
    if labeler.run() != "save":
        print("未存檔。")
        return 1

    zones = labeler.zones()
    if not zones:
        print("8 條邊緣尚未標齊或無法交出角區，未存檔。")
        return 1

    record = {
        "match": args.code,
        "image_size": [base.shape[1], base.shape[0]],
        "base_image": {
            "method": "median",
            "segments": [list(s) for s in picked],
            "frames_per_segment": FRAMES_PER_SEGMENT,
        },
        "corner_order": "TL 遠端左, TR 遠端右, BR 近端右, BL 近端左（順時針，與 court.json 相同）",
        "corners": {name: [[round(x, 3), round(y, 3)] for x, y in zones[name]]
                    for name in CORNERS},
        "lines": {name: {edge: [[round(x, 3), round(y, 3)] for x, y in pts]
                         for edge, pts in edges.items()}
                  for name, edges in labeler.lines.items()},
    }
    ANS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已存檔：{out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
