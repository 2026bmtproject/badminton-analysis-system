# 實驗與真值資料

本目錄集中管理分析實驗的程式、方法、真值與結果來源。視覺實驗結果位於各模組的 `results/`；音訊研究的原始資料另在研究 repository，這裡保留已核對的來源與重現限制。這裡放的是：

- **人工標記的真值**（各模組的 `ans/`；擊球時刻偵測與球種分類的真值取自公開資料集 ShuttleSet）
- **產生真值的標記程式**
- **算出報告數字的評分程式**
- **評分結果**（`results/`，由評分程式產生）
- **影片對照表**（`dataset/`），讓你下載到和我們相同的影片

## 資料夾結構

```
experiments/
├── dataset/                    視覺實驗共用的影片資訊
│   ├── sources.json            影片來源與開發集／測試集切分
│   ├── videos.json             完整影片指紋（由 build_manifest.py 產生）
│   ├── videos.md               同一份資料的表格版，給人看的
│   ├── verify_video.py         確認你的影片和實驗時用的是同一支
│   ├── video_fingerprint.py    比對影片用的共用邏輯
│   └── build_manifest.py       產生 videos.json / videos.md〔維護者用〕
├── match_segmentation/         賽事影片分段
│   ├── README.md               這個模組驗什麼、怎麼跑、結果是什麼
│   ├── ans/                    真值，一場一個 csv
│   ├── eval_segments.py        評分程式
│   ├── results/                評分結果（由 eval_segments.py 產生）
│   └── label_segments.py       標記程式〔維護者用〕
├── court_detection/            場地偵測
│   ├── README.md               這個模組驗什麼、怎麼跑、結果是什麼
│   ├── ans/                    真值，一場一個 json
│   ├── eval_court.py           評分程式
│   ├── results/                評分結果（由 eval_court.py 產生）
│   └── label_court.py          標記程式〔維護者用〕
├── event_detection/            擊球時刻偵測
│   ├── README.md               這個模組驗什麼、怎麼跑、結果是什麼
│   ├── ans/                    真值，取自 ShuttleSet（非我們標記，見 ans/SOURCE.md）
│   ├── eval_events.py          評分程式
│   └── results/                評分結果（由 eval_events.py 產生）
├── stroke_classification/      球種分類
│   ├── README.md               這個模組驗什麼、怎麼跑、結果是什麼
│   ├── eval_strokes.py         評分程式（真值共用 event_detection/ans/）
│   ├── run_oracle.py           用答案擊球幀重跑球種分類，供對照〔需要 GPU〕
│   └── results/                評分結果（oracle.csv 由 run_oracle.py 產生，其餘由 eval_strokes.py）
├── commentary/                 離線讀取效能與 generator view 實驗
│   ├── README.md               目前入口、設定與驗證
│   ├── commentary_inputs.py    baseline／batch-local reuse 比較
│   ├── commentary_payloads.py  bytes、context 與 evidence coverage 比較
│   └── results/                版本化量測摘要與資料 manifest
├── audio_highlight/            歡呼偵測、相對強度與 segment 聚合
│   └── README.md               研究 JSON／程式來源、hashes 與重現限制
└── end_to_end/                 端到端處理時間
    ├── README.md               量測方式、環境與結果
    ├── run_e2e.py              冷啟動跑完整管線並計時〔需要 GPU，約 6 小時〕
    ├── run_e2e.bat             Windows 雙擊執行 run_e2e.py
    └── results/                計時結果（由 run_e2e.py 產生）
```

其他模組之後會以相同形狀加進來。

標了〔維護者用〕的兩支程式，是當初產生這批資料時用的。**重現報告數字不需要執行它們**，放進來是為了讓`videos.json` 和 `ans/` 的產生方式有跡可循。你要跑的是 `verify_video.py` 和各模組的 `eval_*.py`。

## 環境

Python 3.12，套件由 [uv](https://docs.astral.sh/uv/) 管理。在專案根目錄：

```
uv sync
```

視覺評分程式主要使用 Python 標準函式庫；`dataset/` 的 `verify_video.py` 與 `build_manifest.py` 另外需要 OpenCV 與 `ffprobe`（FFmpeg 的一部分）。Commentary 實驗重用 production readers／facts，需專案的 Python dependencies；音訊研究的環境與資料需求見 [audio_highlight/README.md](audio_highlight/README.md)。

Windows 使用者如果看到中文變成亂碼，先設環境變數：

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

## 一、取得影片

`dataset/videos.md` 列出 16 支影片的 YouTube 連結與各自的屬性。用 yt-dlp 下載
1080p 版本，例如：

```
yt-dlp -f "bv*[height<=1080]+ba/b[height<=1080]" --merge-output-format mp4 <連結>
```

把下載到的檔案放在 `matches/<代號>/input/` 底下，一個資料夾一支影片。

## 二、確認影片正確

**這一步不能跳過。** 本專題的真值全部以影格編號記錄，例如「第 3 個片段是影格 23422 到 23969」。只有當你的影片影格排列方式和標記時完全一致，這些標記才成立。

```
uv run python experiments/dataset/verify_video.py test1
uv run python experiments/dataset/verify_video.py --all
```

程式做三層檢查：檔案雜湊 → 影格數／fps／長度 → 影格指紋（在固定的幾個影格上比對畫面內容）。第三層對「重新壓過的同一支影片」會通過，對其他東西不會：實測把 test1 重壓成 480p、CRF 32（檔案小 17 倍）仍然 12 張指紋全數相符，而不同影片之間的 64 位元指紋距離是 19–38 位元，判定門檻取 6 位元。

解析度和實驗時不同不算失敗，只會提醒——它不影響影格編號，但跑出來的數字可能和報告略有出入。

## 三、跑評分

各模組的跑法見該模組資料夾裡的 `README.md`。
