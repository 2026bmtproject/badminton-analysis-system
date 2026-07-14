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
├── audio_highlight/      # 精彩片段偵測 (YAMNet)
├── commentary/           # 賽評生成
├── common/               # 共用工具
│   └── bst/              # BST 球種模型（event_detection 與 stroke_classification 共用）
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
│   ├── match_480p.mp4          # 若原片 > 480p，降解析度快取；多個階段可共用
│   ├── heatmaps/               # shuttle_tracking 的 TrackNet heatmap，一個 segment 一個 .npz
│   │   ├── meta.json           # heatmap 依賴的來源（權重、eval mode、segments…），用於失效判斷
│   │   └── seg0000.npz
│   ├── pose/                   # pose 的逐幀人體偵測（畫面上所有人），一個 segment 一個 .npz
│   │   ├── meta.json           # 同理：模型、來源影片、segments 任一項變了就重建
│   │   └── seg0000.npz
│   └── dense_scan/             # event_detection 的 BST 逐幀 25 類機率，一個 segment 一個 .npz
│       ├── meta.json           # 同理：BST 權重、視窗半寬、球軌跡方法、segments
│       └── seg0000.npz
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

> 註：Python 固定使用 3.12，`uv sync` 會自動挑對版本。

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

# 影格合成工具（單獨使用；把影片抽樣的 frame 合成以凸顯靜態元素如記分板）
uv run python -m modules.common.frame_composite MATCH.mp4 -n 30 -o composites/
```

## 羽球軌跡（shuttle_tracking）

TrackNetV3。權重放在 `models/`（不進 repo，下載連結見本文件開頭）：

```
models/
├── TrackNet_best.pt      # heatmap 網路（seq_len=8, bg_mode=concat）
└── InpaintNet_best.pt    # 軌跡修補網路（seq_len=16）
```

分兩個階段跑：

1. **Heatmap**：每個回合 segment 直接從原片讀 frame（不必先切成 segment mp4），TrackNet 產生逐幀信心
   heatmap，存進 `cache/heatmaps/seg{NNNN}.npz`。這是唯一吃 GPU 的部分，可中斷續跑；cache 完整時
   不會建立網路、不碰 GPU（權重檔仍會被讀取以計算雜湊做失效判斷）。
2. **軌跡**：兩種方法各自從同一批 heatmap 萃取軌跡，**兩份結果都寫進 `shuttle.json`**（以 `method` 欄位區分），
   因為 `event_detection` 兩者都要。它們共用第一步（取最大連通塊當基準點），之後分道揚鑣：

   | method | 作法 | 性格 |
   |---|---|---|
   | `inpaint` | TrackNetV3 原生：每幀取最強的一顆 blob，缺口交給 InpaintNet 修補 | 積極，補得多 |
   | `viterbi` | 低閾值抽出多個候選，用最小成本路徑選出最合理的一條飛行軌跡，再剪枝、補洞 | 保守，寧缺勿濫 |

heatmap 屬於 `cache/` 而非 `stages/`：它是可重建的衍生媒體，只有本模組讀它。存檔前會把低於閾值的雜訊歸零
（所有下游都不讀那個範圍），實測非零像素僅約 0.1%，壓縮率因此高出兩個數量級。`cache/heatmaps/meta.json`
記錄 heatmap 依賴的一切（權重雜湊、eval mode、來源影片、各 segment 起訖），任一項改變即自動重建，
不會讀到對不上的舊資料。

> TrackNet 的 `--eval-mode` 預設 `nonoverlap`：每幀只推論一次。`weight` / `average` 會以滑動窗逐幀重複推論並
> 加權平均，準確度略升但運算量是 `seq_len` 倍。

### GPU、記憶體與其他人的電腦

執行時第一行就會印出實際用到的裝置，**沒吃到 GPU 會大聲警告**，不會讓你跑了 40 分鐘才發現：

```
  device:   cuda (NVIDIA GeForce RTX 3060 Ti, 7.3 of 8.6 GB VRAM free)
  TrackNet: seq_len=8 bg_mode='concat' eval_mode=nonoverlap batch_size=8 (auto)
