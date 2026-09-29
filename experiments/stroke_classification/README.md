# 球種分類實驗

對每一拍判斷球種（8 類）與擊球者（上方／下方），和 ShuttleSet 的答案比對。

## 執行

```bash
# 評估所有有真值的比賽（結果寫入 results/）
uv run python experiments/stroke_classification/eval_strokes.py

# 只評指定場次：只印在畫面，預設不覆寫 results/
uv run python experiments/stroke_classification/eval_strokes.py test5 test8

# 用答案的擊球幀重跑一次球種分類（需要 GPU 與既有的管線輸出，14 場約數分鐘）
uv run python experiments/stroke_classification/run_oracle.py
```


管線輸出：`matches/<代號>/stages/stroke_classification/strokes.json`，
另外會讀 `event_detection/events.json` 與 `match_segmentation/segments.json`。

## 真值

和擊球時刻偵測**是同一份**：`experiments/event_detection/ans/`，取自 ShuttleSet。

**球種：18 種併成 8 類。** 先照 BST 訓練時的併法（BST repo 的
`ShuttleSet_merged/class_total.xlsx`），再照系統輸出的 8 類：

| 答案原始球種                             | BST 的 12 類 | 8 類   |
| ---------------------------------------- | ------------ | ------ |
| 放小球、擋小球                           | 原名         | 小球   |
| 殺球、點扣                               | 殺球         | 殺球   |
| 挑球、防守回挑、長球                     | 挑球／長球   | 高遠球 |
| 平球、小平球、後場抽平球、防守回抽、推球 | 平球／推球   | 平快球 |
| 切球、過度切球                           | 切球         | 切球   |
| 發短球、發長球                           | 原名         | 發球   |
| 撲球                                     | 撲球         | 撲球   |
| 勾球                                     | 勾球         | 勾球   |

答案是「未知球種」的拍不計分，系統判成「未知球種」算錯。

**擊球者：A/B 換成上方／下方。** ShuttleSet 每拍記了擊球者與對手在畫面上的 y 座標，
擊球者在對手上面就是上方。少數拍沒有座標，就用同一回合其他拍投票決定 A 站哪一邊。

## BST 看過哪些比賽

BST 的權重是用 ShuttleSet 訓練的，切分寫在 BST repo 的 `ShuttleSet/gen_my_dataset.py`
（第 222–224 行，依 `match.csv` 的 id）。對照我們的 14 場：

| 代號           |   ShuttleSet id | BST 用途 |
| -------------- | --------------: | -------- |
| PC_vs_PVS_2020 |              42 | 測試集   |
| test5          |              10 | 整場排除 |
| test8          |               9 | 整場排除 |
| 其餘 11 場     | 1–8、15、29、34 | 訓練集   |

所以結果分成「BST 沒看過（3 場）」與「BST 看過（11 場）」兩組，**主要數字用沒看過的 3 場**。

BST 排除 test5、test8 的理由是「整場標記錯誤」，但這兩場正是影片開頭多一段、影格整體差
整數分鐘的兩場，補回位移之後，擊球時刻偵測在這兩場的配對正常，因此照常使用。

## 評估

- **評分對象**：擊球時刻偵測在 tol = 5 下配對成功的拍，和 `eval_events.py` 完全相同的配對。
- **球種準確率**與 **Macro-F1**（8 類 F1 平均）。另附「全部猜最多的那一類」當基準。
- **端到端準確率**：分母換成評分範圍內所有可計分的答案拍，漏抓的也算錯。
- **擊球者準確率**：上方／下方判對的比例。
- **用答案擊球幀重跑**：`run_oracle.py` 只把擊球幀換成答案，其他輸入不變。
  拿它把判錯的拍分成「錯在上游」與「錯在 BST」。

完整定義見 `eval_strokes.py` 開頭的說明。

## 結果（tol = 5）

| 範圍               | 計分拍數 | 球種 Acc | Macro-F1 |  基準 | 端到端 Acc | 擊球者 Acc |
| ------------------ | -------: | -------: | -------: | ----: | ---------: | ---------: |
| BST 沒看過（3 場） |     1601 |    87.38 |    84.71 | 33.17 |      78.99 |      97.88 |
| BST 看過（11 場）  |     9333 |    88.32 |    86.36 | 28.53 |      80.06 |      98.02 |
| 合計（14 場）      |    10934 |    88.18 |    86.22 | 27.89 |      79.90 |      98.00 |

擊球幀換成答案後，沒看過的 3 場球種準確率為 88.54%（+1.16）。

## 輸出與延伸資料

- `results/report_tables.md`：完整結果表（含混淆矩陣與判錯原因）
- `results/per_match.csv`：逐場數據
- `results/per_class.csv`：各組、各球種的 P/R/F1
- `results/confusion.csv`：各組的混淆矩陣
- `results/oracle.csv`：用答案擊球幀重跑的每一拍（`run_oracle.py` 產生）
- `results/errors.csv`：每一筆球種判錯的拍，含擊球幀偏差、重跑結果與原因
