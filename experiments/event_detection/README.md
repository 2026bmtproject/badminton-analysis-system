# 擊球時刻偵測實驗

在每個比賽片段內找出每一拍的擊球影格，供後續球種分類切窗口用。

## 執行

```bash
# 評估所有有真值的比賽（結果寫入共用的 results/）
uv run python experiments/event_detection/eval_events.py

# 只評指定場次：只印在畫面，預設不覆寫 results/
uv run python experiments/event_detection/eval_events.py test2 test3

# 跑完整管線
uv run python -m modules.runner matches/<代號>
```

真值：`ans/<代號>/setN.csv`；管線輸出：`matches/<代號>/stages/event_detection/events.json`，評分範圍另外會讀 `matches/<代號>/stages/match_segmentation/segments.json`。

## 真值

真值**不是我們標的**，取自 [ShuttleSet](https://github.com/wywyWang/CoachAI-Projects/tree/main/ShuttleSet)（MIT License），原檔未經修改。來源 commit、場次對照與引用格式見 `ans/SOURCE.md`。

16 場中有 14 場在 ShuttleSet 內；`TTY_vs_ASY_2023` 與 `test1` 沒有真值，不參與評分。

答案幀原樣使用，唯一的調整是**影片版本差**：`test5`、`test8` 我們下載的影片開頭比 ShuttleSet 用的版本多了一段，影格編號整體差 +15000（10 分鐘）與 +12000（8 分鐘），在 `eval_events.py` 讀檔時補回，不改動 `ans/` 的檔案。

## 評估

- **評分範圍**：只看「答案的回合（第一拍到最後一拍，前後放寬 tol）」與「系統切出的片段」重疊的時間。範圍外的答案不算漏抓，範圍外的偵測不算誤抓。
- **配對**：偵測與答案相差 ≤ tol 影格即可配對，一對一，從相差最小的開始配。主要數字用 tol = 5，另附 3 與 10。
- **時間誤差**：偵測 − 答案，只算配對成功的拍。

完整定義見 `eval_events.py` 開頭的說明。

## 結果（14 場、12,163 拍，tol = 5）

| 範圍           | Precision | Recall |    F1 |
| -------------- | --------: | -----: | ----: |
| 開發集（6 場） |     92.45 |  89.02 | 90.70 |
| 測試集（8 場） |     95.49 |  90.90 | 93.14 |
| 合計           |     94.19 |  90.10 | 92.10 |

配對 10,959 拍（676 FP、1,204 FN）。時間誤差中位數 −1 影格，MAE 1.66 影格。

## 輸出與延伸資料

- `results/report_tables.md`：完整結果表
- `results/per_match.csv`：逐場數據
- `results/tolerance.csv`：各容差的 P/R/F1
- `results/mismatches.csv`：每一筆多抓與漏掉的拍，含所屬回合、在回合中的位置、離另一邊最近一拍的距離
