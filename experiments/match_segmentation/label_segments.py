"""手動標記賽事影片分段的真值（正解），用來驗證系統準確率。

概念
----
match_segmentation 切出來的 segment 對應「主鏡頭（比賽中）的一段連續鏡頭」，
segment 邊界 = 鏡頭切換（camera cut）。一次鏡頭切換是相鄰兩幀之間的劇烈視覺
不連續，攤成縮圖流時「一眼」就能看出來。所以：

    主畫面（縮圖流）  ── 負責『搜尋』。每個縮圖代表 N 幀（N 可調），快速捲動
                        整支 cache 影片，找出鏡頭切換點與偵測錯/漏的地方。
    局部放大（逐幀）  ── 負責『精修』。點任一縮圖進入逐幀檢視，把鏡頭切換的
                        『確切那一幀』標成 segment 邊界。

種子（seed）
    直接載入 stages/match_segmentation/segments.json 的 112 段，你只改動錯的地方
    （調邊界 / 刪多的 / 補漏的）。>99% 正確，改 delta 最省力。

輸出
    matches/<代號>/ans/segments.csv   欄位比照 segments.json：
                        start_frame,end_frame,start_sec,end_sec,duration_sec

    這是「工作副本」。標完之後把它複製到
    experiments/match_segmentation/ans/<代號>.csv（發布副本，評分程式讀的
    是那一份）。

讀的是 cache 裡被 match_segmentation 掃描用的降頻影片（畫質低、更快），
它的幀索引與 segments.json 完全對齊（同一支影片、同 fps）。

用法
----
    uv run python experiments/match_segmentation/label_segments.py
    uv run python experiments/match_segmentation/label_segments.py matches/ASY_vs_PC_2021
    uv run python experiments/match_segmentation/label_segments.py matches/ASG_vs_AA_2020
        --port 8756 --base-stride 5 --thumb-width 128 --cols 24

參數
    --base-stride   縮圖流最細的顆粒度（每個縮圖代表幾幀）；也是可調『每縮圖幀數』
                    的最小步進。預設 5。改這個值會重建 sprite 快取。
    --thumb-width   縮圖寬（px），高依 16:9 推導。預設 128。改這個值會重建快取。
    --cols          縮圖流預設欄數（UI 可再調）。預設 24。
    --port          預設 8756。
    --no-open       不自動開瀏覽器。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.artifacts import read_segments  # noqa: E402

# sprite 版面：每張 sprite 排 SPRITE_COLS x SPRITE_ROWS 個縮圖。
SPRITE_COLS = 20
SPRITE_ROWS = 20
PER_SHEET = SPRITE_COLS * SPRITE_ROWS


# --------------------------------------------------------------------------- #
# 影片 / 路徑解析
# --------------------------------------------------------------------------- #
def resolve_cache_video(match_path: Path) -> Path:
    """cache 裡 match_segmentation 掃描用的降頻影片（*.mp4，取畫質最低者）。"""
    cache = match_path / "cache"
    vids = sorted(cache.glob("*.mp4"))
    if not vids:
        raise FileNotFoundError(f"找不到 cache 影片：{cache}/*.mp4")
    # 取高度最小（畫質最低、最快）的那支
    def height(p: Path) -> float:
        c = cv2.VideoCapture(str(p))
        h = c.get(cv2.CAP_PROP_FRAME_HEIGHT)
        c.release()
        return h or 1e9
    return min(vids, key=height)


def pick_default_match() -> Path:
    matches = ROOT / "matches"
    for d in sorted(matches.iterdir()):
        if not d.is_dir():
            continue
        if (d / "stages" / "match_segmentation" / "segments.json").is_file() and list(
            (d / "cache").glob("*.mp4")
        ):
            return d
    raise FileNotFoundError("matches/ 下找不到同時有 cache 影片與 segments.json 的比賽")


# --------------------------------------------------------------------------- #
# sprite 產生 / 快取
# --------------------------------------------------------------------------- #
class Thumbs:
    """把 cache 影片每 base_stride 幀抽一張縮圖，打包成 sprite 表並快取到 cache/。"""

    def __init__(self, video: Path, cache_root: Path, base_stride: int, thumb_w: int):
        self.video = video
        self.base_stride = base_stride
        self.thumb_w = thumb_w
        cap = cv2.VideoCapture(str(video))
        self.fps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
        vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 480
        vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 854
        cap.release()
        self.thumb_h = max(1, round(thumb_w * vh / vw))
        self.dir = cache_root / "mark_thumbs" / f"s{base_stride}_w{thumb_w}"
        self.manifest_path = self.dir / "manifest.json"
        self.total_frames = 0          # 實際解出的幀數
        self.n_thumbs = 0              # 抽出的縮圖數
        self.n_sheets = 0

    def sheet_px(self) -> tuple[int, int]:
        return SPRITE_COLS * self.thumb_w, SPRITE_ROWS * self.thumb_h

    def ensure(self, log=print) -> None:
        if self.manifest_path.is_file():
            m = json.loads(self.manifest_path.read_text())
            self.total_frames = m["total_frames"]
            self.n_thumbs = m["n_thumbs"]
            self.n_sheets = m["n_sheets"]
            log(f"[thumbs] 使用快取 {self.dir}（{self.n_thumbs} 縮圖 / {self.n_sheets} sprite）")
            return
        self._build(log)

    def _blank_sheet(self) -> np.ndarray:
        sw, sh = self.sheet_px()
        return np.zeros((sh, sw, 3), np.uint8)

    def _build(self, log) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(self.video))
        approx = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        log(f"[thumbs] 產生縮圖（base_stride={self.base_stride}, {self.thumb_w}x{self.thumb_h}）約 {approx} 幀…")
        sheet = self._blank_sheet()
        idx_in_sheet = 0
        sheet_no = 0
        frame_i = 0        # 目前解出的幀序號
        n_thumbs = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_i % self.base_stride == 0:
                th = cv2.resize(frame, (self.thumb_w, self.thumb_h), interpolation=cv2.INTER_AREA)
                col = idx_in_sheet % SPRITE_COLS
                row = idx_in_sheet // SPRITE_COLS
                y0 = row * self.thumb_h
                x0 = col * self.thumb_w
                sheet[y0:y0 + self.thumb_h, x0:x0 + self.thumb_w] = th
                idx_in_sheet += 1
                n_thumbs += 1
                if idx_in_sheet == PER_SHEET:
                    self._write_sheet(sheet_no, sheet)
                    sheet_no += 1
                    idx_in_sheet = 0
                    sheet = self._blank_sheet()
                if n_thumbs % 2000 == 0:
                    log(f"[thumbs]   …{n_thumbs} 縮圖 / 幀 {frame_i}")
            frame_i += 1
        if idx_in_sheet > 0:
            self._write_sheet(sheet_no, sheet)
            sheet_no += 1
        cap.release()
        self.total_frames = frame_i
        self.n_thumbs = n_thumbs
        self.n_sheets = sheet_no
        self.manifest_path.write_text(json.dumps({
            "total_frames": frame_i,
            "n_thumbs": n_thumbs,
            "n_sheets": sheet_no,
            "base_stride": self.base_stride,
            "thumb_w": self.thumb_w,
            "thumb_h": self.thumb_h,
            "sprite_cols": SPRITE_COLS,
            "sprite_rows": SPRITE_ROWS,
        }, indent=2))
        log(f"[thumbs] 完成：{n_thumbs} 縮圖 / {sheet_no} sprite → {self.dir}")

    def _write_sheet(self, no: int, sheet: np.ndarray) -> None:
        p = self.dir / f"sprite_{no:05d}.jpg"
        cv2.imwrite(str(p), sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])

    def sheet_bytes(self, no: int) -> bytes | None:
        p = self.dir / f"sprite_{no:05d}.jpg"
        return p.read_bytes() if p.is_file() else None


# --------------------------------------------------------------------------- #
# 逐幀（zoom）：按需從影片解出確切一幀
# --------------------------------------------------------------------------- #
class FrameServer:
    def __init__(self, video: Path):
        self.cap = cv2.VideoCapture(str(video))
        self.lock = threading.Lock()
        self._last = -10

    def frame_jpeg(self, n: int, width: int) -> bytes | None:
        with self.lock:
            if n != self._last + 1:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, n)
            ok, frame = self.cap.read()
            self._last = n if ok else -10
            if not ok:
                return None
            if width and width != frame.shape[1]:
                h = round(width * frame.shape[0] / frame.shape[1])
                frame = cv2.resize(frame, (width, h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return buf.tobytes() if ok else None

    def strip_jpeg(self, start: int, n: int, width: int) -> bytes | None:
        """把 start..start+n-1 這 n 幀（連續）解成一張橫向 1×n sprite。"""
        with self.lock:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            self._last = -10
            frames = []
            for _ in range(n):
                ok, fr = self.cap.read()
                if not ok:
                    break
                h = round(width * fr.shape[0] / fr.shape[1])
                frames.append(cv2.resize(fr, (width, h), interpolation=cv2.INTER_AREA))
            if not frames:
                return None
            h = frames[0].shape[0]
            blank = np.zeros((h, width, 3), np.uint8)
            while len(frames) < n:                    # 影片尾端不足則補黑
                frames.append(blank)
            sprite = np.hstack(frames)
            ok, buf = cv2.imencode(".jpg", sprite, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return buf.tobytes() if ok else None


# --------------------------------------------------------------------------- #
# 狀態 + CSV
# --------------------------------------------------------------------------- #
def load_seed_segments(match_path: Path) -> tuple[list[dict], float]:
    """優先讀 ans/segments.csv（續標），否則讀偵測 segments.json（種子）。"""
    ans = match_path / "ans" / "segments.csv"
    if ans.is_file():
        segs = []
        import csv
        with ans.open(newline="") as f:
            for row in csv.DictReader(f):
                segs.append({"s": int(row["start_frame"]), "e": int(row["end_frame"])})
        segs.sort(key=lambda x: x["s"])
        return segs, "ans"
    records, _fps = read_segments(match_path)
    segs = [{"s": int(r["start_frame"]), "e": int(r["end_frame"])} for r in records]
    segs.sort(key=lambda x: x["s"])
    return segs, "seed"


def write_ans_csv(match_path: Path, segments: list[dict], fps: float) -> Path:
    import csv
    out = match_path / "ans" / "segments.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    clean = []
    for s in segments:
        a, b = int(s["s"]), int(s["e"])
        if b < a:
            a, b = b, a
        clean.append((a, b))
    clean.sort()
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start_frame", "end_frame", "start_sec", "end_sec", "duration_sec"])
        for a, b in clean:
            sa, sb = round(a / fps, 3), round(b / fps, 3)
            # 起訖影格皆含兩端，故片段長度是 (b - a + 1) 影格。
            # 不可用 sb - sa（= (b - a)/fps），會少算一影格。
            w.writerow([a, b, sa, sb, round((b - a + 1) / fps, 3)])
    return out


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, match_path: Path, thumbs: Thumbs, frames: FrameServer, cols: int):
        self.match_path = match_path
        self.thumbs = thumbs
        self.frames = frames
        self.cols = cols
        self.fps = thumbs.fps
        segs, source = load_seed_segments(match_path)
        self.seed_source = source

    def meta(self) -> dict:
        seed_records, _ = read_segments(self.match_path)
        seed = [{"s": int(r["start_frame"]), "e": int(r["end_frame"])} for r in seed_records]
        segs, source = load_seed_segments(self.match_path)
        return {
            "match": self.match_path.name,
            "fps": self.fps,
            "total_frames": self.thumbs.total_frames,
            "base_stride": self.thumbs.base_stride,
            "thumb_w": self.thumbs.thumb_w,
            "thumb_h": self.thumbs.thumb_h,
            "sprite_cols": SPRITE_COLS,
            "sprite_rows": SPRITE_ROWS,
            "per_sheet": PER_SHEET,
            "n_thumbs": self.thumbs.n_thumbs,
            "n_sheets": self.thumbs.n_sheets,
            "sheet_w": self.thumbs.sheet_px()[0],
            "sheet_h": self.thumbs.sheet_px()[1],
            "cols": self.cols,
            "segments": segs,
            "seed": seed,
            "source": source,
        }


def make_handler(app: App):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # 安靜
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif u.path == "/meta":
                self._send(200, json.dumps(app.meta()).encode("utf-8"), "application/json")
            elif u.path.startswith("/sprite/"):
                try:
                    no = int(u.path.rsplit("/", 1)[1].split(".")[0])
                except ValueError:
                    self._send(404, b"", "text/plain"); return
                data = app.thumbs.sheet_bytes(no)
                if data is None:
                    self._send(404, b"", "text/plain")
                else:
                    self._send(200, data, "image/jpeg")
            elif u.path == "/frame":
                n = int(q.get("n", ["0"])[0])
                w = int(q.get("w", ["854"])[0])
                data = app.frames.frame_jpeg(n, w)
                if data is None:
                    self._send(404, b"", "text/plain")
                else:
                    self._send(200, data, "image/jpeg")
            elif u.path == "/strip":
                start = int(q.get("start", ["0"])[0])
                n = int(q.get("n", ["20"])[0])
                w = int(q.get("w", ["160"])[0])
                data = app.frames.strip_jpeg(start, n, w)
                if data is None:
                    self._send(404, b"", "text/plain")
                else:
                    self._send(200, data, "image/jpeg")
            else:
                self._send(404, b"", "text/plain")

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/save":
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                segs = payload.get("segments", [])
                out = write_ans_csv(app.match_path, segs, app.fps)
                body = json.dumps({"ok": True, "path": str(out), "n": len(segs)}).encode()
                self._send(200, body, "application/json")
            else:
                self._send(404, b"", "text/plain")

    return H


# --------------------------------------------------------------------------- #
# 前端（單頁）
# --------------------------------------------------------------------------- #
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8"><title>segment marker</title>
<style>
  :root{--bg:#111;--fg:#e8e8e8;--dim:#888;--seg:#39d353;--cut-in:#39d353;--cut-out:#ff5a5a;
        --panel:#1b1b1b;--line:#333;--accent:#4aa3ff;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
            font:13px/1.4 system-ui,"Noto Sans TC",sans-serif}
  #bar{display:flex;gap:12px;align-items:center;padding:6px 10px;background:var(--panel);
       border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5;flex-wrap:wrap}
  #bar b{color:#fff}
  .ctl{display:flex;gap:4px;align-items:center;color:var(--dim)}
  .ctl input{width:64px;background:#000;color:var(--fg);border:1px solid var(--line);
             border-radius:4px;padding:2px 4px}
  button{background:#262626;color:var(--fg);border:1px solid var(--line);border-radius:4px;
         padding:3px 9px;cursor:pointer}
  button:hover{background:#333}
  button.on{background:var(--accent);color:#000;border-color:var(--accent)}
  #status{margin-left:auto;color:var(--dim)}
  #unsaved{color:#ffcf4a}
  #grid{position:relative;overflow:auto;height:calc(100vh - 42px)}
  #canvas{position:relative}
  .th{position:absolute;background-repeat:no-repeat;outline:1px solid #000;cursor:pointer}
  .th.inseg{box-shadow:inset 0 3px 0 var(--seg)}
  .th.cin{box-shadow:inset 4px 0 0 var(--cut-in),inset 0 3px 0 var(--seg)}
  .th.cout{box-shadow:inset -4px 0 0 var(--cut-out),inset 0 3px 0 var(--seg)}
  .th.dim{opacity:.18}
  .th.sel{outline:2px solid var(--accent)}
  /* zoom */
  #zoom{position:fixed;inset:0;background:rgba(0,0,0,.9);display:none;z-index:20;
        flex-direction:column;align-items:center;justify-content:center;gap:10px}
  #zimg{max-width:92vw;max-height:42vh;background:#000;outline:1px solid #333}
  #zstrip{display:flex;flex-wrap:wrap;gap:2px;justify-content:center;margin:0 auto}
  #zstrip .cell{background-repeat:no-repeat;cursor:pointer;outline:1px solid #000;
                position:relative;flex:0 0 auto}
  #zstrip .cell.cur{outline:2px solid var(--accent);z-index:1}
  #zstrip .cell.cin{box-shadow:inset 4px 0 0 var(--cut-in)}
  #zstrip .cell.cout{box-shadow:inset -4px 0 0 var(--cut-out)}
  #zstrip .cell.pend{box-shadow:inset 0 0 0 3px #ffcf4a}
  #zstrip .cell b{position:absolute;bottom:0;left:0;font-size:9px;color:#fff;
                  background:rgba(0,0,0,.55);padding:0 2px;line-height:1.3}
  #zinfo{font-size:15px}
  #zinfo .tag{padding:1px 6px;border-radius:4px;margin-left:6px}
  .tag.in{background:var(--seg);color:#000}
  .tag.gap{background:#444}
  .tag.pend{background:#ffcf4a;color:#000}
  #zbtns{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;max-width:92vw}
  #zhint{color:var(--dim);font-size:12px;max-width:720px;text-align:center}
  #ztoast{position:fixed;top:52px;left:50%;transform:translateX(-50%);background:#ffcf4a;
          color:#000;padding:4px 12px;border-radius:6px;opacity:0;transition:opacity .25s;
          pointer-events:none;font-size:13px;z-index:30}
  kbd{background:#222;border:1px solid #444;border-bottom-width:2px;border-radius:3px;
      padding:0 5px;color:#fff}
</style></head>
<body>
<div id="bar">
  <b id="mtitle">…</b>
  <span class="ctl">每縮圖幀數 <input id="fpt" type="number"></span>
  <span class="ctl">欄數 <input id="cols" type="number"></span>
  <span class="ctl">檢視
    <button data-task="all" class="task on">全部</button>
    <button data-task="seg" class="task">看 segment</button>
    <button data-task="gap" class="task">看 gap（漏抓）</button>
  </span>
  <button id="save">儲存 CSV</button>
  <button id="reseed" title="丟棄目前編輯，重載偵測種子 segments.json">重載種子</button>
  <button id="help">?</button>
  <span id="status"></span>
</div>
<div id="grid"><div id="canvas"></div></div>

<div id="zoom">
  <img id="zimg">
  <div id="zstrip"></div>
  <div id="zinfo"></div>
  <div id="zbtns">
    <button data-k="[">[ 設起點=此幀</button>
    <button data-k="]">] 設終點=此幀</button>
    <button data-k="x">x 刪除此 segment</button>
    <button data-k=",">◀ -1</button>
    <button data-k=".">+1 ▶</button>
    <button data-k="close">關閉 (Esc)</button>
  </div>
  <div id="zhint">
    上排逐幀縮圖：<b>單擊</b>看那一幀、<b>雙擊</b>標邊界（第一次雙擊=起點、第二次=終點，成一段；涵蓋/相接舊段自動併）·
    也可雙擊大圖標目前這幀 ·
    <kbd>,</kbd>/<kbd>.</kbd> 前後一幀 · <kbd>Shift</kbd>+ 跳一個 base_stride ·
    <kbd>[</kbd>/<kbd>]</kbd> 移動段內起/終點 · <kbd>x</kbd> 刪除此段 · <kbd>Esc</kbd> 關閉
  </div>
</div>
<div id="ztoast"></div>

<script>
const S = {meta:null, segs:[], task:'all', fpt:0, cols:24, sel:null, pend:null,
           dirty:false, zoomFrame:null};

const el = id => document.getElementById(id);
const grid = el('grid'), canvas = el('canvas');

function frameToSec(f){return (f/S.meta.fps);}
function fmtTime(f){const s=frameToSec(f);const m=Math.floor(s/60);
  return `${m}:${(s%60).toFixed(2).padStart(5,'0')}`;}

// 二分：frame 落在哪個 segment（回傳 index 或 -1）
function segAt(f){
  let lo=0,hi=S.segs.length-1,ans=-1;
  while(lo<=hi){const m=(lo+hi)>>1;
    if(S.segs[m].s<=f){ans=m;lo=m+1;}else hi=m-1;}
  if(ans>=0 && f<=S.segs[ans].e) return ans;
  return -1;
}
// 某顯示縮圖涵蓋的幀範圍 [f0,f1] 是否碰到 segment / 起點 / 終點
function rangeInfo(f0,f1){
  let inseg=false,cin=false,cout=false;
  const i=segAt(f0), j=segAt(f1);
  if(i>=0||j>=0) inseg=true;
  // 起訖點是否落在此範圍
  for(const g of S.segs){
    if(g.s>=f0 && g.s<=f1){inseg=true;cin=true;}
    if(g.e>=f0 && g.e<=f1){inseg=true;cout=true;}
    if(g.s<=f0 && g.e>=f1){inseg=true;}
    if(g.s>f1) break;
  }
  return {inseg,cin,cout};
}

async function boot(){
  S.meta = await (await fetch('/meta')).json();
  S.segs = S.meta.segments.map(x=>({s:x.s,e:x.e}));
  S.cols = S.meta.cols;
  S.fpt = S.meta.base_stride;           // 預設每縮圖 = base_stride（縮圖連續不跳）
  el('mtitle').textContent = `${S.meta.match}  ·  ${S.meta.total_frames} 幀 @ ${S.meta.fps}fps  ·  base_stride=${S.meta.base_stride}`;
  el('fpt').value=S.fpt; el('fpt').min=S.meta.base_stride; el('fpt').step=S.meta.base_stride;
  el('cols').value=S.cols; el('cols').min=4;
  layout();
  grid.addEventListener('scroll', render);
  window.addEventListener('resize', ()=>{layout();});
  el('fpt').onchange=()=>{let v=Math.max(S.meta.base_stride,Math.round(+el('fpt').value/S.meta.base_stride)*S.meta.base_stride);S.fpt=v;el('fpt').value=v;layout();};
  el('cols').onchange=()=>{S.cols=Math.max(4,+el('cols').value|0);el('cols').value=S.cols;layout();};
  document.querySelectorAll('.task').forEach(b=>b.onclick=()=>{
    S.task=b.dataset.task;document.querySelectorAll('.task').forEach(x=>x.classList.remove('on'));
    b.classList.add('on');render();});
  el('save').onclick=save;
  el('reseed').onclick=()=>{if(confirm('丟棄目前編輯，重載偵測種子？')){S.segs=S.meta.seed.map(x=>({s:x.s,e:x.e}));S.sel=null;S.pend=null;markDirty(false);layout();}};
  el('help').onclick=()=>alert(
    '主畫面＝搜尋：捲動縮圖流找鏡頭切換 / 偵測錯漏。\n每個縮圖代表「每縮圖幀數」幀。\n綠頂線=在 segment 內，左綠=起點，右紅=終點。\n\n點任一縮圖 → 逐幀精修（zoom）：\n  , / .  前後一幀（Shift 跳一個 base_stride）\n  [ 起點：在段內→移動該段起點；在 gap→開新段\n  ] 終點：有新段待收→收尾；在段內→移動該段終點\n  x 刪除此幀所在 segment\n\n新段若涵蓋 / 相接舊段，會自動併成一段（不會產生重疊）。\n「看 segment」淡化 gap；「看 gap」淡化 segment。\n完成按「儲存 CSV」→ ans/segments.csv。');
  bindZoom();
  updateStatus();
}

// 顯示縮圖數、每列列高
function dispCount(){return Math.ceil(S.meta.total_frames / S.fpt);}
let rowH=0, colW=0, nRows=0;
function layout(){
  colW = S.meta.thumb_w; rowH = S.meta.thumb_h;
  nRows = Math.ceil(dispCount()/S.cols);
  canvas.innerHTML=''; canvas._th={};      // fpt/cols 變了 → 重建（映射改變）
  canvas.style.width = (S.cols*colW)+'px';
  canvas.style.height = (nRows*rowH)+'px';
  render();
}

function render(){
  const top = grid.scrollTop, vh = grid.clientHeight;
  const first = Math.max(0, Math.floor(top/rowH)-2);
  const last = Math.min(nRows-1, Math.ceil((top+vh)/rowH)+2);
  const D = dispCount();
  const need = {};
  for(let r=first;r<=last;r++){
    for(let c=0;c<S.cols;c++){
      const j = r*S.cols+c;
      if(j>=D) continue;
      need[j] = {r,c};
    }
  }
  // 移除超出範圍的
  for(const j in canvas._th||{}){
    if(!(j in need)){canvas._th[j].remove();delete canvas._th[j];}
  }
  canvas._th = canvas._th||{};
  const step = S.fpt/S.meta.base_stride;            // 每隔幾個抽出縮圖顯示一個
  const sw=S.meta.sheet_w, sh=S.meta.sheet_h;
  for(const j in need){
    const {r,c}=need[j];
    const f0 = j*S.fpt;                              // 此縮圖代表的起始幀
    const f1 = Math.min(S.meta.total_frames-1, f0+S.fpt-1);
    let d = canvas._th[j];
    if(!d){
      d=document.createElement('div');d.className='th';
      d.style.width=colW+'px';d.style.height=rowH+'px';
      d.style.backgroundSize=sw+'px '+sh+'px';
      // sprite 定位
      const e = Math.round(f0/S.meta.base_stride);   // 抽出縮圖 index
      const sheet=Math.floor(e/S.meta.per_sheet), within=e%S.meta.per_sheet;
      const sc=within%S.meta.sprite_cols, sr=Math.floor(within/S.meta.sprite_cols);
      d.style.backgroundImage=`url(/sprite/${sheet}.jpg)`;
      d.style.backgroundPosition=`-${sc*colW}px -${sr*rowH}px`;
      d.title=`幀 ${f0}  ${fmtTime(f0)}`;
      d.onclick=()=>openZoom(f0);
      canvas.appendChild(d);canvas._th[j]=d;
      d._f0=f0;d._f1=f1;
    }
    d.style.left=(c*colW)+'px';d.style.top=(r*rowH)+'px';
    paintThumb(d,f0,f1);
  }
}

function paintThumb(d,f0,f1){
  const {inseg,cin,cout}=rangeInfo(f0,f1);
  d.classList.toggle('inseg', inseg && !cin && !cout);
  d.classList.toggle('cin', cin);
  d.classList.toggle('cout', cout);
  let dim=false;
  if(S.task==='seg' && !inseg) dim=true;
  if(S.task==='gap' && inseg && !cin && !cout) dim=true;
  d.classList.toggle('dim', dim);
  d.classList.toggle('sel', S.sel!=null && segAt(f0)===S.sel);
}
function repaint(){for(const j in canvas._th||{}){const d=canvas._th[j];paintThumb(d,d._f0,d._f1);}}

// ---------- zoom ----------
function openZoom(f){
  S.winN = S.fpt;                           // strip 視窗 = 一個 grid 縮圖所代表的幀數（= fpt）
  S.winStart = Math.max(0, Math.min(S.meta.total_frames-S.winN, f));
  S.zoomFrame=f;
  const i=segAt(f); S.sel = i>=0 ? i : null;
  el('zoom').style.display='flex';
  showFrame(f);
}
function showFrame(f){
  f=Math.max(0,Math.min(S.meta.total_frames-1,f));
  S.zoomFrame=f;
  el('zimg').src=`/frame?n=${f}&w=854`;
  // 超出目前 strip 視窗 → 以 f 為中心重取視窗
  if(f<S.winStart || f>=S.winStart+S.winN){
    S.winStart=Math.max(0,Math.min(S.meta.total_frames-S.winN, f-Math.floor(S.winN/2)));
  }
  renderStrip();
  const i=segAt(f);
  let tag = i>=0 ? `<span class="tag in">segment #${i}  [${S.segs[i].s}–${S.segs[i].e}]</span>`
                 : `<span class="tag gap">gap（不在任何 segment）</span>`;
  if(S.pend!=null) tag += `<span class="tag pend">新段起點=${S.pend}（按 ] 收尾）</span>`;
  el('zinfo').innerHTML=`幀 <b>${f}</b> · ${fmtTime(f)} ${tag}`;
}
// 逐幀縮圖列：把 strip 視窗內每一幀攤成一排，點一下直接跳到那幀
const STRIP_ROWS=3;
function renderStrip(){
  const N=S.winN, start=S.winStart;
  const perRow=Math.ceil(N/STRIP_ROWS);        // 每列幾張 → 攤成 STRIP_ROWS 排
  const asp=S.meta.thumb_h/S.meta.thumb_w;
  const dispW=Math.max(40, Math.floor(Math.min(window.innerWidth*0.96, perRow*190)/perRow));
  const dispH=Math.round(dispW*asp);
  const totalW=N*dispW;
  const bg=`url(/strip?start=${start}&n=${N}&w=160)`;
  const strip=el('zstrip'); strip.innerHTML='';
  strip.style.width=(perRow*(dispW+2))+'px';    // 固定寬度 → 剛好每列 perRow 張、共 STRIP_ROWS 排
  const isBoundary=f=>{let cin=false,cout=false;for(const g of S.segs){if(g.s===f)cin=true;if(g.e===f)cout=true;}return {cin,cout};};
  for(let i=0;i<N;i++){
    const f=start+i;
    const b=isBoundary(f);
    const c=document.createElement('div');
    c.className='cell'+(f===S.zoomFrame?' cur':'')+(b.cin?' cin':'')+(b.cout?' cout':'')
                +(f===S.pend?' pend':'');
    c.style.width=dispW+'px'; c.style.height=dispH+'px';
    c.style.backgroundImage=bg; c.style.backgroundSize=totalW+'px '+dispH+'px';
    c.style.backgroundPosition=`-${i*dispW}px 0`;
    if(f>=S.meta.total_frames) c.style.opacity='.2';
    c.innerHTML=`<b>${f}</b>`;
    c.onclick=()=>showFrame(f);          // 單擊：看那一幀
    c.ondblclick=()=>markBoundary(f);    // 雙擊：標起點 / 終點
    strip.appendChild(c);
  }
}
function bindZoom(){
  el('zoom').querySelectorAll('#zbtns button').forEach(b=>b.onclick=()=>zoomKey(b.dataset.k));
  el('zimg').ondblclick=()=>markBoundary(S.zoomFrame);   // 雙擊大圖 = 標目前這一幀
  el('zimg').ondragstart=e=>e.preventDefault();
  document.addEventListener('keydown',e=>{
    if(el('zoom').style.display!=='flex') return;
    const k=e.key;
    if(k==='Escape'){zoomKey('close');e.preventDefault();return;}
    if([',','.','[',']','x'].includes(k)){
      zoomKey(k, e.shiftKey); e.preventDefault();
    }
  });
}
function zoomKey(k, shift){
  const bs=S.meta.base_stride, f=S.zoomFrame;
  if(k==='close'){el('zoom').style.display='none';return;}
  if(k===','){showFrame(f-(shift?bs:1));return;}
  if(k==='.'){showFrame(f+(shift?bs:1));return;}
  if(k==='['){setStart(f);return;}
  if(k===']'){setEnd(f);return;}
  if(k==='x'){delSeg();return;}
}
// 合併重疊 / 相接（無空檔）的 segment 成聯集，避免重疊造成錯亂
function normalize(){
  S.segs.sort((a,b)=>a.s-b.s);
  const out=[];
  for(const g of S.segs){
    const a=Math.min(g.s,g.e), b=Math.max(g.s,g.e);
    if(out.length && a<=out[out.length-1].e+1){
      out[out.length-1].e=Math.max(out[out.length-1].e,b);
    } else out.push({s:a,e:b});
  }
  S.segs=out;
}
// [ 設起點：在 segment 內 → 移動該段起點；在 gap → 開新段（pending）
function setStart(f){
  const i=segAt(f);
  if(i>=0){ S.segs[i].s=Math.min(f,S.segs[i].e); }
  else { S.pend=f; }
  afterEdit();
}
// ] 設終點：有 pending → 收尾成新段（normalize 會併掉被涵蓋的舊段）；
//            在 segment 內 → 移動該段終點；gap 又沒 pending → 提示
function setEnd(f){
  if(S.pend!=null){
    S.segs.push({s:Math.min(S.pend,f), e:Math.max(S.pend,f)}); S.pend=null;
  } else {
    const i=segAt(f);
    if(i>=0){ S.segs[i].e=Math.max(f,S.segs[i].s); }
    else { flash('先按 [ 標起點，再按 ] 收尾'); return; }
  }
  afterEdit();
}
function delSeg(){
  const i=segAt(S.zoomFrame);
  if(i<0){ flash('此幀不在任何 segment'); return; }
  S.segs.splice(i,1);
  afterEdit();
}
// 雙擊：第一次標起點，第二次標終點並成段（涵蓋/相接舊段會自動併）
function markBoundary(f){
  if(S.pend==null){
    S.pend=f; flash('起點='+f+'，再雙擊終點幀'); showFrame(S.zoomFrame);
  } else {
    S.segs.push({s:Math.min(S.pend,f), e:Math.max(S.pend,f)}); S.pend=null;
    afterEdit();
  }
}
function afterEdit(){
  normalize();
  const i=segAt(S.zoomFrame); S.sel = i>=0 ? i : null;
  markDirty(true); showFrame(S.zoomFrame); repaint(); updateStatus();
}
let _toastT=null;
function flash(msg){
  const t=el('ztoast'); t.textContent=msg; t.style.opacity='1';
  clearTimeout(_toastT); _toastT=setTimeout(()=>t.style.opacity='0',1600);
}

// ---------- save / status ----------
function markDirty(v){S.dirty=v;updateStatus();}
function updateStatus(){
  el('status').innerHTML=`${S.segs.length} 段` +
    (S.meta && S.meta.source==='ans' && !S.dirty ? '（來自 ans/）':'') +
    (S.dirty?' · <span id="unsaved">● 未儲存</span>':' · 已儲存');
}
async function save(){
  const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({segments:S.segs})});
  const j=await r.json();
  if(j.ok){markDirty(false);el('status').innerHTML=`已存 ${j.n} 段 → ans/segments.csv`;}
}
window.addEventListener('beforeunload',e=>{if(S.dirty){e.preventDefault();e.returnValue='';}});
boot();
</script>
</body></html>
"""


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("match", nargs="?", default=None, help="matches/<NAME>（預設第一場可用的）")
    ap.add_argument("--port", type=int, default=8756)
    ap.add_argument("--base-stride", type=int, default=5)
    ap.add_argument("--thumb-width", type=int, default=128)
    ap.add_argument("--cols", type=int, default=24)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    match_path = Path(args.match).resolve() if args.match else pick_default_match()
    if not match_path.is_dir():
        ap.error(f"找不到比賽資料夾：{match_path}")

    video = resolve_cache_video(match_path)
    print(f"[marker] 比賽：{match_path.name}")
    print(f"[marker] cache 影片：{video}")

    thumbs = Thumbs(video, match_path / "cache", args.base_stride, args.thumb_width)
    thumbs.ensure()
    frames = FrameServer(video)
    app = App(match_path, thumbs, frames, args.cols)
    print(f"[marker] 種子來源：{'ans/segments.csv（續標）' if app.seed_source=='ans' else 'segments.json（偵測）'}")

    handler = make_handler(app)
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[marker] 開啟 {url}  （Ctrl-C 結束）")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[marker] bye")


if __name__ == "__main__":
    main()
