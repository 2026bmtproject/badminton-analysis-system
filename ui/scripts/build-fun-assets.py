"""把派對模式的原始素材轉成前端直接能用的檔案（src/assets/fun/）。

只有換素材時才需要重跑；產出的檔案會 commit，平常建置不跑這支。

    uv run python ui/scripts/build-fun-assets.py <原始素材資料夾>

原始素材資料夾要有：
    oiia-oiiaoiia.gif            旋轉貓
    oiiaioiiiai-cat.mp3          oiia 音樂
    images.png                   nyan cat 像素圖
    Nyanyanyanyanyanyanya!-(48k).mp3
    鋼管落地.mp3

產出：
    fun-assets.json               貓的 sprite 格子與分段、oiia 人聲起訖、nyan 循環點
    oiia-sprite.webp              去背後的貓（sprite sheet）
    oiia.mp3                      原檔照搬
    nyan.png                      33x20 透明底像素圖（一格一 px，前端最近鄰放大）
    nyan.mp3                      主旋律一個完整循環（64 拍），前後各留一點給 loopStart/loopEnd
    pipe.mp3                      去掉開頭靜音、尾巴淡出的鋼管聲
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageSequence

UI = Path(__file__).resolve().parents[1]
OUT = UI / "src" / "assets" / "fun"
FFMPEG = UI / "node_modules" / "@ffmpeg-installer" / "win32-x64" / "ffmpeg.exe"

#: gif 的分段是看過每一幀後手標的：兩段旋轉，中間一段站著（最後幾幀帶暗角，不用）。
OIIA_SPINS = ((0, 57), (90, 152))
OIIA_STAND = (58, 89)
OIIA_SPRITE_HEIGHT = 128
OIIA_COLUMNS = 15

#: nyan 主旋律從 ~8.4s 開始，每 64 拍（142 BPM，約 27.05s）重複一次。
NYAN_LOOP_FROM = 9.0
NYAN_LOOP_GUESS = 27.05
NYAN_PAD = 0.15

PIPE_FROM = 0.34
PIPE_TO = 2.4


def ffmpeg(*args: str) -> None:
    subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def pcm(path: Path, rate: int) -> np.ndarray:
    raw = subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", "-i", str(path),
                          "-map", "0:a", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


# ── oiia ─────────────────────────────────────────────────────────────────────
def cut_out(bgr: np.ndarray) -> np.ndarray:
    """去掉 gif 的淡灰白底，回傳 0-255 alpha（同 tools/overlay_viewer.py 的 _cut_out）。

    底色帶暗角，所以用「相鄰像素差」的 floodFill 從四邊往內灌；灌不到的就是貓。
    只留最大的一塊，邊緣收 1px 再羽化，免得留一圈白邊。
    """
    h, w = bgr.shape[:2]
    blur = cv2.GaussianBlur(bgr, (5, 5), 0)
    mask = np.zeros((h + 2, w + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    seeds = [(x, y) for x in range(0, w, 8) for y in (0, h - 1)]
    seeds += [(x, y) for y in range(0, h, 8) for x in (0, w - 1)]
    for x, y in seeds:
        if mask[y + 1, x + 1] == 0:
            cv2.floodFill(blur, mask, (x, y), (0, 0, 0), (3, 3, 3), (3, 3, 3), flags)
    fg = np.where(mask[1:-1, 1:-1] == 0, 255, 0).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(fg)
    if n > 1:
        fg = np.where(lab == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA]), 255, 0).astype(np.uint8)
    fg = cv2.erode(fg, np.ones((3, 3), np.uint8))
    return cv2.GaussianBlur(fg, (3, 3), 0)


def build_oiia_sprite(gif: Path) -> dict:
    frames = [np.array(f.convert("RGB")) for f in ImageSequence.Iterator(Image.open(gif))]
    alphas = [cut_out(cv2.cvtColor(f, cv2.COLOR_RGB2BGR)) for f in frames]
    # 每幀同一個裁切框（所有幀的聯集），貓才會在原地轉
    ys, xs = np.nonzero(np.maximum.reduce(alphas))
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    height = OIIA_SPRITE_HEIGHT
    width = int(round((x1 - x0) * height / (y1 - y0)))
    rows = -(-len(frames) // OIIA_COLUMNS)
    sheet = np.zeros((rows * height, OIIA_COLUMNS * width, 4), np.uint8)
    for i, (rgb, alpha) in enumerate(zip(frames, alphas)):
        rgba = np.dstack([rgb[y0:y1, x0:x1], alpha[y0:y1, x0:x1]])
        small = cv2.resize(rgba, (width, height), interpolation=cv2.INTER_AREA)
        r, c = divmod(i, OIIA_COLUMNS)
        sheet[r * height:(r + 1) * height, c * width:(c + 1) * width] = small
    Image.fromarray(sheet, "RGBA").save(OUT / "oiia-sprite.webp", quality=88, method=6)
    return {"frameWidth": width, "frameHeight": height, "columns": OIIA_COLUMNS, "count": len(frames),
            "spins": [list(s) for s in OIIA_SPINS], "stand": list(OIIA_STAND)}


def oiia_voice(mp3: Path) -> dict:
    """音樂裡每段「oiia」人聲的起訖秒數：20ms 響度超過峰值 15% 算有聲，短於 0.15s 的空隙併掉。"""
    rate, hop = 8000, 160
    a = pcm(mp3, rate)
    rms = np.sqrt(np.convolve(a ** 2, np.ones(hop) / hop, "same")[::hop])
    loud = rms > 0.15 * rms.max()
    bursts: list[list[float]] = []
    for i in np.flatnonzero(loud):
        t = i * hop / rate
        if bursts and t - bursts[-1][1] < 0.15:
            bursts[-1][1] = t + hop / rate
        else:
            bursts.append([t, t + hop / rate])
    bursts = [[round(s, 3), round(e, 3)] for s, e in bursts if e - s > 0.3]
    # 循環從第一段人聲前 0.1s 開始，跳過開頭一秒多的靜音；結尾的靜音留著當換氣
    return {"loopStart": round(max(bursts[0][0] - 0.1, 0.0), 3), "loopEnd": round(len(a) / rate, 3),
            "bursts": bursts}


# ── nyan ─────────────────────────────────────────────────────────────────────
def build_nyan_png(png: Path) -> None:
    """還原成原始格子（一格 8px），白底從四邊灌掉；眼睛的白被灰臉包住，會留下來。"""
    rgb = np.array(Image.open(png).convert("RGB"))
    block = 8
    grid = np.ascontiguousarray(rgb[block // 2::block, block // 2::block])
    h, w = grid.shape[:2]
    fill = np.zeros((h + 2, w + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    work = grid.copy()
    for x, y in [(x, y) for x in range(w) for y in (0, h - 1)] + [(x, y) for y in range(h) for x in (0, w - 1)]:
        if fill[y + 1, x + 1] == 0 and (grid[y, x] == 255).all():
            cv2.floodFill(work, fill, (x, y), (0, 0, 0), (0, 0, 0), (0, 0, 0), flags)
    alpha = np.where(fill[1:-1, 1:-1] == 0, 255, 0).astype(np.uint8)
    Image.fromarray(np.dstack([grid, alpha]), "RGBA").save(OUT / "nyan.png", optimize=True)


def nyan_loop(mp3: Path) -> tuple[float, float]:
    """在 NYAN_LOOP_GUESS 附近 ±40ms 找波形最像的位置，回傳 (循環起點, 循環長度)。"""
    rate = 22050
    a = pcm(mp3, rate)
    start = int(NYAN_LOOP_FROM * rate)
    win = int(0.5 * rate)
    ref = a[start:start + win]
    lags = range(int((NYAN_LOOP_GUESS - 0.04) * rate), int((NYAN_LOOP_GUESS + 0.04) * rate))
    score = [float(ref @ a[start + lag:start + lag + win]) for lag in lags]
    return NYAN_LOOP_FROM, lags[int(np.argmax(score))] / rate


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    src = Path(sys.argv[1])
    OUT.mkdir(parents=True, exist_ok=True)

    oiia = build_oiia_sprite(src / "oiia-oiiaoiia.gif")
    oiia["audio"] = oiia_voice(src / "oiiaioiiiai-cat.mp3")
    shutil.copyfile(src / "oiiaioiiiai-cat.mp3", OUT / "oiia.mp3")

    build_nyan_png(src / "images.png")
    begin, length = nyan_loop(src / "Nyanyanyanyanyanyanya!-(48k).mp3")
    # 前後各多留 NYAN_PAD：mp3 解碼的前導延遲不會吃到循環本身
    ffmpeg("-ss", f"{begin - NYAN_PAD:.4f}", "-t", f"{length + 2 * NYAN_PAD:.4f}",
           "-i", str(src / "Nyanyanyanyanyanyanya!-(48k).mp3"), "-map", "0:a",
           "-ar", "44100", "-b:a", "128k", str(OUT / "nyan.mp3"))

    ffmpeg("-ss", f"{PIPE_FROM}", "-to", f"{PIPE_TO}", "-i", str(src / "鋼管落地.mp3"),
           "-af", f"afade=t=out:st={PIPE_TO - PIPE_FROM - 0.4:.2f}:d=0.4", "-b:a", "128k", str(OUT / "pipe.mp3"))

    meta = {"oiia": oiia, "nyan": {"loopStart": NYAN_PAD, "loopEnd": round(NYAN_PAD + length, 4)}}
    (OUT / "fun-assets.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
