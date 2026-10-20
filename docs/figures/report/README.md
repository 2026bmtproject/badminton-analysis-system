# 報告圖表：CVPR 論文方法圖風格樣圖

目前包含 `match-segmentation`（賽事影片分段）及 `system-architecture`（系統架構）兩張樣圖。各有 `.drawio`、`.svg` 與 `.pdf`。其餘十二張尚未轉換。原報告所有 Mermaid 語法保留，樣圖附在對應區塊下方。

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
