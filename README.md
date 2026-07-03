# 羽球比賽分析系統 (Badminton Analysis System)

> 本地端系統：輸入一整場羽球比賽轉播影片，自動切割回合、逐球分析、挑選精彩片段，並產生 AI 賽評播報後合成成精華影片。

## 架構概覽

一條分析管線，逐階段處理，最終在 UI 呈現結果。各模組對應一個處理階段：

```
modules/
├── match_segmentation/   # 回合切割
├── score_recognition/    # 比分辨識 (Gemini API)
├── court_detection/      # 球場邊界辨識
├── pose/                 # 骨架標記 (mmpose)
├── shuttle_tracking/     # 羽球軌跡 (TrackNetV3)
├── event_detection/      # 擊球偵測
├── stroke_classification/# 球種辨識 (BST)
├── audio_highlight/      # 精彩片段偵測 (YAMNet)
├── commentary/           # 賽評生成
├── common/               # 共用工具
├── base.py               # BaseModule 介面 + 階段狀態 (status.json)
├── contracts.py          # 階段間資料契約 + 依賴圖 (single source of truth)
└── runner.py             # 管線 runner：拓樸排序、跳過已完成、失敗即停

ui/        # 分析介面
matches/   # 每場分析的資料（不進 repo）
```

### 每場分析的目錄結構（project path）

一場比賽對應 `matches/{比賽}/`，即該場的 **project path**。原始影片放在 `input/`，
各階段的產出寫進 `stages/{階段名}/`：

```
matches/MK_vs_CT_2019/          # = project_path
├── input/
│   └── match.mp4               # 原始轉播影片（第一個影片檔即為輸入）
├── cache/                      # 共用衍生媒體（可刪可重建，不進 repo）
│   └── match_480p.mp4          # 若原片 > 480p，降解析度快取；多個階段可共用
└── stages/
    └── match_segmentation/
        ├── status.json         # 該階段執行狀態（pending/running/completed/failed）
        └── segments.json       # 該階段的產出（資料契約，見 contracts.py）
```

三種目錄語意分開：`input/` 是原始檔、`stages/{名}/` 是各階段的契約產出（JSON）、
`cache/` 是可重建的衍生媒體（降畫質影片、抽出的音軌…），以參數命名讓任何階段都能
取用並重用（見 `modules.common.downscale.scaled_video`）。

階段間的輸入輸出格式（每個 `stages/*/*.json` 的 schema）與依賴關係都定義在
[`modules/contracts.py`](modules/contracts.py)，模組展開前先把契約鎖好，之後接線較不痛。

## 開始使用

```bash
uv sync
cp config.yaml.example config.yaml   # 填入你的設定
```

> 註：Python 固定使用 3.10（`requires-python = ">=3.10,<3.11"`），numpy 鎖在 <2，`uv sync` 會自動挑對版本。

## 執行管線

把原始影片放到 `matches/{比賽}/input/` 後，用 runner 跑整條管線。runner 會依模組
依賴做拓樸排序、跳過已 `completed` 的階段、任一階段失敗即停：

```bash
# 跑一整場（讀 matches/MK_vs_CT_2019/input/ 的影片，產出寫進 stages/）
uv run python -m modules.runner matches/MK_vs_CT_2019

# 強制重跑（忽略既有的 completed 狀態）
uv run python -m modules.runner matches/MK_vs_CT_2019 --force
```

## 命令列工具（單一工具 / 任意路徑）

各工具亦可單獨執行（於專案根目錄），輸入輸出接受任意路徑，不受 project path 約束：

```bash
# 回合切割：輸出片段 JSON（含 fps 與 segments 陣列）
# 預設會先讀取影片畫質，若高於 480p 會自動先降到 480p 再掃描（fps/幀數不變，
# 片段時間軸仍對應原始影片）；用 --scan-max-height 0 可關閉、改用原畫質掃描。
uv run python -m modules.match_segmentation MATCH.mp4 segments.json

# 手動降解析度（單獨使用；回合切割已內建自動降解析度）
uv run python -m modules.common.downscale MATCH.mp4 --height 480

# 依片段 JSON 剪輯影片：給 project path 即自動找影片與 segments.json
# （video=input/、segments=stages/match_segmentation/segments.json、output=<project>/clips）
uv run python -m modules.common.video_cutter matches/MK_vs_CT_2019            # 預設 separate
uv run python -m modules.common.video_cutter matches/MK_vs_CT_2019 -m merge   # 合併

# 或完全指定路徑（separate / merge / inverse-merge）
uv run python -m modules.common.video_cutter -v MATCH.mp4 -s segments.json -m separate -o ./clips
```

片段 JSON 格式：

```json
{
  "fps": 30.0,
  "segments": [
    {"start_frame": 1, "end_frame": 19, "start_sec": 0.033, "end_sec": 0.633, "duration_sec": 0.6}
  ]
}
```
