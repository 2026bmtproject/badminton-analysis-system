# 賽事影片分段實驗

從轉播影片中切出固定俯拍的比賽片段，供後續場地、球員、球路與擊球分析使用。

## 執行

```bash
# 評估所有有真值的比賽（結果寫入共用的 results/）
uv run python experiments/match_segmentation/eval_segments.py

# 只評指定場次：只印在畫面，預設不覆寫 results/，避免用部分結果蓋掉全體報表
# 若確實要用指定場次覆寫 results/，再加 --write
uv run python experiments/match_segmentation/eval_segments.py test1 test6

# 跑完整管線
uv run python -m modules.runner matches/<代號>

# 直接分段單支影片
uv run python -m modules.match_segmentation <影片路徑> <輸出.json>
```

真值：`ans/<代號>.csv`；管線輸出：`matches/<代號>/stages/match_segmentation/segments.json`。

## 評估

真值與輸出段採一對一 IoU 配對，IoU ≥ 0.9 才算成功。報告包含 matched、FP、FN、Precision、Recall、F1，以及配對段的起訖邊界誤差與容差命中率。完整定義見`eval_segments.py`。

## 結果（16 場、1,354 段）

| 範圍           | Precision | Recall |    F1 |
| -------------- | --------: | -----: | ----: |
| 開發集（7 場） |     99.13 |  99.56 | 99.34 |
| 測試集（9 場） |     98.37 |  98.96 | 98.66 |
| 合計           |     98.75 |  99.26 | 99.01 |

共配對 1,344 段（17 FP、10 FN）；11 場零錯誤。起點／終點誤差中位數皆為 0 影格，且所有配對段的邊界誤差都在 10 影格內。

## 輸出與延伸資料

- `results/report_tables.md`：完整結果表
- `results/per_match.csv`：逐場數據
- `results/boundary_errors.csv`、`results/mismatches.csv`：邊界誤差與失配明細

標記工具為 `label_segments.py`；真值欄位為`start_frame, end_frame, start_sec, end_sec, duration_sec`（起訖影格皆含）。