```

- **GPU**：`pyproject.toml` 讓 Windows / Linux 從 PyTorch 的 CUDA 12.8 index 取 torch（PyPI 的 Windows wheel 只有
  CPU 版），macOS 則走 PyPI。沒有 NVIDIA 顯卡也能安裝，執行時自動退回 CPU 並警告。實測 CPU 約 12 fps
  （GPU 約 90 fps）。
- **VRAM**：`--batch-size` 預設**依剩餘 VRAM 自動決定**（每個 sample 約 0.65 GB）。就算估得太樂觀也不會崩：
  批次遇到 CUDA OOM 會自動對半拆再試，最差退到一次一張。4 GB 的顯卡也能跑完，只是慢一點。
- **主記憶體**：實測峰值 **約 1.0 GB**（本專案最長的回合，1011 幀／40 秒）。最大宗是 frame buffer，由
  `--chunk-frames` 封頂（預設 1200 幀 ≈ 530 MB），更長的回合會分塊推論，所以再長的回合也不會讓它爆掉；
  剩下的輸出 heatmap 陣列仍會隨回合長度線性成長（每幀 147 KB），但斜率只有 frame 的 1/3。記憶體吃緊的
  機器把 `--chunk-frames` 調低即可（例如 600 → 峰值約 0.7 GB）。

## 骨架標記（pose）

RTMPose，**top-down 兩階段**：YOLOX 先找出畫面上所有人，RTMPose 再把每個人裁切放大到 192x256 重估骨架，
所以廣播畫面裡只有 150 px 高的遠場球員，也是以模型的滿解析度在做姿態估計。輸出 COCO-17 關節，
座標已還原回原始畫面像素。兩個模型都是 ONNX，**首次執行由 rtmlib 自動下載並快取**，`models/` 不必放東西。

同樣分兩個階段跑：

1. **偵測**：每個回合 segment 逐幀跑 YOLOX 找出所有人，再對**所有可能是球員的人**（不只最後選中的兩位）
   跑 RTMPose，存進 `cache/pose/seg{NNNN}.npz`。這是唯一吃 GPU 的部分，可中斷續跑。
2. **選人**：用 `court_detection` 的 homography 把每個人投影到球場座標，選出兩名球員，寫進 `pose.json`。

之所以把「所有候選人」都存進 cache，是因為選人邊界是**啟發式、需要對著真實畫面調**；分開存之後，改邊界
重跑只要 **9 秒**，不必再花 20 分鐘的 GPU。`--debug-overlay` 會把選人結果畫在真實畫面上（被選中的畫骨架，
其餘的人畫灰框並標上其球場座標），這是唯一能真正判斷邊界對不對的方法。

> **觀眾不會被 pose。** YOLOX 每幀找到 9–12 個人，若全部都跑 RTMPose，約 **80% 的 GPU 時間會花在幫觀眾
> 畫骨架**。所以在兩個模型之間插一道 bbox 層級的球場預篩（用 bbox 底邊當落地點）：整場從 54 分鐘降到 20 分鐘。
> 預篩的判界**必須嚴格比選人的判界寬**（它用的落地點比較粗糙，而被它丟掉的人就再也沒有骨架可以反悔了），
> 這條不變式由 `cache/pose/meta.json` 裡的 `candidate_margins` 把關：選人邊界一旦超出快取時的預篩範圍，
> 快取會**自動重建**，而不是默默去搜尋一塊根本沒人被 pose 過的區域。

### 為什麼球場要外擴

判界用的是球場外的一圈**帶狀區域**，兩個方向的理由完全不同，而且都是**量出來的、不是猜的**：

| 參數 | 預設 | 方向 | 理由 |
|---|---|---|---|
| `--x-margin` | 0.25（≈ 1.5 m） | 邊線外 | **球員撲救大角度球會整個人衝出邊線**。實測超出邊線中位數 0.84 m、最遠 1.74 m |
| `--y-margin` | 0.25（≈ 3.4 m） | 底線外 | 起跳的球員腳踝騰空，反投影會被推到底線外。實測球場 y 低至 **-0.21** |

`y` 方向的原理值得說清楚：homography 是**地平面**的映射，只對「腳踏在地上」的點成立，而**起跳殺球的
球員不在地上**——腳踝騰空，反投影回地平面時會被推到球員身後更遠處，遠場球員因此會落在**底線之外**。

> **實測告訴我們的事**：把兩個邊界拆開量之後，測試賽事的 592 個漏抓**全部是 x（邊線）方向，y 一個都沒有**。
> 也就是說真正在弄丟球員的是**撲救**，不是跳殺；`y_margin=0.25` 早就夠用，太緊的一直是 `x_margin`。
> 若憑直覺只顧著放寬底線，會完全修錯地方。

### 選人：判界只是入圍，時序連續性才是當選

外擴之後場內的人變多（實測每幀偵測到 9–12 人：司線員、裁判、教練、觀眾），而 1.5 m 的邊線外擴**正好
碰到司線員和裁判坐的位置**。所以入圍之後還要排序，排序有兩個依據：**時序連續性**（`PlayerTracker`）決定
每一幀跟誰，**球場尺寸**（`court_size`）決定第一幀從誰開始。

先講連續性：

- **球員就是上一幀那個球員最近的人。** 實測球員相鄰兩幀只移動中位數 3 px（p99 為 19 px），而誤闖判界的
  司線員離上一幀的球員很遠——連續性是遠比「誰的 bbox 比較大」更強的訊號。
- **這是閘門，不是搜尋。** 上一幀位置附近（`--max-step-px`，預設 120 px／每格先驗年齡）找不到人，就回報
  **找不到**，而**不會**放寬範圍直到抓到某個人為止。因為球員沒被偵測到的那些幀，場上離他最近的「人」
  正是司線員——**一個有信心的錯誤骨架，比一個誠實的空缺糟得多**，下游沒有任何欄位能分辨兩者。
- **過期的先驗等於沒有先驗。** 超過 `prior_max_age`（預設 5 幀）就丟棄，下一幀改用「該半場**球場尺寸**
  最大者」重新起手（見下節），所以真的跟丟的球員會被重新捕獲，而不是永遠拿舊位置去卡。
- 每個回合開始時重置——上一個回合的最後一幀，對下一個回合的第一幀毫無意義。

實測效果：找到球員的比例 **98.3% → 99.4%**，而且原本 6 個「相鄰兩幀跳超過 150 px」的可疑幀（幾乎確定是
選錯人）**降為 0**，最大位移從 248 px 降到 97 px。整個重新選人只花 **9 秒、不碰 GPU**。

> 腳踝信心值太低（被網子擋住、被畫面裁掉）時，腳踝中點是垃圾值，會改用 bbox 底邊中點當落地點：
> 不那麼精確，但至少還在那個人身上。

剩下的 0.6% 漏抓是**偵測器根本沒找到人**，選人邏輯救不了——那要靠更大的 detector 或調 `--person-min-area`。

### 裁判：bbox 大小量的是鏡頭距離，不是量人

連續性再強，也得先有一個對的起點。而**起手用 bbox 面積排序是錯的**——像素大小量的是「離鏡頭多近」，
不是「這個人有多大」。

裁判坐在網邊的**高椅**上，比遠場球員更靠近鏡頭，所以在畫面上就是比較大：實測面積 **22.1k px² 對球員的
19.2k**。而裁判又**不會動**，一旦被誤認成球員，他跟自己上一幀的距離永遠是 0——讓 tracker 好用的連續性，
反而讓它整個回合都忠實地跟著裁判跑。實測 `top` 球員的正確率因此只有 **20.3%**。

修法是把排序的尺換掉：`court_size()` 用 `ground_scale()` 問 homography「這個人腳下的**一公尺值幾個像素**」，
再拿 bbox 高度去除。遠近不同但體型相同的兩個人分數就相同——比較的是人，不是鏡頭。

| 排序依據 | 在兩人同框的 27,620 幀中，把真球員排第一 |
|---|---|
| bbox 像素面積（舊） | **13.6%** |
| 高度 ÷ 球場公尺（`court_size`） | **96.0%** |

> **椅子從偽裝變成破綻。** homography 只映射**地平面**，所以離地 1.5 m 的人會被反投影到**不屬於他的地面點**
> ——裁判被推到網線附近，那裡一公尺值 47 px，而遠場只值 32 px。真的站在那裡的人會顯得很高大，裁判不會：
> 他只有 **3.9**，真球員是 **5.6**。這跟 `y-margin` 那節是同一個物理現象，只是這次它幫我們抓到了兇手。
>
> 同樣的算術**反而會獎勵起跳的球員**——腳踝騰空往反方向投影到底線外，那裡一公尺更「便宜」，所以分數更高。
> 在最關鍵的殺球幀上，排序反而更有把握。

注意這裡**沒有把 `x-margin` 調窄**。調到 0.10 也能把裁判排除在場外（實測同樣有效），但那是拿入圍去解決
排序的問題：邊界是**入圍測試**，寬度是為了容納撲救的球員（上表的理由），擋混進來的裁判線審本來就該是
**排序**的工作。把門關小只會在別的場館裁判椅位置一變就失效。

實測 `top` **20.3% → 99.6%**（對照人工校對的骨架資料），`bottom` 維持 100%，選錯的幀從 22,193 降到 132。

### GPU

`pyproject.toml` 把 `onnxruntime-gpu` **釘在 `<1.23`**，這是這個模組能吃到 GPU 的關鍵：1.23 以上是用
**CUDA 13** 編的，而本專案的 torch 是 **CUDA 12.8** 版，新版 ORT 會找不到 `cublasLt64_13.dll`、建不起
CUDA provider，然後**默默用 CPU 把整個 stage 跑完**（慢約 3 倍，而且看起來一切正常）。釘在 CUDA 12 世代
之後，它直接複用 torch 已經帶在 `torch/lib` 裡的 cuDNN / cuBLAS DLL——**不必額外安裝任何東西，torch 吃得到
GPU 的機器，這裡就吃得到 GPU**。要解除這個 pin，必須連同 torch 的 CUDA 版本一起升。

裝置是自動決定的，且**沒吃到 GPU 會大聲警告**而不是安靜地慢三倍：

```
  device:   cuda
  RTMPose:  balanced + YOLOX person detector
