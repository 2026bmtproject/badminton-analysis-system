"""Generate the Chapter 3 module flowcharts (.svg + .drawio) from one coordinate spec each.

Same visual vocabulary as make_system_architecture.py: stage boxes take the fill of
their Chapter 3 group in the architecture figure, JSON artifacts are notes, caches are
cylinders, optional inputs and dependencies are dashed.

    uv run python docs/figures/report/make_flowcharts.py docs/figures/report [name ...]
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape

FONT = "Arial Unicode MS"
INK, LINE, FRAME, ICON = "#243247", "#526174", "#9CA9B7", "#5B8F8A"
ART = "#F4F6F8"  # artifacts: JSON files, caches, raw inputs
PRE, BALL, SCENE, AUD, SEM, UI = "#E8EDF3", "#E9E3F4", "#E6EFFA", "#DCEEE6", "#F7E4CE", "#EAF3F1"


def esc(s):
    return escape(s, {'"': "&quot;", "\n": "&#10;"})


class Fig:
    def __init__(self, name, w, h):
        self.name, self.w, self.h = name, w, h
        self.svg, self.cells, self.boxes, self.n = [], [], {}, 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"

    # ---- low-level emitters ----
    def _svg_text(self, x, y, s, size, anchor="middle"):
        self.svg.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" dominant-baseline="central" '
                        f'font-family="{FONT}" font-size="{size}" fill="{INK}">{escape(s)}</text>')

    def _svg_lines(self, cx, cy, lines, size):
        step = size * 1.3
        for i, s in enumerate(lines):
            self._svg_text(cx, cy + (i - (len(lines) - 1) / 2) * step, s, size)

    def _vertex(self, cid, value, style, x, y, w, h):
        self.cells.append(f'<mxCell id="{cid}" value="{esc(value)}" style="{style}" vertex="1" '
                          f'parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" '
                          f'as="geometry" /></mxCell>')

    def _polyline(self, cid, pts, style, src=None, tgt=None):
        ends = (f' source="{src}"' if src else "") + (f' target="{tgt}"' if tgt else "")
        mid = "".join(f'<mxPoint x="{x}" y="{y}" />' for x, y in pts[1:-1])
        self.cells.append(f'<mxCell id="{cid}" edge="1" parent="1" style="{style}"{ends}>'
                          f'<mxGeometry relative="1" as="geometry">'
                          f'<mxPoint x="{pts[0][0]}" y="{pts[0][1]}" as="sourcePoint" />'
                          f'<mxPoint x="{pts[-1][0]}" y="{pts[-1][1]}" as="targetPoint" />'
                          f'<Array as="points">{mid}</Array></mxGeometry></mxCell>')

    @staticmethod
    def _label_style(size, extra=""):
        return (f"html=0;whiteSpace=wrap;align=center;verticalAlign=middle;fontFamily={FONT};"
                f"fontSize={size};fontColor={INK};{extra}")

    def _stroke(self, color, width, dashed):
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        return f'stroke="{color}" stroke-width="{width}"{dash}'

    def line(self, pts, color=LINE, width=2):
        """Arrowless decoration line (icon strokes)."""
        p = " ".join(f"{x},{y}" for x, y in pts)
        self.svg.append(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="{width}"/>')
        self._polyline(self._id("deco"), pts, f"endArrow=none;strokeColor={color};strokeWidth={width};")

    def _rect(self, cid, x, y, w, h, fill, stroke=LINE, dashed=False, rx=7, value="", size=23):
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
                        f'{self._stroke(stroke, 1.5, dashed)}/>')
        style = (f"rounded=1;arcSize={10 if rx else 0};fillColor={fill};strokeColor={stroke};"
                 f"strokeWidth=1.5;dashed={int(dashed)};") + self._label_style(size)
        self._vertex(cid, value, style, x, y, w, h)

    # ---- diagram vocabulary ----
    def box(self, cid, x, y, w, h, lines, fill, size=23):
        """Processing stage."""
        self.boxes[cid] = (x, y, w, h)
        self._rect(cid, x, y, w, h, fill, value="\n".join(lines), size=size)
        self._svg_lines(x + w / 2, y + h / 2, lines, size)

    def group(self, cid, x, y, w, h, fill, label):
        self._rect(cid, x, y, w, h, fill, FRAME, True, 8)
        self.text(f"{cid}-title", x + 12, y + 6, w - 24, 28, label, 20, "left")

    def note(self, cid, x, y, w, h, lines, dashed=False, size=21):
        """JSON artifact: a page with a folded corner."""
        self.boxes[cid] = (x, y, w, h)
        f = 14
        self.svg.append(f'<path d="M{x} {y} H{x + w - f} L{x + w} {y + f} V{y + h} H{x} Z" '
                        f'fill="{ART}" {self._stroke(LINE, 1.5, dashed)}/>')
        self.svg.append(f'<path d="M{x + w - f} {y} V{y + f} H{x + w}" fill="none" '
                        f'{self._stroke(LINE, 1.5, False)}/>')
        self._svg_lines(x + w / 2, y + h / 2, lines, size)
        style = (f"shape=note;size={f};fillColor={ART};strokeColor={LINE};strokeWidth=1.5;"
                 f"dashed={int(dashed)};") + self._label_style(size)
        self._vertex(cid, "\n".join(lines), style, x, y, w, h)

    def cyl(self, cid, x, y, w, h, lines, dashed=False, size=21):
        """Cache on disk."""
        self.boxes[cid] = (x, y, w, h)
        s, rx = 10, w / 2
        self.svg.append(f'<path d="M{x} {y + s} A{rx} {s} 0 0 1 {x + w} {y + s} V{y + h - s} '
                        f'A{rx} {s} 0 0 1 {x} {y + h - s} Z" fill="{ART}" '
                        f'{self._stroke(LINE, 1.5, dashed)}/>')
        self.svg.append(f'<path d="M{x} {y + s} A{rx} {s} 0 0 0 {x + w} {y + s}" fill="none" '
                        f'{self._stroke(LINE, 1.5, dashed)}/>')
        self._svg_lines(x + w / 2, y + (h + s) / 2, lines, size)
        style = (f"shape=cylinder3;size={s};fillColor={ART};strokeColor={LINE};strokeWidth=1.5;"
                 f"dashed={int(dashed)};spacingTop={s};") + self._label_style(size)
        self._vertex(cid, "\n".join(lines), style, x, y, w, h)

    def diamond(self, cid, x, y, w, h, lines, fill, size=21):
        self.boxes[cid] = (x, y, w, h)
        pts = [(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)]
        self.svg.append(f'<polygon points="{" ".join(f"{a},{b}" for a, b in pts)}" fill="{fill}" '
                        f'{self._stroke(LINE, 1.5, False)}/>')
        self._svg_lines(x + w / 2, y + h / 2, lines, size)
        style = (f"rhombus;fillColor={fill};strokeColor={LINE};strokeWidth=1.5;"
                 + self._label_style(size))
        self._vertex(cid, "\n".join(lines), style, x, y, w, h)

    def hexagon(self, cid, x, y, w, h, lines, size=21):
        """Shared decision criterion."""
        self.boxes[cid] = (x, y, w, h)
        s = 22
        pts = [(x + s, y), (x + w - s, y), (x + w, y + h / 2), (x + w - s, y + h), (x + s, y + h),
               (x, y + h / 2)]
        self.svg.append(f'<polygon points="{" ".join(f"{a},{b}" for a, b in pts)}" fill="#FFFFFF" '
                        f'{self._stroke(LINE, 1.5, False)}/>')
        self._svg_lines(x + w / 2, y + h / 2, lines, size)
        style = (f"shape=hexagon;perimeter=hexagonPerimeter2;fixedSize=1;size={s};fillColor=#FFFFFF;"
                 f"strokeColor={LINE};strokeWidth=1.5;" + self._label_style(size))
        self._vertex(cid, "\n".join(lines), style, x, y, w, h)

    def text(self, cid, x, y, w, h, s, size=19, align="center"):
        anchor = {"left": ("start", x), "center": ("middle", x + w / 2), "right": ("end", x + w)}
        a, tx = anchor[align]
        self._svg_text(tx, y + h / 2, s, size, a)
        self._vertex(cid, s, f"text;{self._label_style(size)}align={align};", x, y, w, h)

    def _icon_frame(self, cid, x, y, w, h, label):
        self.boxes[cid] = (x, y, w, h)
        self._rect(cid, x, y, w, h, ART)
        self.text(f"{cid}-label", x, y + h - 40, w, 36, label, 21)
        return x + w / 2

    def video(self, cid, x, y, w, h, label):
        """Broadcast video: three stacked frames showing a court."""
        cx = self._icon_frame(cid, x, y, w, h, label)
        for i, (dx, dy) in enumerate([(-39, 11), (-46, 18), (-53, 25)]):
            self._rect(f"{cid}-frame{i}", cx + dx, y + dy, 90, 62, "#FFFFFF", rx=3)
        fx, fy = cx - 53, y + 25
        self.line([(fx + 18, fy + 12), (fx + 72, fy + 12), (fx + 80, fy + 50), (fx + 10, fy + 50),
                   (fx + 18, fy + 12)], ICON)
        self.line([(fx + 14, fy + 31), (fx + 76, fy + 31)], ICON)
        self.line([(fx + 45, fy + 12), (fx + 45, fy + 50)], ICON)

    def audio(self, cid, x, y, w, h, label):
        """Audio track: a waveform."""
        cx = self._icon_frame(cid, x, y, w, h, label)
        mid = y + 52
        for i, a in enumerate([8, 18, 30, 14, 36, 22, 10, 26, 16, 6]):
            bx = cx - 54 + i * 12
            self.line([(bx, mid - a), (bx, mid + a)], ICON, 4)

    def screen(self, cid, x, y, w, h, label):
        """Review UI: a player with a timeline."""
        cx = self._icon_frame(cid, x, y, w, h, label)
        self._rect(f"{cid}-screen", cx - 40, y + 14, 80, 54, "#FFFFFF", rx=3)
        tri = [(cx - 7, y + 30), (cx + 11, y + 41), (cx - 7, y + 52)]
        self.svg.append(f'<polygon points="{" ".join(f"{a},{b}" for a, b in tri)}" fill="{ICON}"/>')
        self._vertex(f"{cid}-play", "", f"triangle;fillColor={ICON};strokeColor=none;html=0;",
                     cx - 7, y + 30, 18, 22)
        self.line([(cx - 40, y + 80), (cx + 40, y + 80)], FRAME, 3)
        self.line([(cx - 40, y + 80), (cx - 10, y + 80)], ICON, 3)

    def edge(self, src, tgt, pts, dashed=False):
        p = " ".join(f"{x},{y}" for x, y in pts)
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        self.svg.append(f'<polyline points="{p}" fill="none" stroke="{LINE}" stroke-width="1.6" '
                        f'marker-end="url(#arrow)"{dash}/>')

        def anchor(box, pt):
            bx, by, bw, bh = box
            return f"{(pt[0] - bx) / bw:.4f}", f"{(pt[1] - by) / bh:.4f}"

        ex, ey = anchor(self.boxes[src], pts[0])
        nx, ny = anchor(self.boxes[tgt], pts[-1])
        style = ("edgeStyle=none;rounded=0;html=0;endArrow=block;endFill=1;strokeColor=#526174;"
                 f"strokeWidth=1.6;dashed={int(dashed)};exitX={ex};exitY={ey};exitPerimeter=0;"
                 f"entryX={nx};entryY={ny};entryPerimeter=0;")
        self._polyline(self._id(f"e-{src}-{tgt}-"), pts, style, src, tgt)

    def save(self, out):
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}"><defs><marker id="arrow" markerWidth="8" '
                'markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse">'
                f'<path d="M0 0 L8 4 L0 8 Z" fill="{LINE}"/></marker></defs>'
                '<rect width="100%" height="100%" fill="white"/>')
        (out / f"{self.name}.svg").write_text(head + "".join(self.svg) + "</svg>", encoding="utf-8")
        drawio = (f"<?xml version='1.0' encoding='utf-8'?>\n<mxfile host=\"app.diagrams.net\">"
                  f'<diagram name="{self.name}" id="{self.name}"><mxGraphModel page="1" '
                  f'pageWidth="{self.w}" pageHeight="{self.h}" grid="1" gridSize="10"><root>'
                  f'<mxCell id="0" /><mxCell id="1" parent="0" />{"".join(self.cells)}</root>'
                  f"</mxGraphModel></diagram></mxfile>\n")
        (out / f"{self.name}.drawio").write_text(drawio, encoding="utf-8")


# ---------------------------------------------------------------------------
def score_recognition():
    f = Fig("score-recognition", 1380, 420)
    f.video("IN1", 15, 20, 170, 130, "原始賽事影片")
    f.note("IN2", 25, 200, 150, 64, ["segments.json"], size=20)
    f.box("S1", 255, 128, 190, 86, ["1. 多幀合成", "前處理"], PRE)
    f.box("S2", 505, 139, 200, 64, ["2. VLM 轉錄比分"], PRE)
    f.diamond("D", 765, 111, 230, 120, ["相鄰片段", "分差 ≥ 2？"], PRE)
    f.note("OUT", 1195, 139, 170, 64, ["scores.json"], size=20)
    f.box("S3", 765, 300, 230, 86, ["3. 二分遞迴", "定位回合邊界"], PRE)
    f.edge("IN1", "S1", [(185, 85), (220, 85), (220, 155), (255, 155)])
    f.edge("IN2", "S1", [(175, 232), (220, 232), (220, 187), (255, 187)])
    f.edge("S1", "S2", [(445, 171), (505, 171)])
    f.edge("S2", "D", [(705, 171), (765, 171)])
    f.edge("D", "OUT", [(995, 171), (1195, 171)])
    f.text("t-no", 1000, 128, 190, 30, "否：片段屬單一回合")
    f.edge("D", "S3", [(880, 231), (880, 300)])
    f.text("t-yes", 892, 250, 210, 30, "是：片段含有多個回合", align="left")
    f.edge("S3", "S1", [(765, 343), (350, 343), (350, 214)], dashed=True)
    f.text("t-loop", 380, 350, 360, 30, "對每個子區間重跑")
    return f


def court_detection():
    f = Fig("court-detection", 1390, 560)
    f.video("IN1", 15, 20, 170, 130, "原始賽事影片")
    f.note("IN2", 25, 185, 150, 64, ["segments.json"], size=20)
    f.box("S1", 255, 107, 170, 86, ["1. 球員遮擋", "前處理"], SCENE)
    f.box("S2", 565, 107, 170, 86, ["2. 球場區域", "定位"], SCENE)
    f.box("S3", 875, 118, 170, 64, ["3. 場線偵測"], SCENE)
    f.box("S4", 255, 318, 170, 64, ["4. 外角候選"], SCENE)
    f.box("S5", 565, 307, 170, 86, ["5. 角點重估", "與邊界修正"], SCENE)
    f.box("S6", 875, 307, 170, 86, ["6. 結果驗證", "與評分"], SCENE)
    f.note("OUT", 1185, 318, 170, 64, ["court.json"], size=20)
    f.hexagon("CRIT", 460, 470, 480, 64, ["單應性 + 吻合度評分"])
    f.edge("IN1", "S1", [(185, 85), (220, 85), (220, 135), (255, 135)])
    f.edge("IN2", "S1", [(175, 217), (220, 217), (220, 165), (255, 165)])
    f.edge("S1", "S2", [(425, 150), (565, 150)])
    f.text("t1", 430, 112, 130, 30, "乾淨球場影像", 18)
    f.edge("S2", "S3", [(735, 150), (875, 150)])
    f.text("t2", 740, 112, 130, 30, "ROI", 18)
    f.edge("S3", "S4", [(960, 182), (960, 255), (340, 255), (340, 318)])
    f.text("t3", 580, 222, 140, 30, "候選線段", 18)
    f.edge("S4", "S5", [(425, 350), (565, 350)])
    f.text("t4", 430, 312, 130, 30, "候選四角", 18)
    f.edge("S5", "S6", [(735, 350), (875, 350)])
    f.text("t5", 740, 312, 130, 30, "精修四角與 H", 18)
    f.edge("S6", "OUT", [(1045, 350), (1185, 350)])
    f.edge("CRIT", "S4", [(500, 470), (500, 430), (340, 430), (340, 382)], dashed=True)
    f.edge("CRIT", "S5", [(650, 470), (650, 393)], dashed=True)
    f.edge("CRIT", "S6", [(900, 470), (900, 430), (960, 430), (960, 393)], dashed=True)
    f.text("t-judge", 660, 418, 80, 30, "裁決", 18, "left")
    return f


def pose_tracking():
    f = Fig("pose-tracking", 1300, 470)
    f.video("IN1", 15, 20, 170, 130, "原始賽事影片")
    f.note("IN2", 25, 200, 150, 64, ["segments.json"], size=20)
    f.note("IN3", 25, 348, 150, 64, ["court.json"], size=20)
    f.group("P1", 235, 60, 690, 170, "#F0F5FC", "感知（GPU）")
    f.box("S1", 255, 118, 190, 64, ["1. 人體位置偵測"], SCENE)
    f.box("S2", 495, 118, 190, 64, ["2. 球場範圍預篩"], SCENE)
    f.box("S3", 735, 118, 170, 64, ["3. 姿態估計"], SCENE)
    f.cyl("CACHE", 975, 105, 180, 90, ["全幀所有人", "骨架快取"])
    f.group("P2", 620, 275, 450, 175, "#F0F5FC", "選人（CPU）")
    f.box("S4", 640, 348, 190, 64, ["4. 腳點反投影"], SCENE)
    f.box("S5", 880, 320, 170, 120, ["5. 公尺身高", "靜態錨點", "記憶追蹤"], SCENE, 22)
    f.note("OUT", 1120, 348, 160, 64, ["pose.json"], size=20)
    f.edge("IN1", "S1", [(185, 85), (215, 85), (215, 138), (255, 138)])
    f.edge("IN2", "S1", [(175, 232), (225, 232), (225, 162), (255, 162)])
    f.edge("S1", "S2", [(445, 150), (495, 150)])
    f.edge("S2", "S3", [(685, 150), (735, 150)])
    f.edge("S3", "CACHE", [(905, 150), (975, 150)])
    f.edge("IN3", "S2", [(175, 380), (560, 380), (560, 182)])
    f.edge("IN3", "S4", [(175, 380), (640, 380)])
    f.edge("CACHE", "S4", [(1065, 195), (1065, 250), (790, 250), (790, 348)])
    f.edge("S4", "S5", [(830, 380), (880, 380)])
    f.edge("S5", "OUT", [(1050, 380), (1120, 380)])
    return f


def shuttle_tracking():
    f = Fig("shuttle-tracking", 1300, 300)
    f.video("IN1", 15, 20, 170, 130, "原始賽事影片")
    f.note("IN2", 25, 200, 150, 64, ["segments.json"], size=20)
    f.box("S1", 255, 113, 220, 86, ["1. 熱圖偵測", "（TrackNetV3）"], BALL)
    f.cyl("C1", 535, 111, 150, 90, ["熱圖快取"])
    f.box("A1", 755, 33, 280, 86, ["2a. 軌跡修補", "InpaintNet 補洞"], BALL)
    f.box("B1", 755, 193, 280, 86, ["2b. 多候選修補", "低門檻候選 + Viterbi"], BALL)
    f.note("OUT", 1110, 124, 170, 64, ["shuttle.json"], size=20)
    f.edge("IN1", "S1", [(185, 85), (220, 85), (220, 141), (255, 141)])
    f.edge("IN2", "S1", [(175, 232), (220, 232), (220, 171), (255, 171)])
    f.edge("S1", "C1", [(475, 156), (535, 156)])
    f.edge("C1", "A1", [(685, 156), (720, 156), (720, 76), (755, 76)])
    f.edge("C1", "B1", [(685, 156), (720, 156), (720, 236), (755, 236)])
    f.edge("A1", "OUT", [(1035, 76), (1072, 76), (1072, 146), (1110, 146)])
    f.edge("B1", "OUT", [(1035, 236), (1072, 236), (1072, 166), (1110, 166)])
    return f


def event_detection():
    f = Fig("event-detection", 1290, 340)
    f.note("SH", 15, 110, 170, 64, ["shuttle.json"], size=20)
    f.note("POSE", 15, 198, 170, 64, ["pose.json"], size=20)
    f.box("BST", 260, 40, 210, 86, ["BST 逐幀", "密集掃描"], BALL)
    f.cyl("CACHE", 540, 38, 220, 90, ["逐幀球種", "機率快取"])
    f.note("SCORE", 845, 40, 160, 80, ["scores.json", "（選用）"], dashed=True, size=20)
    f.box("CAND", 260, 227, 210, 86, ["候選生成", "與篩選"], BALL)
    f.box("FILL", 560, 238, 180, 64, ["結構性補漏"], BALL)
    f.box("PRUNE", 830, 238, 180, 64, ["誤判修剪"], BALL)
    f.note("OUT", 1100, 238, 170, 64, ["events.json"], size=20)
    for src, y in (("SH", 142), ("POSE", 230)):
        f.edge(src, "BST", [(185, y), (220, y), (220, 83), (260, 83)])
        f.edge(src, "CAND", [(185, y), (220, y), (220, 270), (260, 270)])
    f.edge("BST", "CACHE", [(470, 83), (540, 83)])
    f.edge("CACHE", "CAND", [(580, 128), (580, 175), (420, 175), (420, 227)])
    f.edge("CACHE", "FILL", [(650, 128), (650, 238)])
    f.edge("CACHE", "PRUNE", [(720, 128), (720, 175), (880, 175), (880, 238)])
    f.edge("SCORE", "PRUNE", [(950, 120), (950, 238)], dashed=True)
    f.edge("CAND", "FILL", [(470, 270), (560, 270)])
    f.edge("FILL", "PRUNE", [(740, 270), (830, 270)])
    f.edge("PRUNE", "OUT", [(1010, 270), (1100, 270)])
    return f


def stroke_classification():
    f = Fig("stroke-classification", 720, 350)
    names = ["events.json", "segments.json", "pose.json", "shuttle.json", "court.json"]
    for i, name in enumerate(names):
        f.note(f"IN{i}", 15, 15 + i * 64, 180, 50, [name], size=20)
    f.box("BST", 300, 143, 160, 64, ["BST"], BALL)
    f.note("OUT", 535, 143, 170, 64, ["strokes.json"], size=20)
    for i in range(len(names)):
        y = 40 + i * 64
        f.edge(f"IN{i}", "BST", [(195, y), (245, y), (245, 175), (300, 175)])
    f.edge("BST", "OUT", [(460, 175), (535, 175)])
    return f


def audio_cheer():
    f = Fig("audio-cheer", 1460, 280)
    f.audio("VID", 15, 20, 170, 130, "賽事影片音軌")
    f.note("SEG", 25, 190, 150, 64, ["segments.json"], size=20)
    f.box("S1", 255, 120, 170, 64, ["1. 音訊切窗"], AUD)
    f.box("S2", 485, 120, 170, 64, ["2. 歡呼分類"], AUD)
    f.box("S3", 715, 120, 170, 64, ["3. 歡呼強度"], AUD)
    f.box("S4", 945, 120, 210, 64, ["4. 片段層級聚合"], AUD)
    f.note("OUT", 1215, 120, 230, 64, ["audio_signals.json"], size=20)
    f.edge("VID", "S1", [(185, 85), (215, 85), (215, 140), (255, 140)])
    f.edge("SEG", "S1", [(175, 222), (215, 222), (215, 164), (255, 164)])
    f.edge("S1", "S2", [(425, 152), (485, 152)])
    f.edge("S2", "S3", [(655, 152), (715, 152)])
    f.edge("S3", "S4", [(885, 152), (945, 152)])
    f.edge("S2", "S4", [(570, 120), (570, 75), (1050, 75), (1050, 120)])
    f.edge("S4", "OUT", [(1155, 152), (1215, 152)])
    return f


def highlight_ranking():
    f = Fig("highlight-ranking", 960, 94)
    f.note("IN", 15, 15, 230, 64, ["audio_signals.json"], size=20)
    f.box("S1", 315, 15, 300, 64, ["歡呼分數與強度合成"], AUD)
    f.note("OUT", 685, 15, 260, 64, ["highlights.json"], size=20)
    f.edge("IN", "S1", [(245, 47), (315, 47)])
    f.edge("S1", "OUT", [(615, 47), (685, 47)])
    return f


def identity_mapping():
    f = Fig("identity-mapping", 1540, 480)
    f.note("SC", 15, 88, 190, 64, ["scores.json"], size=20)
    f.note("ST", 15, 238, 190, 64, ["strokes.json"], size=20)
    f.cyl("DENSE", 15, 340, 190, 100, ["逐幀球種", "機率快取（選用）"], dashed=True, size=19)
    f.box("EP", 280, 27, 260, 86, ["依規則切出", "換邊時段"], SEM)
    f.box("ROW", 280, 134, 260, 86, ["比分變化", "→ 發球選手"], SEM)
    f.box("SIDE", 280, 242, 260, 86, ["首拍為發球", "→ 發球半場"], SEM)
    f.box("VOTE", 610, 145, 180, 64, ["逐時段投票"], SEM)
    f.box("FILL", 850, 134, 220, 86, ["依計分板", "慣例補齊"], SEM)
    f.box("HSV", 1130, 280, 180, 64, ["球衣色彩比對"], SEM)
    f.note("POSE", 1135, 395, 170, 70, ["pose.json", "（選用）"], dashed=True, size=20)
    f.note("OUT", 1350, 145, 170, 64, ["identity.json"], size=20)
    f.edge("SC", "EP", [(205, 120), (240, 120), (240, 70), (280, 70)])
    f.edge("SC", "ROW", [(205, 120), (240, 120), (240, 177), (280, 177)])
    f.edge("ST", "SIDE", [(205, 270), (280, 270)])
    f.edge("DENSE", "SIDE", [(205, 395), (240, 395), (240, 300), (280, 300)], dashed=True)
    f.edge("EP", "VOTE", [(540, 70), (575, 70), (575, 155), (610, 155)])
    f.edge("ROW", "VOTE", [(540, 177), (610, 177)])
    f.edge("SIDE", "VOTE", [(540, 285), (575, 285), (575, 199), (610, 199)])
    f.edge("VOTE", "FILL", [(790, 177), (850, 177)])
    f.edge("FILL", "OUT", [(1070, 177), (1350, 177)])
    f.edge("FILL", "HSV", [(960, 220), (960, 312), (1130, 312)])
    f.text("t-undecided", 975, 278, 150, 30, "仍未判定", 18)
    f.edge("POSE", "HSV", [(1220, 395), (1220, 344)], dashed=True)
    f.edge("HSV", "OUT", [(1310, 312), (1435, 312), (1435, 209)])
    return f


def ui_workflow():
    f = Fig("ui-workflow", 1090, 320)
    f.box("LIB", 15, 140, 150, 64, ["比賽庫"], UI)
    f.box("SET", 300, 40, 170, 64, ["分析設定"], UI)
    f.box("TASK", 540, 40, 170, 64, ["任務進度"], UI)
    f.box("IMP", 420, 240, 190, 64, ["匯入既有結果"], UI)
    f.screen("REV", 900, 110, 170, 130, "回看工作區")
    f.edge("LIB", "SET", [(165, 160), (220, 160), (220, 72), (300, 72)])
    f.edge("SET", "TASK", [(470, 72), (540, 72)])
    f.edge("TASK", "REV", [(710, 72), (985, 72), (985, 110)])
    f.text("t-done", 720, 40, 255, 30, "完成後自動更新", 18)
    f.edge("LIB", "REV", [(165, 172), (900, 172)])
    f.text("t-ready", 300, 140, 200, 30, "已可回看", 18)
    f.edge("LIB", "IMP", [(165, 184), (220, 184), (220, 272), (420, 272)])
    f.text("t-done-before", 228, 240, 185, 30, "已有分析結果", 18)
    f.edge("IMP", "REV", [(610, 272), (985, 272), (985, 240)])
    return f


def ui_architecture():
    f = Fig("ui-architecture", 1220, 380)
    f.video("VID", 15, 20, 190, 130, "比賽影片")
    f.note("ST", 15, 190, 190, 64, ["各階段 JSON"], size=20)
    f.box("IMP", 290, 110, 190, 86, ["匯入程式", "（驗證、整合）"], UI)
    f.note("REV", 560, 60, 190, 64, ["回看資料"], size=20)
    f.note("OVL", 560, 180, 190, 64, ["逐回合疊圖資料"], size=20)
    f.box("UIX", 1000, 110, 170, 86, ["使用者介面"], UI)
    f.box("SVC", 640, 298, 190, 64, ["本機分析服務"], UI)
    f.box("PIPE", 290, 298, 190, 64, ["分析管線"], UI)
    f.edge("VID", "IMP", [(205, 85), (245, 85), (245, 140), (290, 140)])
    f.edge("ST", "IMP", [(205, 222), (245, 222), (245, 166), (290, 166)])
    f.edge("IMP", "REV", [(480, 135), (520, 135), (520, 92), (560, 92)])
    f.edge("IMP", "OVL", [(480, 171), (520, 171), (520, 212), (560, 212)])
    f.edge("REV", "UIX", [(750, 92), (960, 92), (960, 136), (1000, 136)])
    f.edge("OVL", "UIX", [(750, 212), (960, 212), (960, 170), (1000, 170)], dashed=True)
    f.text("t-overlay", 765, 180, 180, 30, "打開疊圖時讀取", 18)
    f.edge("UIX", "SVC", [(1085, 196), (1085, 330), (830, 330)])
    f.text("t-submit", 1097, 248, 110, 30, "送出任務", 18, "left")
    f.edge("SVC", "PIPE", [(640, 330), (480, 330)])
    f.edge("PIPE", "ST", [(290, 330), (110, 330), (110, 254)])
    return f


FIGURES = [score_recognition, court_detection, pose_tracking, shuttle_tracking, event_detection,
           stroke_classification, audio_cheer, highlight_ranking, identity_mapping, ui_workflow,
           ui_architecture]

if __name__ == "__main__":
    out, only = Path(sys.argv[1]), set(sys.argv[2:])
    for make in FIGURES:
        fig = make()
        if not only or fig.name in only:
            fig.save(out)
            print("wrote", out / f"{fig.name}.svg", out / f"{fig.name}.drawio")
