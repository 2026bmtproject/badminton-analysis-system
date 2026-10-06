# 端到端處理時間實驗

量測整條分析管線從原始影片到全部 stage 完成所需的時間。

## 執行

```bash
# 預檢 + 建立測試資料夾，不實際執行（約 1 分鐘）
uv run python experiments/end_to_end/run_e2e.py --dry-run

# 正式執行，5 支影片依短到長逐支跑（約 6 小時）
uv run python experiments/end_to_end/run_e2e.py

# 只跑指定場次
uv run python experiments/end_to_end/run_e2e.py --only test1
```

Windows 也可以直接雙擊 `run_e2e.bat`，參數會原樣轉給 `run_e2e.py`。

影片需先放在 `matches/<代號>/input/`，並以 `experiments/dataset/verify_video.py` 確認與實驗用的版本一致。

## 量測方式

- **冷啟動。** 每場另建 `matches/_e2e/<代號>/`，裡面只有 `input/<影片>`（原檔的硬連結），沒有任何 `cache/` 或 `stages/`。因此每個 stage，包含 GPU 推論，都從頭實際執行；原比賽資料夾的快取不受影響。
- **計時。** 外層以 `perf_counter` 包住 `python -m modules.runner <match>`，得到 E2E 時間；各 stage 時間取自各自的 `status.json`（`finished_at − started_at`）。兩者相差 4–15 秒，即 runner 啟動與影片檢查的開銷。
- **不含 API 的本機時間。** `score_recognition` 呼叫 Gemini，時間主要取決於速率上限而非本機效能，因此另列「E2E − score_recognition」。
- **正規化。** 處理時間主要隨回合總長度增加，與片長關係較弱，因此除了 RTF（real-time factor，E2E 時間 ÷ 片長，小於 1 表示快於即時播放）也列出「÷ 回合總長度」。
- **條件。** 賽評關閉（runner 預設）；一次只跑一支，不與其他 GPU 工作並行；腳本執行期間阻止 Windows 休眠。每支只跑一次，未估計重複量測的變異。
- **預檢。** 開跑前確認 torch 與 onnxruntime 都能使用 CUDA、Gemini 金鑰與模型可用、ffmpeg 與模型權重存在、磁碟空間足夠；任何一項不通過就不執行。

## 環境

Windows 11、NVIDIA RTX 3060 Ti（8 GB）、torch 2.11.0+cu128、onnxruntime-gpu 1.22.0、Python 3.12.5；程式版本為 commit `a9adb48`，執行時工作目錄另有 7 個未提交的檔案。完整紀錄見 `results/env_*.json`。

## 結果

| 比賽            | 編碼 / fps | 片長 (分) |  回合數 | 回合總長 (分) |  E2E (分) | 不含 API (分) |      RTF | 不含 API ÷ 回合總長 |
| --------------- | ---------- | --------: | ------: | ------------: | --------: | ------------: | -------: | ------------------: |
| test1           | VP9 / 25   |       8.2 |      23 |           5.3 |      19.5 |          10.8 |     2.38 |                2.02 |
| test5           | H.264 / 25 |      57.3 |      70 |          15.0 |      38.0 |          26.8 |     0.66 |                1.79 |
| test4           | VP9 / 25   |      71.9 |      78 |          18.3 |      42.5 |          32.9 |     0.59 |                1.80 |
| test2           | H.264 / 25 |     100.9 |     123 |          31.4 |      74.9 |          57.2 |     0.74 |                1.82 |
| TTY_vs_ASY_2023 | AV1 / 30   |      94.0 |     147 |          27.8 |      90.1 |          63.8 |     0.96 |                2.29 |
| **合計**        |            | **332.3** | **441** |      **97.8** | **264.9** |     **191.5** | **0.80** |            **1.96** |

各 stage 佔總時間：shuttle_tracking 34.6%、score_recognition 27.8%、pose 27.6%、match_segmentation 7.0%，其餘 6 個 stage 合計 3.0%。

## 觀察

- **本機計算約為回合總長的 1.8 倍。** 3 支 25 fps 的完整比賽落在 1.79–1.82；TTY 為 30 fps 且是 AV1 編碼（解碼較重），為 2.29。test1 是剪輯過的精華片，回合佔片長 65%（完整比賽約 25–31%），加上固定開銷，因此 RTF 偏高。
- **score_recognition 受速率上限限制。** 程式設定每分鐘 8 次 Gemini 呼叫（`DEFAULT_RPM = 8`），5 場平均每次呼叫 7.3–7.4 秒，正好約等於 60 ÷ 8。
- **shuttle_tracking 的 heatmap 階段約佔該 stage 的 95%**，處理速度 26–31 幀/秒，主要受 CPU 端的解碼與縮圖限制，GPU 未滿載。

## 已知問題

- **TTY 第一次執行失敗。** pose 開始後約 11 秒程式以 `0xC0000409` 結束，屬於原生程式崩潰（onnxruntime／CUDA／解碼器之一），當時沒有保留主控台輸出，原因未查明。刪除該場的測試資料夾後重跑一次即正常完成，表中數字為重跑結果；兩次執行都記錄在 `results/runs.jsonl` 與 `driver.log`。

## 輸出

- `results/summary.md`：總表與各 stage 佔比
- `results/e2e_timing.csv`：一場一列，含各 stage 秒數
- `results/stages_long.csv`：場次 × stage 長表，方便畫圖
- `results/runs.jsonl`：每次執行的完整紀錄（同一場重跑時，報表只取最後一筆）
- `results/env_*.json`：每次啟動時的環境紀錄
- `results/driver.log`：腳本自身的執行紀錄
