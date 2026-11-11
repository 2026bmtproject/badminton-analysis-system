# 報告流程圖

第一章與第三章的流程圖，報告本體在 `docs/latex/`。每張圖有 `.drawio`（可編輯原檔）、`.svg` 與 `.pdf`（LaTeX 使用）三種檔案。

## 字級一致的規則

LaTeX 以同一比例 `\flowscale` 放入所有流程圖，不指定寬度。SVG 裡的 1 px 在每張圖都印成同樣大小，所以：

- **最大寬度 1300 px**：1300 px 寬的圖剛好等於版心寬（17 cm）。
- **字級**：框內文字 23 px，約印成 8.5 pt；最小 20 px，約 7.4 pt。
- 畫得比 1300 px 寬的圖，由 `fit_width.py` 等比縮小座標、框與路徑，**字級不變**；小於 20 px 的字一併放大到 20 px。

## 重新產生

```bash
uv run python docs/figures/report/make_flowcharts.py docs/figures/report      # 第三章各模組流程圖
uv run python docs/figures/report/make_system_architecture.py docs/figures/report
cd docs/figures/report && uv run python fit_width.py *.svg                    # 套用寬度與字級規則（可重複執行）
bash docs/figures/report/svg2pdf.sh                                           # SVG → PDF（headless Edge）
```

`system-overview` 與 `match-segmentation` 沒有產生程式，直接以 draw.io 編輯。改完後匯出同名 SVG，再執行上面最後兩行。

## 視覺規範

- **版面**：橫向主流程、白底、虛線分組框、深灰箭頭。
- **配色**：低飽和的藍、青綠、橘模組配色。框的底色對應第三章各節，與 `system-architecture` 的分組一致。
- **元素**：JSON 產物畫成摺角便條，快取畫成圓柱，選用的輸入與相依畫成虛線。
- **示意性質**：分段圖中的影格、曲線與色帶只是流程示意，不是實測數據。

SVG 指定的字型是 Arial Unicode MS。這台電腦沒有安裝，`svg2pdf.sh` 轉 PDF 時會改用 Noto Sans TC。
