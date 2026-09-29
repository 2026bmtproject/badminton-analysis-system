"""跑一次場地偵測模組，把結果寫到本實驗資料夾，不碰 matches/。

為什麼要有這支程式
------------------
matches/<代號>/stages/court_detection/court.json 有些是人工調過的，不能拿來
當系統的自動偵測成績。所以評分前先用這支程式，對每一場重跑一次「headless
自動偵測」（和管線跑的是同一套 detector、同樣的中位數合成），把結果寫到

    experiments/court_detection/results/detected/<代號>.json

格式與 court.json 相同。eval_court.py 預設就讀這個資料夾，不讀 matches/。

用法
----
    uv run python experiments/court_detection/detect_court.py
    uv run python experiments/court_detection/detect_court.py test1 test6
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.artifacts import read_segments  # noqa: E402
from modules.court_detection import detector  # noqa: E402
from modules.court_detection.module import CourtDetectionConfig, CourtDetectionModule  # noqa: E402

HERE = Path(__file__).resolve().parent
ANS_DIR = HERE / "ans"
DETECTED_DIR = HERE / "results" / "detected"


def detect_one(code: str) -> dict:
    """對一場比賽重跑自動偵測，回傳 court.json 形狀的 dict。"""
    match_path = ROOT / "matches" / code
    module = CourtDetectionModule(config=CourtDetectionConfig())
    video = module._resolve_input_video(match_path)
    segments, _ = read_segments(match_path)

    # 和 module._run 相同的步驟：挑最長片段 -> 中位數合成 -> 偵測。
    picked = module._pick_segments(segments)
    picked_idx = [idx for idx, _ in picked]
    composite = module._build_composite(video, [seg for _, seg in picked])

    auto = detector.detect(composite)  # (16, 2) 或 None
    detection_failed = auto is None
    if detection_failed:
        # 偵測失敗：退回畫面四角當佔位（與模組行為一致；評分會視為失敗且不算距離）。
        h, w = composite.shape[:2]
        tl, tr, bl, br = [0.0, 0.0], [w - 1.0, 0.0], [0.0, h - 1.0], [w - 1.0, h - 1.0]
    else:
        pts = [(float(x), float(y)) for x, y in auto]
        tl, tr, bl, br = pts[0], pts[1], pts[2], pts[3]

    # detector 給的順序是 TL, TR, BL, BR；court.json 要順時針 TL, TR, BR, BL。
    corners_cw = [list(tl), list(tr), list(br), list(bl)]
    homography = detector.homography_from_corners(np.float32([tl, tr, bl, br]))
    return {
        "courts": [{
            "corners": corners_cw,
            "homography": None if homography is None else homography.tolist(),
            "segment_index": None,
        }],
        "segments_used": picked_idx,
        "detection_failed": detection_failed,
    }


def main() -> int:
    codes = sys.argv[1:] or sorted(p.stem for p in ANS_DIR.glob("*.json"))
    DETECTED_DIR.mkdir(parents=True, exist_ok=True)
    for code in codes:
        print(f"偵測 {code} ...", flush=True)
        result = detect_one(code)
        out = DETECTED_DIR / f"{code}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        status = "偵測失敗" if result["detection_failed"] else "ok"
        print(f"  -> {out}  ({status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
