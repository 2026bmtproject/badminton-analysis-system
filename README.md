# 羽球比賽分析系統 (Badminton Analysis System)

> 本地端系統：輸入一整場羽球比賽轉播影片，自動切割回合、逐球分析、挑選精彩片段，並產生 AI 賽評播報後合成成精華影片。

## 架構概覽

一條分析管線，逐階段處理，最終在 UI 呈現結果。各模組對應一個處理階段：

```
modules/
├── match_segmentation/   # 回合切割
├── score_recognition/    # 比分辨識 (Gemini API)
├── court_detection/      # 球場邊界辨識
├── pose/                 # 骨架標記 (mmpose)
├── shuttle_tracking/     # 羽球軌跡 (TrackNetV3)
├── event_detection/      # 擊球偵測
├── stroke_classification/# 球種辨識 (BST)
├── audio_highlight/      # 精彩片段偵測 (YAMNet)
├── commentary/           # 賽評生成
└── common/               # 共用工具

ui/         # 分析介面
matches/   # 每場分析的產出資料（不進 repo）
```

## 開始使用

```bash
uv sync
cp config.yaml.example config.yaml   # 填入你的設定
```
