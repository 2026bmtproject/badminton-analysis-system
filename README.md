# 羽球比賽分析系統 (Badminton Analysis System)

> 本地端系統：輸入一整場羽球比賽轉播影片，自動切割回合、逐球分析、挑選精彩片段，並產生 AI 賽評播報後合成成精華影片。

TrackNetV3 權重下載：https://drive.google.com/file/d/1CfzE87a0f6LhBp0kniSl1-89zaLCZ8cA/view?usp=sharing
BST 權重下載：https://drive.google.com/drive/folders/1D4172WZDJWPvpJdpaHDhy_cA-s8F-zR5?usp=sharing(請下載 bst_CG_JnB_bone_between_2_hits_with_max_limits_seq_100_merged.pt)
下載後解壓縮檔案並把 `TrackNet_best.pt` 與 `InpaintNet_best.pt` 與 `bst_CG_JnB_bone_between_2_hits_with_max_limits_seq_100_merged.pt` 放進 `models/`。

## 架構概覽

一條分析管線，逐階段處理，最終在 UI 呈現結果。各模組對應一個處理階段：

```
modules/
├── match_segmentation/   # 回合切割
├── score_recognition/    # 比分辨識 (Gemini API)
├── court_detection/      # 球場邊界辨識
├── pose/                 # 骨架標記 (RTMPose)
├── shuttle_tracking/     # 羽球軌跡 (TrackNetV3)
├── event_detection/      # 擊球偵測
├── stroke_classification/# 球種辨識 (BST)
├── audio_highlight/      # 音訊歡呼訊號 / audio measurements
├── highlight_ranking/    # 精彩片段排序 / downstream ranking policy
├── commentary/           # 賽評生成
├── common/               # 共用工具
│   └── bst/              # BST 球種模型（event_detection 與 stroke_classification 共用）
├── base.py               # BaseModule 介面 + 階段狀態 (status.json)
├── contracts.py          # 階段間資料契約 + 依賴圖 (single source of truth)
└── runner.py             # 管線 runner：拓樸排序、跳過已完成、失敗即停

ui/        # 分析介面
matches/   # 每場分析的資料（不進 repo）
```

階段間的輸入輸出格式（每個 `stages/*/*.json` 的 schema）與依賴關係都定義在
[`modules/contracts.py`](modules/contracts.py)。

音訊階段是 measurement producer：`audio_highlight` 以 `audio_signals.json`
分別輸出每個 segment 的 `cheer_confidence`、`cheer_intensity` 與
`n_cheer_windows`，這些 measurement 在 stage boundary 保持分離。後續
`highlight_ranking` 才負責決定它們如何與其他比賽訊號形成最終 ranking policy；
目前尚未定義或實作任何組合公式、threshold policy 或 `HighlightScore` producer。

### 每場分析的目錄結構（match 路徑）

一場比賽（match）對應 `matches/{比賽}/`，即該場的 **match 路徑**（程式中的 `match_path`）。原始影片放在 `input/`，
各階段的產出寫進 `stages/{階段名}/`：

```
matches/MK_vs_CT_2019/          # = match_path
├── input/
│   └── match.mp4               # 原始轉播影片（第一個影片檔即為輸入）
├── cache/                      # 可重建的衍生媒體（可刪，不進 repo）
│   ├── match_480p.mp4          # 若原片 > 480p，降解析度快取；多個階段可共用
│   ├── heatmaps/               # shuttle_tracking 的 TrackNet heatmap，一段一個 .npz
│   ├── pose/                   # pose 的逐幀人體偵測（球場附近的候選人），一段一個 .npz
│   ├── dense_scan/             # event_detection 的 BST 逐幀 25 類機率，一段一個 .npz
│   └── scores/                 # score_recognition 讀過的比分，一段一個小 JSON
└── stages/
    └── match_segmentation/
        ├── status.json         # 該階段執行狀態（pending/running/completed/failed）+ 上游輸入指紋
        └── segments.json       # 該階段的產出（資料契約，見 contracts.py）
```

