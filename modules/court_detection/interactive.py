"""Interactive court-corner fine-tuning (OpenCV highGUI).

This is the *confirmation* half of the court_detection stage. It shows the
composite court image with the four auto-detected outer corners as draggable
handles; moving a corner recomputes the homography and re-projects all 16 key
points live. The user presses Enter to confirm or ESC to keep the automatic
result.

It is deliberately kept out of :mod:`modules.court_detection.module`: the
pipeline runner is headless and must never block on a GUI window. The module's
``run`` therefore takes an optional ``confirm`` callback, and only the CLI
(``python -m modules.court_detection``) injects :func:`fine_tune` as that
callback (see ``__main__``). When no display is available the whole step is
simply skipped and the automatic corners are used.

Ported from the standalone ``court_select.py`` prototype; geometry constants are
imported from :mod:`modules.court_detection.detector` so the two never drift.
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from modules.court_detection.detector import (
    H_LINES,
    OUTPUT_IDX,
    V_LINES,
)

# Indices within the 16-point list that are the four draggable outer corners
# (TL, TR, BL, BR) — this is the ordering detector.project_16 emits.
CORNER_IDXS = [0, 1, 2, 3]

# Court-space (V, H) coordinates of the four corners, in metres.
CORNER_COURT_PTS = np.float32([
    [V_LINES[0],  H_LINES[0]],   # TL
    [V_LINES[-1], H_LINES[0]],   # TR
    [V_LINES[0],  H_LINES[-1]],  # BL
    [V_LINES[-1], H_LINES[-1]],  # BR
])

# Lines to draw (pairs of indices into the 16-point output list).
COURT_DRAW_LINES = [
    (0,  1),   # top baseline
    (2,  3),   # bottom baseline
    (0,  2),   # left sideline
    (1,  3),   # right sideline
    (4,  6),   # left singles line
    (5,  7),   # right singles line
    (8,  9),   # top service line
    (10, 11),  # bottom service line
    (12, 13),  # center service line
    (14, 15),  # net
]

DISPLAY_W = 1280
DISPLAY_H = 720


# ── Geometry helpers ──────────────────────────────────────────────────────────

def recompute_from_corners(corners: np.ndarray) -> Optional[list]:
    """Project all 16 key points from 4 pixel corners ``[TL, TR, BL, BR]``.

    Returns a list of 16 ``(x, y)`` tuples, or ``None`` if the homography is
    degenerate.
    """
    H, _ = cv2.findHomography(CORNER_COURT_PTS, np.float32(corners))
    if H is None:
        return None
    pts = []
    for hi, vi in OUTPUT_IDX:
        p = H @ np.array([V_LINES[vi], H_LINES[hi], 1.0])
        pts.append((float(p[0] / p[2]), float(p[1] / p[2])))
    return pts


def is_detection_valid(pts: Optional[list], frame_shape) -> bool:
    """True if all four corners exist and sit within the frame (with margin)."""
    if pts is None or len(pts) < 4:
        return False
    h, w = frame_shape[:2]
    margin = 20
    for idx in CORNER_IDXS:
        x, y = pts[idx]
        if x < -margin or x > w + margin or y < -margin or y > h + margin:
            return False
    return True


def get_default_corners(frame_shape) -> np.ndarray:
    """Fallback corners at the image corners, for manual marking (4x2 float32)."""
    h, w = frame_shape[:2]
    return np.float32([
        [0.0, 0.0],            # TL
        [float(w), 0.0],       # TR
        [0.0, float(h)],       # BL
        [float(w), float(h)],  # BR
    ])


def scale_to_fit(img: np.ndarray, max_w: int, max_h: int):
    """Downscale ``img`` to fit ``max_w`` x ``max_h``; return (scaled, scale)."""
    h, w = img.shape[:2]
    s = min(max_w / w, max_h / h, 1.0)
    if s < 1.0:
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return img, s


def draw_court(img: np.ndarray, pts,
               line_color=(0, 220, 220),
               pt_color=(50, 255, 50),
               corner_color=(0, 140, 255),
               pt_r=4, corner_r=10,
               highlight_corners=True) -> np.ndarray:
    """Draw court boundary lines and key-point markers onto a copy of ``img``."""
    out = img.copy()
    if pts is None:
        h, w = out.shape[:2]
        cv2.putText(out, "DETECTION FAILED", (w // 2 - 150, h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
        return out

    for i, j in COURT_DRAW_LINES:
        if i < len(pts) and j < len(pts):
            p1 = (int(round(pts[i][0])), int(round(pts[i][1])))
            p2 = (int(round(pts[j][0])), int(round(pts[j][1])))
            cv2.line(out, p1, p2, line_color, 1, cv2.LINE_AA)

    for idx, (x, y) in enumerate(pts):
        cx, cy = int(round(x)), int(round(y))
        if highlight_corners and idx in CORNER_IDXS:
            cv2.circle(out, (cx, cy), corner_r,     corner_color,   -1, cv2.LINE_AA)
            cv2.circle(out, (cx, cy), corner_r + 2, (255, 255, 255), 1, cv2.LINE_AA)
        else:
            cv2.circle(out, (cx, cy), pt_r, pt_color, -1, cv2.LINE_AA)
    return out


# ── Interactive fine-tuning ───────────────────────────────────────────────────

def fine_tune(frame: np.ndarray, initial_pts: list, is_manual_mode: bool = False) -> list:
    """Drag the four outer corners to fine-tune; return 16 points (frame coords).

    Enter confirms, ESC reverts to ``initial_pts``, R resets to ``initial_pts``.
    Moving a corner recomputes the homography and re-projects all 16 points.
    Intended to be passed as the ``confirm`` callback to
    :meth:`CourtDetectionModule.run`.
    """
    disp_frame, scale = scale_to_fit(frame, DISPLAY_W, DISPLAY_H)
    dh, dw = disp_frame.shape[:2]

    def to_disp(pt):
        return np.array([pt[0] * scale, pt[1] * scale], dtype=np.float32)

    def from_disp(pt):
        return (pt[0] / scale, pt[1] / scale)

    corners_disp = np.array(
        [to_disp(initial_pts[i]) for i in CORNER_IDXS], dtype=np.float32)

    state = {
        "corners": corners_disp.copy(),
        "pts":     recompute_from_corners(corners_disp),
        "drag":    -1,
        "done":    False,
        "cancel":  False,
    }

    HANDLE_R = 6
    HIT_R    = HANDLE_R * 3
    TEXT_H   = 36

    mode_text = "Manual marking" if is_manual_mode else "Drag orange corners"
    instruction = f"{mode_text}  |  R: reset  |  Enter: confirm  |  ESC: cancel"

    def render():
        vis = draw_court(disp_frame, state["pts"],
                         corner_r=HANDLE_R, pt_r=3, highlight_corners=True)
        canvas = np.zeros((dh + TEXT_H, dw, 3), np.uint8)
        canvas[:dh] = vis
        canvas[dh:] = (30, 30, 30)
        cv2.putText(canvas, instruction, (8, dh + TEXT_H - 9),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (200, 200, 200), 1)
        return canvas

    def on_mouse(event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN:
            if state["pts"] is None or y >= dh:
                return
            best_k, best_d = -1, float("inf")
            for k, ci in enumerate(CORNER_IDXS):
                px = int(round(state["pts"][ci][0]))
                py = int(round(state["pts"][ci][1]))
                d = np.hypot(x - px, y - py)
                if d < HIT_R and d < best_d:
                    best_d, best_k = d, k
            state["drag"] = best_k
        elif event == cv2.EVENT_MOUSEMOVE and state["drag"] >= 0:
            state["corners"][state["drag"]] = [float(x), float(y)]
            state["pts"] = recompute_from_corners(state["corners"])
        elif event == cv2.EVENT_LBUTTONUP:
            state["drag"] = -1

    win = "Fine-tune Court Corners"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, dw, dh + TEXT_H)
    cv2.setMouseCallback(win, on_mouse)

    while not state["done"] and not state["cancel"]:
        cv2.imshow(win, render())
        key = cv2.waitKey(20) & 0xFF
        if key in (13, 10):       # Enter
            state["done"] = True
        elif key == 27:            # ESC -> revert
            state["cancel"] = True
        elif key in (ord('r'), ord('R')):  # reset to initial corners
            state["corners"] = np.array(
                [to_disp(initial_pts[i]) for i in CORNER_IDXS], dtype=np.float32)
            state["pts"] = recompute_from_corners(state["corners"])

    cv2.destroyWindow(win)

    if state["cancel"] or state["pts"] is None:
        return initial_pts
    return [from_disp(p) for p in state["pts"]]