```

沒有 NVIDIA 顯卡的機器會自動退回 CPU 並印出警告，**仍然跑得完**。若你希望「拿不到 GPU 就當作錯誤」
（例如在你自己確定有卡的機器上跑，不想被默默降速），加 `--device cuda` 即可讓它直接失敗。

## 球種模型（common/bst）

BST（Badminton Stroke-type Transformer）**同時被兩個階段用到**，所以它不住在任何一個階段裡，而住在
`modules/common/bst/`：

- **event_detection** 逐幀掃描整個回合，從 25 類機率裡讀出**擊球方**的證據（誰打的、什麼時候打的）；
- **stroke_classification** 拿前者找到的擊球點，從同一組機率裡讀出**球種**。

權重是**合併過的 25 類**：`0=未知球種`、`1–12=Top_<球種>`、`13–24=Bottom_<球種>`。也就是說**一次推論同時
回答「哪一種球」和「誰打的」**——側邊不是另一顆模型的輸出，而是直接編在類別名稱裡。這正是兩個階段能共用同一次
forward 的原因。對使用者呈現時 12 種再併成 8 種（`長球/挑球 → 高遠球`、`發短球/發長球 → 發球`…），artifact 存
中文 8 類。

```
modules/common/bst/
├── classes.py     # 25 類標籤、Top/Bottom 索引、8 類合併、COCO 骨架與骨頭定義
├── model.py       # BST_CG_AP 網路 + 權重載入
├── features.py    # 正規化、切窗、補幀 —— 與訓練完全一致的幾何前處理
├── inference.py   # predict_windows()：批次跑一串窗，回傳 (n_windows, 25) 機率
└── adapter.py     # artifacts（segments/court/pose/shuttle）→ SegmentFeatures
```

用法只有一種形狀：**先把一個回合的幾何抽出來一次，再對它切出想問的窗**。

```python
from modules.common.bst import adapter, centered_windows, load_bst_model, predict_windows