三種目錄語意分開：`input/` 是原始檔、`stages/{名}/` 是各階段的契約產出（JSON）、
`cache/` 是可重建的衍生媒體。

### 快取以內容定址

每個快取檔的檔名是「它是什麼的函數」的雜湊：全域參數（權重、閾值、來源影片）、該段起訖幀、
以及上游資料的指紋。

`stages/{名}/status.json` 另外記下每個上游產出的內容指紋，所以 runner 分得出「跑完了」和
「還是最新的」：上游一改，下游自動判為 stale 並重跑。指紋用內容而非 mtime，重跑上游得到
同樣答案不算變動。


## 命名約定（Glossary）

| 名詞                 | 中文         | 意義                                                  | 程式中的用法                                              |
| -------------------- | ------------ | ----------------------------------------------------- | --------------------------------------------------------- |
| **match**            | 一場比賽     | 一整場羽球比賽（一支轉播影片）                        | `matches/{match}/`、`match_path`（該場路徑）、`match.mp4` |
| **game**             | 一局         | 比賽中的一局                                          | `game_index`                                              |
| **rally**            | 一回合／一分 | 一次得分回合，邏輯計分單位                            | `RallyScore`、`scores.json` 的 `rallies`、`server`        |
| **segment**          | 一個影片片段 | rally 對應的影片切片（起訖 frame），與 rally 一對一   | `Segment`、`segments.json`、`segment_index`               |
| **player**           | 球員         | 場上兩位球員，固定以 `a`/`b` 標示，不隨換邊改變       | `player: "a"/"b"`                                         |
| **score a/b**        | 比分         | 兩邊比分，固定綁 `a`/`b`（跟著 player，不受換邊影響） | `score_a`、`score_b`                                      |
| **stage**            | 階段         | 管線中的一個處理階段                                  | `stages/{stage}/`、`StageSpec`                            |
| **downscaled video** | 低解析度影片 | 為加速掃描／辨識而降解析度的快取影片                  | `downscaled_video()`、`cache/match_480p.mp4`              |

## 開始使用

```bash
uv sync
cp config.yaml.example config.yaml   # 填入你的設定
```

> 註：Python 固定使用 3.12，`uv sync` 會自動挑對版本。

## 執行管線

把原始影片放到 `matches/{比賽}/input/` 後，用 runner 跑整條管線。runner 會依模組
依賴做拓樸排序、跳過已 `completed` 的階段、任一階段失敗即停：

