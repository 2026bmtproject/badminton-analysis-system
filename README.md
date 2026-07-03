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

### 每場分析的目錄結構（match 路徑）

一場比賽（match）對應 `matches/{比賽}/`，即該場的 **match 路徑**（程式中的 `match_path`）。原始影片放在 `input/`，
各階段的產出寫進 `stages/{階段名}/`：

```
matches/MK_vs_CT_2019/          # = match_path
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
取用並重用。

階段間的輸入輸出格式（每個 `stages/*/*.json` 的 schema）與依賴關係都定義在
[`modules/contracts.py`](modules/contracts.py)，模組展開前先把契約鎖好。

## 命名約定（Glossary）

| 名詞 | 中文 | 意義 | 程式中的用法 |
|---|---|---|---|
| **match** | 一場比賽 | 一整場羽球比賽（一支轉播影片） | `matches/{match}/`、`match_path`（該場路徑）、`match.mp4` |
| **game** | 一局 | 比賽中的一局 | `game_index` |
| **rally** | 一回合／一分 | 一次得分回合，邏輯計分單位 | `RallyScore`、`scores.json` 的 `rallies`、`server` |
| **segment** | 一個影片片段 | rally 對應的影片切片（起訖 frame），與 rally 一對一 | `Segment`、`segments.json`、`segment_index` |
| **player** | 球員 | 場上兩位球員，固定以 `a`/`b` 標示，不隨換邊改變 | `player: "a"/"b"` |
| **score a/b** | 比分 | 兩邊比分，固定綁 `a`/`b`（跟著 player，不受換邊影響） | `score_a`、`score_b` |
| **stage** | 階段 | 管線中的一個處理階段 | `stages/{stage}/`、`StageSpec` |
| **downscaled video** | 低解析度影片 | 為加速掃描／辨識而降解析度的快取影片 | `downscaled_video()`、`cache/match_480p.mp4` |

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

各工具亦可單獨執行（於專案根目錄），輸入輸出接受任意路徑，不受 match 路徑 約束：

```bash
# 回合切割：輸出片段 JSON（含 fps 與 segments 陣列）
# 預設會先讀取影片畫質，若高於 480p 會自動先降到 480p 再掃描；用 --scan-max-height 0 可關閉、改用原畫質掃描。
uv run python -m modules.match_segmentation MATCH.mp4 segments.json

# 手動降解析度（單獨使用；回合切割已內建自動降解析度）
uv run python -m modules.common.downscale MATCH.mp4 --height 480

# 依片段 JSON 剪輯影片：給 match 路徑 即自動找影片與 segments.json
uv run python -m modules.common.video_cutter matches/MK_vs_CT_2019            # 預設 separate
uv run python -m modules.common.video_cutter matches/MK_vs_CT_2019 -m merge   # 合併

# 或完全指定路徑（separate / merge / inverse-merge）
uv run python -m modules.common.video_cutter -v MATCH.mp4 -s segments.json -m separate -o ./segments

# 比分辨識：對每個 segment 直接從原片抽 frame、合成後送 Gemini 讀比分
# 需先設定 GEMINI_API_KEY 環境變數
uv run python -m modules.score_recognition matches/MK_vs_CT_2019

# 影格合成工具（單獨使用；把影片抽樣的 frame 合成以凸顯靜態元素如記分板）
uv run python -m modules.common.frame_composite MATCH.mp4 -n 30 -o composites/
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
