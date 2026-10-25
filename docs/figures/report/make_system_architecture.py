"""Generate system-architecture (.svg + .drawio) from one coordinate spec.

Groups follow the Chapter 3 sections; arrows are the transitive reduction of the
stage dependencies in modules/contracts.py; dashed arrows are optional deps that
are not already implied by a solid path.
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(sys.argv[1])
NAME = "system-architecture"
W, H = 1530, 790
FONT = "Arial Unicode MS"
INK, LINE, FRAME = "#243247", "#526174", "#9CA9B7"

# id: (x, y, w, h, fill, lines)
GROUPS = {
    "G_PRE": (210, 70, 190, 430, "#F5F7F9", "影片前處理"),
    "G_BALL": (435, 196, 670, 140, "#F4F1FA", "球體分析"),
    "G_SCENE": (435, 372, 450, 136, "#F0F5FC", "場景與人體理解"),
    "G_AUD": (435, 544, 670, 136, "#EFF7F3", "音訊與精彩度分析"),
    "G_SEM": (1135, 196, 190, 484, "#FCF3E9", "賽事理解"),
}
NODES = {
    "SCORE": (225, 112, 160, 64, "#E8EDF3", ["比分辨識"]),
    "SEG": (225, 397, 160, 86, "#E8EDF3", ["賽事影片", "分段"]),
    "SHUT": (450, 240, 190, 80, "#E9E3F4", ["球體追蹤與", "軌跡重建"]),
    "EV": (680, 248, 190, 64, "#E9E3F4", ["擊球時刻偵測"]),
    "STK": (910, 248, 180, 64, "#E9E3F4", ["球種分類"]),
    "COURT": (450, 408, 190, 64, "#E6EFFA", ["場地偵測"]),
    "POSE": (680, 408, 190, 64, "#E6EFFA", ["球員骨架追蹤"]),
    "AUDIO": (450, 580, 190, 64, "#DCEEE6", ["歡呼音訊量測"]),
    "HL": (910, 580, 180, 64, "#DCEEE6", ["精彩片段排序"]),
    "ID": (1150, 240, 160, 80, "#F7E4CE", ["球員身分", "對應"]),
    "COM": (1150, 580, 160, 64, "#F7E4CE", ["賽評生成"]),
    "UI": (1370, 397, 145, 86, "#E8EDF3", ["回看介面"]),
}
# (source, target, points from source anchor to target anchor, dashed)
EDGES = [
    ("IN", "SEG", [(175, 440), (225, 440)], False),
    ("SEG", "SCORE", [(305, 397), (305, 176)], False),
    ("SEG", "SHUT", [(385, 412), (418, 412), (418, 280), (450, 280)], False),
    ("SEG", "COURT", [(385, 440), (450, 440)], False),
    ("SEG", "AUDIO", [(305, 483), (305, 612), (450, 612)], False),
    ("SHUT", "EV", [(640, 280), (680, 280)], False),
    ("EV", "STK", [(870, 280), (910, 280)], False),
    ("COURT", "POSE", [(640, 440), (680, 440)], False),
    ("POSE", "EV", [(775, 408), (775, 312)], False),
    ("STK", "ID", [(1090, 280), (1150, 280)], False),
    ("SCORE", "ID", [(385, 132), (1275, 132), (1275, 240)], False),
    ("SCORE", "EV", [(385, 158), (775, 158), (775, 248)], True),
    ("AUDIO", "HL", [(640, 612), (910, 612)], False),
    ("HL", "COM", [(1090, 612), (1150, 612)], True),
    ("ID", "COM", [(1230, 320), (1230, 580)], False),
    ("PIPE", "UI", [(1345, 440), (1370, 440)], False),
]
PIPE = (195, 20, 1150, 690)


def svg_rect(x, y, w, h, fill, stroke, dashed=False, rx=7):
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="1.5"{dash}/>')


def svg_text(x, y, s, size=23, anchor="middle"):
    return (f'<text x="{x}" y="{y}" text-anchor="{anchor}" dominant-baseline="central" '
            f'font-family="{FONT}" font-size="{size}" fill="{INK}">{escape(s)}</text>')


def svg_line(pts, color=LINE, width=1.6, arrow=True, dashed=False):
    p = " ".join(f"{x},{y}" for x, y in pts)
    extra = ' marker-end="url(#arrow)"' if arrow else ""
    extra += ' stroke-dasharray="7 5"' if dashed else ""
    return f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="{width}"{extra}/>'


def node_text_svg(x, y, w, h, lines):
    cx, cy = x + w / 2, y + h / 2
    return [svg_text(cx, cy + (i - (len(lines) - 1) / 2) * 30, s) for i, s in enumerate(lines)]


svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
       '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" '
       f'markerUnits="userSpaceOnUse"><path d="M0 0 L8 4 L0 8 Z" fill="{LINE}"/></marker></defs>',
       '<rect width="100%" height="100%" fill="white"/>']
x, y, w, h = PIPE
svg.append(svg_rect(x, y, w, h, "#FFFFFF", FRAME, True, 8))
svg.append(svg_text(x + w / 2, 44, "分析管線", 24))
for gx, gy, gw, gh, fill, label in GROUPS.values():
    svg.append(svg_rect(gx, gy, gw, gh, fill, FRAME, True, 8))
    svg.append(svg_text(gx + 12, gy + 20, label, 20, "start"))

# input video icon (same drawing as the original figure)
VIDEO = [svg_rect(15, 360, 160, 160, "#F4F6F8", LINE, rx=8),
         svg_rect(56, 371, 90, 62, "#FFFFFF", LINE, rx=3),
         svg_rect(49, 378, 90, 62, "#FFFFFF", LINE, rx=3),
         svg_rect(42, 385, 90, 62, "#FFFFFF", LINE, rx=3),
         svg_line([(60, 397), (114, 397), (122, 435), (52, 435), (60, 397)], "#5B8F8A", 2, False),
         svg_line([(56, 416), (118, 416)], "#5B8F8A", 2, False),
         svg_line([(87, 397), (87, 435)], "#5B8F8A", 2, False),
         svg_text(95, 490, "賽事轉播影片", 21)]
svg += VIDEO
for nid, (nx, ny, nw, nh, fill, lines) in NODES.items():
    svg.append(svg_rect(nx, ny, nw, nh, fill, LINE))
    svg += node_text_svg(nx, ny, nw, nh, lines)
# monitor glyph above the review UI
svg.append(svg_rect(1402, 327, 80, 48, "#FFFFFF", LINE, rx=3))
svg.append(svg_line([(1442, 375), (1442, 385), (1422, 385), (1462, 385)], LINE, 2, False))
for _, _, pts, dashed in EDGES:
    svg.append(svg_line(pts, dashed=dashed))
# legend
svg.append(svg_line([(1040, 752), (1100, 752)], arrow=True))
svg.append(svg_text(1110, 752, "必要相依", 19, "start"))
svg.append(svg_line([(1220, 752), (1280, 752)], arrow=True, dashed=True))
svg.append(svg_text(1290, 752, "選用相依", 19, "start"))
svg.append("</svg>")
(OUT / f"{NAME}.svg").write_text("".join(svg), encoding="utf-8")

# ---- draw.io ----
cells = []


def vstyle(fill, stroke, dashed):
    return (f"rounded=1;arcSize=10;fillColor={fill};strokeColor={stroke};strokeWidth=1.5;"
            f"dashed={1 if dashed else 0};")


def tstyle(size, align="center"):
    return (f"text;html=0;whiteSpace=wrap;align={align};verticalAlign=middle;fontFamily={FONT};"
            f"fontSize={size};fontColor={INK};fontStyle=0;")


def vertex(cid, value, style, x, y, w, h):
    cells.append(f'<mxCell id="{cid}" value="{escape(value, {chr(34): "&quot;", chr(10): "&#10;"})}" '
                 f'style="{style}" vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" '
                 f'height="{h}" as="geometry" /></mxCell>')


def edge(cid, pts, style, src=None, tgt=None):
    ends = (f' source="{src}"' if src else "") + (f' target="{tgt}"' if tgt else "")
    mid = "".join(f'<mxPoint x="{x}" y="{y}" />' for x, y in pts[1:-1])
    cells.append(f'<mxCell id="{cid}" edge="1" parent="1" style="{style}"{ends}>'
                 f'<mxGeometry relative="1" as="geometry">'
                 f'<mxPoint x="{pts[0][0]}" y="{pts[0][1]}" as="sourcePoint" />'
                 f'<mxPoint x="{pts[-1][0]}" y="{pts[-1][1]}" as="targetPoint" />'
                 f'<Array as="points">{mid}</Array></mxGeometry></mxCell>')


BOXES = {"IN": (15, 360, 160, 160), "PIPE": PIPE,
         **{k: v[:4] for k, v in NODES.items()}}


def anchor(box, pt):
    bx, by, bw, bh = box
    return f"{(pt[0] - bx) / bw:.4f}", f"{(pt[1] - by) / bh:.4f}"


x, y, w, h = PIPE
vertex("PIPE", "", vstyle("#FFFFFF", FRAME, True), x, y, w, h)
vertex("PIPE-title", "分析管線", tstyle(24), x + 12, 28, w - 24, 32)
for gid, (gx, gy, gw, gh, fill, label) in GROUPS.items():
    vertex(gid, "", vstyle(fill, FRAME, True), gx, gy, gw, gh)
    vertex(f"{gid}-title", label, tstyle(20, "left"), gx + 12, gy + 6, gw - 24, 28)
vertex("IN", "", vstyle("#F4F6F8", LINE, False), 15, 360, 160, 160)
for i, (fx, fy) in enumerate([(56, 371), (49, 378), (42, 385)]):
    vertex(f"video-frame{i}", "", vstyle("#FFFFFF", LINE, False), fx, fy, 90, 62)
icon = "endArrow=none;strokeColor=#5B8F8A;strokeWidth=2;dashed=0;"
edge("video-court", [(60, 397), (114, 397), (122, 435), (52, 435), (60, 397)], icon)
edge("video-net", [(56, 416), (118, 416)], icon)
edge("video-middle", [(87, 397), (87, 435)], icon)
vertex("IN-text", "賽事轉播影片", tstyle(21), 15, 474, 160, 32)
for nid, (nx, ny, nw, nh, fill, lines) in NODES.items():
    vertex(nid, "\n".join(lines), vstyle(fill, LINE, False) + tstyle(23).replace("text;", ""),
           nx, ny, nw, nh)
vertex("UI-screen", "", vstyle("#FFFFFF", LINE, False), 1402, 327, 80, 48)
edge("UI-stand", [(1442, 375), (1442, 385), (1422, 385), (1462, 385)],
     "endArrow=none;strokeColor=#526174;strokeWidth=2;dashed=0;")
for i, (src, tgt, pts, dashed) in enumerate(EDGES):
    ex, ey = anchor(BOXES[src], pts[0])
    nx_, ny_ = anchor(BOXES[tgt], pts[-1])
    style = ("edgeStyle=none;rounded=0;html=0;endArrow=block;endFill=1;strokeColor=#526174;"
             f"strokeWidth=1.6;dashed={1 if dashed else 0};"
             f"exitX={ex};exitY={ey};exitPerimeter=0;entryX={nx_};entryY={ny_};entryPerimeter=0;")
    edge(f"e{i}-{src}-{tgt}", pts, style, src, tgt)
solid = "edgeStyle=none;endArrow=block;endFill=1;strokeColor=#526174;strokeWidth=1.6;"
edge("legend-solid", [(1040, 752), (1100, 752)], solid + "dashed=0;")
vertex("legend-solid-text", "必要相依", tstyle(19, "left"), 1110, 738, 100, 28)
edge("legend-dashed", [(1220, 752), (1280, 752)], solid + "dashed=1;")
vertex("legend-dashed-text", "選用相依", tstyle(19, "left"), 1290, 738, 100, 28)

drawio = (f"<?xml version='1.0' encoding='utf-8'?>\n<mxfile host=\"app.diagrams.net\">"
          f'<diagram name="{NAME}" id="{NAME}"><mxGraphModel page="1" pageWidth="{W}" '
          f'pageHeight="{H}" grid="1" gridSize="10"><root><mxCell id="0" />'
          f'<mxCell id="1" parent="0" />{"".join(cells)}</root></mxGraphModel></diagram></mxfile>\n')
(OUT / f"{NAME}.drawio").write_text(drawio, encoding="utf-8")
print("wrote", OUT / f"{NAME}.svg", OUT / f"{NAME}.drawio")
