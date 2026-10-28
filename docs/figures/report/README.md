# 報告圖表：CVPR 論文方法圖風格樣圖

報告中的流程圖已全部改為 draw.io 圖，報告不再使用 Mermaid。`match-segmentation`（賽事影片分段）有 `.drawio`、`.svg` 與 `.pdf`。

第三章其餘模組的處理流程圖由 `make_flowcharts.py` 從各圖的座標規格產生 `.drawio` 與 `.svg`（`uv run python docs/figures/report/make_flowcharts.py docs/figures/report [圖名 ...]`，不指定圖名時產生全部）。這些圖目前沒有 PDF，需要時再以 draw.io 匯出。

| 檔名 | 對應章節 |
| --- | --- |
| `score-recognition` | 比分辨識 |
| `court-detection` | 場地偵測 |
| `pose-tracking` | 球員骨架追蹤 |
| `shuttle-tracking` | 球體追蹤與軌跡重建 |
| `event-detection` | 擊球時刻偵測 |
| `stroke-classification` | 球種分類 |
| `audio-cheer` | 歡呼音訊量測 |
| `highlight-ranking` | 精彩片段排序 |
| `identity-mapping` | 球員身分對應 |
| `ui-workflow` | 使用者介面：使用流程 |
| `ui-architecture` | 使用者介面：實作概要 |

流程圖的處理步驟沿用 `system-architecture` 中所屬分組的模組配色。JSON 產物畫成折角文件，快取畫成圓柱，影片與音軌輸入畫成圖示框，選用的輸入與相依以虛線表示。若在 draw.io 中手動修改了某張圖，之後重新執行產生器會覆蓋它，此時請改用圖名只產生其他圖。

`system-architecture` 已重畫為第三章的導覽圖：分組對應第三章各節，只畫直接相依，選用相依以虛線表示。它由 `make_system_architecture.py` 從同一份座標產生 `.drawio` 與 `.svg`（`uv run python docs/figures/report/make_system_architecture.py docs/figures/report`），已直接取代報告中的舊圖；PDF 待以 draw.io 匯出。

`system-overview`（第一章系統概觀）沿用同一套視覺規範，已直接取代原本的 Mermaid 圖；目前只有 `.drawio` 與 `.svg`，PDF 待以 draw.io 匯出。

## 視覺規範

採橫向主流程、白底、低飽和藍／青綠／橘模組配色、虛線分組框與深灰箭頭。這是參考電腦視覺論文常見表達方式的設計，並非 CVPR 官方規範。

分段圖中的影格、曲線、篩選色帶與時間軸都是流程示意，非實測數據。所有文字、框、連線與示意圖形均為原生 draw.io 物件，可個別編輯；沒有點陣圖片、外部圖示或圖示字型依賴。分組框與標籤為獨立物件，移動整組時請多選或先群組。

目前使用 Arial Unicode MS。SVG 保留文字，檢視端需相容的中文字型；PDF 已渲染檢查中文與版面。

## 編輯與匯出

以 draw.io / diagrams.net 開啟 `.drawio`，作為後續編輯來源。修改後透過 File → Export as 匯出同名 SVG 與 PDF，使用白底並裁切至圖形範圍、保留少量邊界。請同步更新三種檔案。

初版 draw.io 與 SVG 來自同一份座標資料，PDF 由 SVG 轉出。已檢查 draw.io XML、節點連線及 PDF 渲染，但尚未在 draw.io GUI 驗證匯出。GUI 匯出後請再次確認中文、文字換行及箭頭位置。

## LaTeX

兩張圖均以跨雙欄的橫向圖為設計方向；正式排版時請檢查實際字級。

```tex
\usepackage{graphicx}
\begin{figure*}[t]
  \centering
  \includegraphics[width=\textwidth]{figures/report/system-architecture.pdf}
  \caption{系統架構}
  \label{fig:system-architecture}
\end{figure*}
```

依 `.tex` 所在目錄調整路徑；單欄文件使用 `figure` 與 `\linewidth`。