features = adapter.load_segment_features(match_path)[0]      # 一個 segment 的幾何
model = load_bst_model(device="cuda")
half = int(adapter.read_fps(match_path) // 2)                # ±0.5 秒
probs = predict_windows(model, features, centered_windows(len(features), half), device="cuda")
```

`event_detection` 要的是「每一幀一個窗」，`stroke_classification` 要的是「兩拍之間一個窗」——差別只在**窗的清單**，
所以兩者走同一個 `predict_windows`。窗怎麼切、機率怎麼融合成擊球事件（side 融合、鎖定區、dead-time），
是**各階段自己的啟發式**，刻意不放進 common。

權重：`models/bst_CG_JnB_bone_between_2_hits_with_max_limits_seq_100_merged.pt`（不進 repo，下載連結見本文件開頭）。

### 三個坑

- **球場方向**：`court.json` 存的是「球場公尺 → 影像」，BST 要的是反過來、而且正規化到 `[0,1]`（y 從**遠**底線 0
  到**近**底線 1）。方向弄反，模型照樣吐出漂亮的機率，只是每一拍的 Top/Bottom 全部相反。所以這裡直接複用
  `pose.select` 的 `court_from_image` / `to_court`，不另寫第三份球場數學。
- **缺失＝0**：artifact 用 `None` 表示「這一幀沒找到這個球員 / 球不可見」，BST 訓練時讀到的是 0。adapter 負責翻譯，
  所以這裡永遠不會寫出 NaN。（唯一的例外是單一關節缺失，`normalize_joints` 的 center-align 會把它移到 `-centre`——
  這是訓練時就有的行為，原樣保留。）
- **球軌跡有兩份**：`shuttle.json` 對同一批幀存了 `inpaint` 與 `viterbi` 兩條軌跡，BST 只吃一條，預設 `inpaint`
  （最接近訓練時餵的 TrackNetV3 輸出）。要求一條不存在的軌跡會直接報錯，而不是安靜地讀到一整場都靜止的球。

## 擊球偵測（event_detection）

輸出 `events.json`：**一場比賽裡每一次擊球的幀號**，就這樣，沒有別的欄位。球員是誰不寫——BST 的 25 類頭
（`Top_*` / `Bottom_*`）本來就會在 `stroke_classification` 回答，這裡再寫一份只會是個比較弱的第二意見；
segment 也不寫——幀號是絕對的，落在哪個回合是去 `segments.json` 查，不是這個階段有資格宣稱的事。
偵測過程用到的證據（side、補球來源、各項訊號量測）是除錯材料，走 `--debug-csv`，不進契約。

分兩相，跟 `shuttle_tracking` / `pose` 同一個切法、同一個理由：

1. **Dense scan**：對每個回合的**每一幀**跑一次 BST，把 25 類機率存進 `cache/dense_scan/`。唯一吃 GPU 的部分，
   可中斷續跑。
2. **偵測**：四個階段的純幾何 + 門檻，跑在那些機率和兩條球軌跡上。

**存的是完整 25 類機率，不是 argmax。** 這個階段幾乎每個參數都是「機率上的門檻」（side margin、鎖定區信心、
onset 閘），而它們正是要反覆調的東西。存機率＝第一次之後的每一次調參都**完全不用 GPU**。代價也不大：一場 25 fps、
約 3 萬個回合幀 ≈ **2.9 MB**（上游 heatmap 是好幾 GB）。實測 ASG_vs_AA_2020：dense scan 約 25 秒，
之後每次重跑偵測 **4.6 秒、不碰 GPU**。

### 兩條球軌跡

`base`（`inpaint`）是**真正拿來偵測**的那條，峰值、振幅、轉折都讀它，除錯 CSV 也是照它的幀排版；
`aux`（`viterbi`）**只當救援池**——第三階段在「結構上一定漏了球」的位置才去翻它。所以 base 要那條比較密、
比較好看的曲線（inpaint 補滿 96% 的幀），aux 要那條看到了 base 沒看到的東西（viterbi 只有 56%，性格保守）。

### 四個階段：

| 階段 | 做什麼 |
|---|---|
| 1 候選訊號 | 五種訊號（serve-drop / serve-rise / ypeak / accel / ramp），刻意寬鬆 |
| 2 三段閘門 | 振幅閘、同側去重、回合脈絡閘（羽球會輪流打，孤立的候選幾乎都是雜訊） |
| 3 結構補洞 | **唯一加球的地方**：同側交替洞、未認領鎖定區、上游救回、發球補拍 |
| 4 修剪 | **唯一刪球的地方**：回合區間、尾段落地球、計分板死時間 |

前身把加球散在三層七處、刪球散在四處，而且互相交錯——同一個洞誰先搶到算誰的，剛刪掉的球又被後面的規則補回來。
這裡補完才刪，刪完不再補。**每條刪除規則都 fail-open**：找不到自己的錨（例如整段沒有發球證據）就什麼都不做，
而不是憑猜測開始刪。