```bash
# 跑一整場（讀 matches/MK_vs_CT_2019/input/ 的影片，產出寫進 stages/）
uv run python -m modules.runner matches/MK_vs_CT_2019

# 強制重跑（忽略既有的 completed 狀態）
uv run python -m modules.runner matches/MK_vs_CT_2019 --force

# 連「status.json 早於輸入指紋機制」的階段也一併重跑（預設會保留不動）
uv run python -m modules.runner matches/MK_vs_CT_2019 --strict-stale
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

# 預設會跳出視窗讓你拖曳四個角點微調（Enter 確認 / ESC 用自動結果 / R 重設）
uv run python -m modules.court_detection matches/MK_vs_CT_2019
# 不做人工確認、直接寫入自動偵測結果（無顯示器環境用；runner 跑管線時一律走此模式）
uv run python -m modules.court_detection matches/MK_vs_CT_2019 --no-confirm

# 羽球軌跡：對每個 segment 跑 TrackNet 產生 heatmap（存 cache），再跑兩種軌跡萃取
uv run python -m modules.shuttle_tracking matches/MK_vs_CT_2019
# 只做 GPU 的 heatmap 推論（之後調整軌跡萃取就不必再動 GPU）
uv run python -m modules.shuttle_tracking matches/MK_vs_CT_2019 --only-heatmap
# 用既有的 heatmap cache 重跑單一 tracker（換補洞方式試效果，不重算 heatmap）
uv run python -m modules.shuttle_tracking matches/MK_vs_CT_2019 --method viterbi --fill kalman

# 骨架標記：對每個 segment 跑人體偵測 + RTMPose（存 cache），再用球場座標選出兩名球員
uv run python -m modules.pose matches/MK_vs_CT_2019
# 只做 GPU 的偵測推論（之後調整選人邊界就不必再動 GPU）
uv run python -m modules.pose matches/MK_vs_CT_2019 --only-detect
# 用既有的 cache 重新選人（放寬底線外擴，不重算骨架）
uv run python -m modules.pose matches/MK_vs_CT_2019 --y-margin 0.35
# GPU 太快、等解碼等到閒置時，加開同時處理的回合數（預設會自己挑，執行時會印出來）
uv run python -m modules.pose matches/MK_vs_CT_2019 --workers 8
# 把選人結果畫在真實畫面上檢查（含起跳中的球員）
uv run python -m modules.pose matches/MK_vs_CT_2019 --debug-overlay overlays/
# 另外匯出 BST 吃的逐 segment 骨架 CSV
uv run python -m modules.pose matches/MK_vs_CT_2019 --csv-dir skeletons/

# 擊球偵測：先跑 BST 逐幀 dense scan（存 cache），再跑四階段偵測
uv run python -m modules.event_detection matches/ASG_vs_AA_2020
# 用既有的 dense scan cache 重跑偵測（調參零 GPU 成本），並輸出 18 欄除錯 CSV
uv run python -m modules.event_detection matches/ASG_vs_AA_2020 --debug-csv hitevents/
# 關掉計分板死時間規則（即使 scores.json 存在）
uv run python -m modules.event_detection matches/ASG_vs_AA_2020 --no-scores

# 球種辨識：對每一拍跑一次 BST（947 拍約 2 秒）
uv run python -m modules.stroke_classification matches/ASG_vs_AA_2020
# 附每拍的除錯 CSV（窗口範圍、前三名、p_top/p_bottom）
uv run python -m modules.stroke_classification matches/ASG_vs_AA_2020 --debug-csv strokes.csv

# 影格合成工具（單獨使用；把影片抽樣的 frame 合成以凸顯靜態元素如記分板）
uv run python -m modules.common.frame_composite MATCH.mp4 -n 30 -o composites/
```

## 各階段簡介

三個吃 GPU 的階段（`shuttle_tracking`、`pose`、`event_detection`）都拆成**兩相**：第一相跑模型、
結果存進 `cache/`，第二相是純幾何與門檻。所以調參只重跑第二相，秒級、不碰 GPU。

### 羽球軌跡（shuttle_tracking）

TrackNetV3。權重放 `models/`：`TrackNet_best.pt`（heatmap 網路）、`InpaintNet_best.pt`（軌跡修補）。

1. **Heatmap**：每個回合逐幀產生信心 heatmap，存進 `cache/heatmaps/`（唯一吃 GPU 的部分，可中斷續跑）。
2. **軌跡**：`inpaint` 與 `viterbi` 兩種萃取法各跑一次，**兩份結果都寫進 `shuttle.json`**（以 `method` 欄位
   區分），因為 `event_detection` 兩者都要——`inpaint` 積極補得多，`viterbi` 保守寧缺勿濫。

### 骨架標記（pose）

RTMPose，top-down 兩階段：YOLOX 先找出畫面上所有人，RTMPose 再逐一裁切放大重估骨架。輸出 COCO-17
關節，座標已還原回原始畫面像素。兩個模型都是 ONNX，**首次執行由 rtmlib 自動下載**，`models/` 不必放東西。

1. **偵測**：逐幀跑 YOLOX，再對**所有可能是球員的人**（不只最後選中的兩位）跑 RTMPose，存進
   `cache/pose/`（唯一吃 GPU 的部分，可中斷續跑）。
2. **選人**：用 `court_detection` 的 homography 把每個人投影到球場座標，選出兩名球員，寫進 `pose.json`。


`--x-margin` / `--y-margin` 把判界擴到球場外一圈：球員撲救會衝出邊線、起跳殺球的腳踝騰空會被反投影到
底線外，判界太緊就會在最關鍵的幀弄丟球員。`--debug-overlay` 把選人結果畫在真實畫面上。

