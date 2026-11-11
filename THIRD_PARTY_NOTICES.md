# 第三方授權聲明（Third-Party Notices）

本專案的原創程式碼以 [MIT License](LICENSE) 授權。本文件列出專案中包含或使用的第三方程式碼、資料、模型與素材，以及各自適用的授權條款。第三方內容的權利屬於其原作者，**不因收錄於本專案而改適用本專案的授權**。

## 1. 隨本專案散布的第三方程式碼與資料

以下內容直接收錄在本儲存庫中，依其授權要求附上完整的著作權與授權聲明。

| 元件 | 收錄位置 | 授權 | 來源 |
| --- | --- | --- | --- |
| TrackNetV3 | `modules/shuttle_tracking/tracknet.py`、`modules/shuttle_tracking/track_inpaint.py` 中的網路架構，以及 `inference.py` 的部分推論流程 | MIT | <https://github.com/qaz812345/TrackNetV3> |
| BST | `modules/common/bst/model.py` 的網路架構，以及 `features.py` 中與權重綁定的特徵處理 | MIT | <https://github.com/Va6lue/BST-Badminton-Stroke-type-Transformer> |
| ShuttleSet | `experiments/event_detection/ans/` 中的擊球真值（原檔未經修改） | MIT | <https://github.com/wywyWang/CoachAI-Projects/tree/main/ShuttleSet> |

上述程式碼為配合預訓練權重而移植，網路結構與層名稱維持與原始實作一致，周邊的資料讀取與流程已依本專案的管線改寫。

### TrackNetV3

```text
MIT License

Copyright (c) 2024 qaz812345

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software, pretrained model checkpoints, and associated documentation
files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom
the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### BST（Badminton Stroke-type Transformer）

```text
MIT License

Copyright (c) 2025 Jing-Yuan Chang

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### ShuttleSet

完整授權條文隨資料附在 [`experiments/event_detection/ans/LICENSE`](experiments/event_detection/ans/LICENSE)（Copyright (c) 2022 Wei-Yao Wang，MIT License），來源 commit 與引用格式見同資料夾的 [`SOURCE.md`](experiments/event_detection/ans/SOURCE.md)。

## 2. 執行時使用、但不隨本專案散布的模型與套件

以下內容由使用者另行下載，或在首次執行時自動下載，本儲存庫不包含其檔案。

| 元件 | 用途 | 授權 | 取得方式 |
| --- | --- | --- | --- |
| TrackNetV3 預訓練權重（`TrackNet_best.pt`、`InpaintNet_best.pt`） | 球體追蹤 | MIT（同上，條文明確涵蓋預訓練權重） | 使用者手動下載 |
| BST 預訓練權重 | 擊球時刻偵測、球種分類 | 依 BST 原始專案的條款 | 使用者手動下載 |
| RTMPose、YOLOX ONNX 模型 | 球員骨架追蹤 | Apache-2.0 | 由 [rtmlib](https://github.com/Tau-J/rtmlib)（Apache-2.0）自動下載 |
| YAMNet | 歡呼音訊量測的音訊特徵 | Apache-2.0 | 由 TensorFlow Hub 自動下載 |
| Python 套件 | 見 `pyproject.toml` 與 `uv.lock` | 各套件自身的授權 | `uv sync` 安裝 |
| JavaScript 套件 | 見 `ui/package.json` 與 `ui/package-lock.json` | 各套件自身的授權 | `npm ci` 安裝 |

`models/yamnet_mean_lr_v1/` 中的歡呼分類器為本專案自行訓練的參數，只包含建立在 YAMNet 特徵之上的線性分類器，不含 YAMNet 本身的權重。

比分辨識與賽評生成會呼叫 Google Gemini API，使用時須遵守 Google 的服務條款。

## 3. 散布桌面版時的額外義務

本儲存庫只包含原始碼。若自行打包並散布 Windows 桌面版（`npm run desktop:unpacked`），打包結果會一併包含以下元件，散布者須附上其授權條文，並依各授權履行義務：

| 元件 | 授權 |
| --- | --- |
| Electron | MIT |
| FFmpeg 執行檔（`@ffmpeg-installer/ffmpeg`） | 套件為 LGPL-2.1，Windows 版執行檔為 GPL-3.0 |
| FFprobe 執行檔（`ffprobe-static`） | 套件為 MIT，執行檔依 FFmpeg 的建置採 GPL 或 LGPL |
| IBM Plex Mono 字型（`@fontsource/ibm-plex-mono`） | SIL Open Font License 1.1 |

GPL 元件要求散布者同時提供對應的原始碼或取得方式。

## 4. 不在本專案授權範圍內的內容

以下內容雖然收錄在本儲存庫中，但本專案**沒有權利以 MIT License 再授權**，不適用 [LICENSE](LICENSE)：

- **轉播畫面**：`docs/report_pic/`、`docs/latex/report.pdf` 與 `README.md` 中的截圖含有比賽轉播影格，其著作權屬於 BWF 與各轉播單位。這些畫面僅為說明研究方法與結果而引用。
- **介面彩蛋素材**：`ui/src/assets/fun/` 中的圖片與音效（Nyan Cat、OIIA 貓、鋼管落地音效）取自網路，權利屬於各原作者，僅供個人開發使用。
- **比賽影片**：本儲存庫不含任何比賽影片。`experiments/dataset/` 只記錄公開影片的網址與指紋，影片的著作權屬於原權利人，請自行從原始來源取得。
