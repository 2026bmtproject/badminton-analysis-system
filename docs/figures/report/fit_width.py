"""Shrink a flowchart's geometry to a maximum width while keeping its type size.

The LaTeX report (docs/latex) prints every flowchart at one fixed scale, so that
23 px text is the same size on the page in every chart. A chart drawn wider than
MAX_W would not fit the text width at that scale; this module scales its
geometry (positions, box sizes, paths) by MAX_W / width and leaves font sizes,
stroke widths and arrowheads alone. Labels under MIN_FONT are raised to it. Lines of one multi-line label keep their
original spacing around their scaled centre.

    uv run python docs/figures/report/fit_width.py system-overview match-segmentation

rewrites ``<name>.svg`` and ``<name>.drawio`` in place (run from this folder or
pass paths). It is idempotent, so it is safe to run on every chart after the
generators: ``python fit_width.py *.svg``.
"""
import re
import sys
from pathlib import Path

MAX_W = 1300  # px; at the report's flowchart scale this is the full text width
MIN_FONT = 20  # px; smaller labels are raised to this so nothing prints under ~7.4 pt

NUM = r"-?\d+(?:\.\d+)?"


def _fmt(v):
    v = round(v, 2)
    return str(int(v)) if v == int(v) else f"{v:g}"


def _scale_nums(text, s):
    return re.sub(NUM, lambda m: _fmt(float(m.group()) * s), text)


def _scale_path(d, s):
    """Scale path data; for arcs the rotation and the two flags stay as they are."""
    out = []
    for cmd, args in re.findall(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)", d):
        nums = re.findall(NUM, args)
        if cmd in "Aa":
            vals = [float(n) * s if i % 7 not in (2, 3, 4) else float(n) for i, n in enumerate(nums)]
        else:
            vals = [float(n) * s for n in nums]
        out.append(cmd + " ".join(_fmt(v) for v in vals))
    return " ".join(out)


GEOM_ATTRS = ("x", "y", "width", "height", "rx", "ry", "cx", "cy", "r", "x1", "y1", "x2", "y2")


def fit_svg(svg, max_w=MAX_W):
    """Return ``svg`` with its geometry scaled so the canvas is at most ``max_w`` wide."""
    w = float(re.search(r'<svg[^>]*\swidth="(' + NUM + ')"', svg).group(1))
    s = min(1.0, max_w / w)
    svg = re.sub(r'font-size="(' + NUM + ')"',
                 lambda m: f'font-size="{_fmt(max(float(m.group(1)), MIN_FONT))}"', svg)
    if s == 1.0:
        return svg
    defs = re.search(r"<defs>.*?</defs>", svg, re.S)
    head, body = (svg[:defs.end()], svg[defs.end():]) if defs else ("", svg)

    def root(m):
        tag = m.group(0)
        tag = re.sub(r'(\s(?:width|height)=")(' + NUM + ')"', lambda a: f'{a.group(1)}{_fmt(float(a.group(2)) * s)}"', tag)
        return re.sub(r'viewBox="([^"]*)"', lambda a: f'viewBox="{_scale_nums(a.group(1), s)}"', tag)

    head = re.sub(r"<svg[^>]*>", root, head, count=1)

    # Text: remember each label line's original y so a stack of lines can keep its spacing.
    texts = []

    def element(m):
        tag = m.group(0)
        if tag.startswith("<text"):
            x = float(re.search(r'\sx="(' + NUM + ')"', tag).group(1))
            y = float(re.search(r'\sy="(' + NUM + ')"', tag).group(1))
            size = float(re.search(r'font-size="(' + NUM + ')"', tag).group(1))
            texts.append((x, y, size))
            return f"<text@@{len(texts) - 1}@@" + tag[5:]
        for a in GEOM_ATTRS:
            tag = re.sub(r'(\s' + a + r'=")(' + NUM + ')"', lambda v: f'{v.group(1)}{_fmt(float(v.group(2)) * s)}"', tag)
        tag = re.sub(r'points="([^"]*)"', lambda v: f'points="{_scale_nums(v.group(1), s)}"', tag)
        tag = re.sub(r'\sd="([^"]*)"', lambda v: f' d="{_scale_path(v.group(1), s)}"', tag)
        return tag

    body = re.sub(r"<(?:rect|text|polyline|polygon|path|line|circle|ellipse)\b[^>]*>", element, body)

    # Group consecutive lines that share x and size and sit within 1.5 line heights.
    new_y = {}
    i = 0
    while i < len(texts):
        j = i
        while (j + 1 < len(texts) and texts[j + 1][0] == texts[i][0] and texts[j + 1][2] == texts[i][2]
               and 0 < texts[j + 1][1] - texts[j][1] <= texts[i][2] * 1.5):
            j += 1
        ys = [t[1] for t in texts[i:j + 1]]
        centre = (ys[0] + ys[-1]) / 2
        for k, y in enumerate(ys, start=i):
            new_y[k] = centre * s + (y - centre)
        i = j + 1

    def place(m):
        k = int(m.group(1))
        tag = m.group(2)
        tag = re.sub(r'(\sx=")(' + NUM + ')"', lambda v: f'{v.group(1)}{_fmt(float(v.group(2)) * s)}"', tag)
        tag = re.sub(r'(\sy=")(' + NUM + ')"', lambda v: f'{v.group(1)}{_fmt(new_y[k])}"', tag)
        return "<text" + tag

    body = re.sub(r"<text@@(\d+)@@([^>]*>)", place, body)
    return head + body


def fit_drawio(xml, max_w=MAX_W, width=None):
    """Same scaling for the draw.io source; ``width`` is the SVG canvas width."""
    if width is None:
        width = float(re.search(r'pageWidth="(' + NUM + ')"', xml).group(1))
    s = min(1.0, max_w / width)
    xml = re.sub(r'fontSize=(' + NUM + ')', lambda m: f"fontSize={_fmt(max(float(m.group(1)), MIN_FONT))}", xml)
    if s == 1.0:
        return xml

    def geom(m):
        tag = m.group(0)
        for a in ("x", "y", "width", "height"):
            tag = re.sub(r'(\s' + a + r'=")(' + NUM + ')"', lambda v: f'{v.group(1)}{_fmt(float(v.group(2)) * s)}"', tag)
        return tag

    xml = re.sub(r"<mx(?:Geometry|Point)\b[^>]*>", geom, xml)
    xml = re.sub(r'(page(?:Width|Height)=")(' + NUM + ')"', lambda v: f'{v.group(1)}{_fmt(float(v.group(2)) * s)}"', xml)
    # Shape parameters measured in px (note fold, cylinder cap, hexagon edge, label inset).
    xml = re.sub(r'((?:^|;|")(?:size|spacingTop)=)(' + NUM + ')', lambda v: f"{v.group(1)}{_fmt(float(v.group(2)) * s)}", xml)
    return xml


if __name__ == "__main__":
    for name in sys.argv[1:]:
        base = Path(name)
        base = base.with_suffix("") if base.suffix else base
        svg_path, drawio_path = base.with_suffix(".svg"), base.with_suffix(".drawio")
        svg = svg_path.read_text(encoding="utf-8")
        w = float(re.search(r'<svg[^>]*\swidth="(' + NUM + ')"', svg).group(1))
        svg_path.write_text(fit_svg(svg), encoding="utf-8")
        if drawio_path.exists():
            drawio_path.write_text(fit_drawio(drawio_path.read_text(encoding="utf-8"), width=w), encoding="utf-8")
        print(f"{base.name}: {w:g} px -> {min(w, MAX_W):g} px")