第一相**同時跑多個回合**，每個 worker 各自開一份解碼器：解碼與兩個模型的前處理都是 CPU，序列化跑
的話 GPU 就在旁邊乾等——換好卡卻沒變快就是這個原因。worker 數預設看裝置決定（GPU 取核心數一半、
上限 4；CPU 固定 1），執行時會印出來，用 `--workers` 覆蓋。

### 擊球偵測（event_detection）

輸出 `events.json`：**一場比賽裡每一次擊球的幀號**，就這樣，沒有別的欄位。球員是誰由
`stroke_classification` 回答；幀號是絕對的，落在哪個回合去 `segments.json` 查。偵測過程用到的證據走
`--debug-csv`，不進契約。

1. **Dense scan**：對每個回合的每一幀跑一次 BST，把 25 類機率存進 `cache/dense_scan/`（唯一吃 GPU 的部分）。
2. **偵測**：四階段的純幾何 + 門檻——候選訊號 → 閘門 → 結構補洞 → 修剪。

存的是完整 25 類機率而非 argmax，因為這個階段幾乎每個參數都是「機率上的門檻」。實測
ASG_vs_AA_2020：dense scan 約 25 秒，之後每次重跑偵測 **4.6 秒、不碰 GPU**。

### 球種辨識（stroke_classification）

輸出 `strokes.json`：`events.json` 裡的每一拍對應一筆，順序相同（`event_index` 就是它在 `events.json`
的位置）。每筆記錄球種（8 類）、**擊球者**（`top`/`bottom`）、信心，以及所屬 segment。

單相位、沒有 cache、沒有門檻：BST 對每一拍只跑一次（一整場約 900–1000 個窗，GPU 上 2 秒），argmax
就是答案。

### 球種模型（common/bst）

BST（Badminton Stroke-type Transformer）同時被 `event_detection` 與 `stroke_classification` 用到，
所以住在 `modules/common/bst/` 而不在任何一個階段裡。權重是合併過的 25 類（`0=未知球種`、
`1–12=Top_<球種>`、`13–24=Bottom_<球種>`）。對使用者呈現時再併成 8 類，artifact 存中文 8 類。

權重：`models/bst_CG_JnB_bone_between_2_hits_with_max_limits_seq_100_merged.pt`（不進 repo，下載連結見本文件開頭）。

## GPU 與環境

執行時第一行就會印出實際用到的裝置，**沒吃到 GPU 會大聲警告**。沒有NVIDIA 顯卡的機器會自動退回 
CPU 並印出警告，**仍然跑得完**（TrackNet 實測 CPU 約 12 fps、GPU 約 90 fps）。
若希望「拿不到 GPU 就當作錯誤」，加 `--device cuda` 讓它直接失敗。

- **torch**：`pyproject.toml` 讓 Windows / Linux 從 PyTorch 的 CUDA 12.8 index 取 torch（PyPI 的 Windows
  wheel 只有 CPU 版），macOS 則走 PyPI。
- **onnxruntime-gpu 釘在 `<1.23`**：1.23 以上是用 CUDA 13 編的，跟本專案的 CUDA 12.8 版 torch 對不上，
  會建不起 CUDA provider 然後**默默用 CPU 跑完**（慢約 3 倍且看起來一切正常）。釘住之後它直接複用 torch
  已經帶在 `torch/lib` 裡的 DLL，不必額外安裝任何東西。**要解除這個 pin，必須連同 torch 的 CUDA 版本一起升。**
- **VRAM**：`--batch-size` 預設依剩餘 VRAM 自動決定。遇到 CUDA OOM 會自動對半拆再試，最差退到一次一張，
  4 GB 的顯卡也跑得完。
- **主記憶體**：shuttle_tracking 實測峰值約 1.0 GB（最長的回合，1011 幀）。由 `--chunk-frames` 封頂
  （預設 1200），記憶體吃緊調低即可（例如 600 → 峰值約 0.7 GB）。
