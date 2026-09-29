# 場地偵測實驗

從固定俯拍的比賽畫面中找出球場四個外角，並由它們解出單應性，供球員與球體分析換算球場座標。本實驗驗證這四個外角定得準不準。

## 執行

```bash
# 1) 先重跑一次自動偵測，結果寫到 results/detected/
uv run python experiments/court_detection/detect_court.py

# 2) 評分（結果寫入 results/）
uv run python experiments/court_detection/eval_court.py

# 只評指定場次：只印在畫面，預設不覆寫 results/
# 若要用指定場次覆寫 results/，再加 --write
uv run python experiments/court_detection/eval_court.py test1 test6
```

真值：`ans/<代號>.json`；系統偵測結果：`results/detected/<代號>.json`。

## 評估

**真值。** 球場外緣是有寬度的白線。邊線和底線在外角交叉，交疊處是一個小四邊形，稱為「角區」。由單一標記者標出每場四個角區。

**像素誤差。** 每個角量「到角區中心距離」（角區中心即真實角落的點估計）。場地偵測是定位任務，這是主要指標。遠端白線在畫面上只有幾個像素寬、近端較寬，透視尺度差異大，所以遠端（TL、TR）和近端（BL、BR）分開統計。

**命中率。** 偵測角點落在角區內、或在角區外緣 2 px 容差內即為命中。角區僅 5–7 px 寬，容差用來吸收標記緊度與次像素邊緣。純落在線寬內的嚴格命中另記於 `corners.csv` 的 `inside_zone`。

完整定義見 `eval_court.py`。

## 結果（16 場、64 角）

| 範圍           | 全部誤差中位數 | 遠端中位數 | 近端中位數 | 命中率 | 四角全中場數 |
| -------------- | -------------: | ---------: | ---------: | -----: | -----------: |
| 開發集（7 場） |           3.34 |       0.97 |       3.59 |  82.14 |          5/7 |
| 測試集（9 場） |           2.79 |       1.00 |       4.83 |  72.22 |          6/9 |
| 合計           |           3.05 |       1.00 |       3.62 |  76.56 |        11/16 |

角點定位誤差中位數約 3 px（遠端約 1 px），11/16 場四角全部命中。誤差的平均與最大值被兩場完全失敗的比賽（CTC_vs_CL_2019、test5）拉高：這兩場是紅色／非標準色球場，偵測把球網線誤認為底線，近端兩角偏離約 400 px。另 test8 四角約 15–34 px、test6 兩近角約 60 px。詳見 `results/report_tables.md`。

## 輸出

- `results/report_tables.md`：像素誤差、命中率、失敗角點列表
- `results/corners.csv`：每個角一列，含偵測座標、是否命中（`hit`）、嚴格落在線寬內（`inside_zone`）、各項距離
- `results/per_match.csv`：每場一列摘要
- `results/detected/<代號>.json`：`detect_court.py` 重跑的自動偵測結果

## 真值的產生方式

標記工具為 `label_court.py`〔維護者用〕：

```bash
uv run python experiments/court_detection/label_court.py <代號>
```

- **底圖**：取 `experiments/match_segmentation/ans/<代號>.csv` 中最長的 3 段真值片段，每段均勻抽 20 格，取中位數合成，讓球員被濾掉。底圖只依賴人工分段真值，不依賴系統輸出。所用片段記錄在 ans 檔的 `base_image` 欄位。
- **由邊緣擬合出角區**：不直接標角區，而是標球場外框 4 條線各自的外緣、內緣共 8 條邊緣。四個角區各由兩條底線邊緣與兩條邊線邊緣的 4 個交點構成：TL = 遠端底線 ∩ 左邊線，其餘類推。角區即兩條白線塗漆重疊處，寬度自動等於真正線寬。四角共用同一組線擬合，彼此一致。

ans 檔欄位：`corners` 為 `{TL, TR, BR, BL}`，每個角是 4 個 `[x, y]` 頂點（依序繞一圈）。`lines` 為原始的 8 條邊緣點，可由它重算 `corners`。座標皆為原始解析度的像素。
