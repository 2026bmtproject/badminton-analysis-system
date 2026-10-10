# Match Intelligence Console

這個 Vue／TypeScript 前端把後端既有分析 artifact 轉成可同步操作的影片、片段、擊球與多軌時間軸。前端只呈現後端可保證的資料，不推導勝者、比分轉換、精華政策或不存在的賽評。

## 架構

```text
backend stage JSON / status.json
  → modules.review_export（Python 驗證、跨 artifact join、球場位置）
  → scripts/local-matches.ts（Python 子程序與原子發布）
  → cached/runtime JSON
  → src/data/matchParser.ts（runtime 驗證、明確支援的舊 cache 相容）
  → src/domain/models.ts（唯一前端 domain 與 capabilities）
  → src/data/matchRepository.ts（catalog／已匯入 MatchModel 載入驗證）
  → src/state/useReviewWorkspace.ts（selected state、video clock、唯一跨元件 seek）
  → src/temporal/timeline.ts（共同 viewport 與 time ↔ percent）
  → workspace / player / inspector / timeline presentation components
```

`ReviewPlayer` 是 analysis-aware video player，HTMLVideoElement 是唯一播放時鐘；`useReviewWorkspace` 由它更新 active Rally，並獨立保留使用者 selected Rally／Stroke。Match Bar 與 Watch current context 只代表 active playback；Inspector、全場 Rally locator 與 Timeline selection 代表 selected analysis state。片段、擊球、精華與賽評 marker 都經同一個 `seek()`。

Timeline 的 Full Match／Fit Rally Temporal Lens 共用同一 viewport 與 time ↔ percent 映射。Full Match 保留全場尺度；Fit Rally 顯示局部精確 Stroke 證據。Rally lane 會選取 owning Rally 並 seek 點擊的 exact time；Score、Cheer、Highlight 使用 owning Rally start；Stroke 與賽評證據使用 exact Stroke time。播放器全場 locator 與 Timeline local locator 以相同 Rally index grammar 註冊全場／局部位置。

Capabilities 由 Python review exporter 的 stage state 與實際逐片段資料集中產生。optional artifact 缺失或錯誤只關閉對應軌道／內容，不阻止 segments 可用的比賽載入。`null` 保留為缺資料，`0` 保留為合法觀察值。

賽評支援 `stages/commentary/commentary.json` 的 `commentary-rallies-v1` 全場 artifact，以及沒有全場 `status.json` 的 `stages/commentary/segments/segment_*.json` `commentary-segment-v1` 隨選 artifact。全場資料是基底；同片段的有效隨選結果以整個 Rally 為單位覆寫。前端分開保留全場 stage state 與逐片段可用率，不會因一筆隨選結果宣稱全場完成。事件以 full-match `stroke_index` 精確 join `Stroke.eventIndex` 並保留 backend `time_sec`；摘要沒有時間，不進 Timeline。`source_fact_ids` 原樣保留，只有精確的 `rally:<segment>:stroke:<eventIndex>` 可導向既有 Stroke，其他依據安全顯示為未解析 provenance。Review 只呈現已發布結果；Matches 的任務介面要明確選取 commentary 才會執行賽評。

Python 本機任務服務執行 `modules/**` pipeline；匯入器讀取完成的 artifacts 並更新回看快取。Vue 仍只載入版本化 Review export，不載入 pose/shuttle 大型 JSON 至首屏。

## Windows 桌面版

桌面版沿用同一份 Vue Review、匯入器與 Python 任務服務。正式版由 Electron main 在 loopback 啟動本機 host，直接提供 `dist/`、回看快取、已登錄影片 Range、匯入 API 與 pipeline 代理；不需要啟動 Vite。Electron 不包含 Python、CUDA 或模型，也不會自動安裝它們。

從原始碼啟動桌面版：

```powershell
cd <repository>\ui
npm.cmd ci
npm.cmd run desktop:run
```

產生可直接啟動的 unpacked Windows 版本：

```powershell
cd <repository>\ui
npm.cmd run desktop:unpacked
& '.\desktop-out\BadmintonReview-win32-x64\BadmintonReview.exe'
```

首次開啟時在「桌面分析環境」依序選擇 matches 資料夾、已準備好 `uv sync` 環境的 Python 專案 checkout，以及 `uv.exe`。Python checkout 可以位於安裝目錄以外，須包含 `pyproject.toml`、`modules/local_tasks/service.py` 和 `.venv\Scripts\python.exe`。也可以在首次啟動前設定 `BADMINTON_MATCHES_DIR`、`BADMINTON_BACKEND_DIR`、`UV_BINARY`。桌面版只使用已準備好的 Python 環境；設定有誤會在 Matches 頁顯示原因，不會執行分析。

桌面設定存於 Electron 的 Windows `userData/settings.json`（通常是 `%APPDATA%\badminton-review-ui\settings.json`）。每個 matches 根目錄使用獨立的 `userData/profiles/<hash>/`，其中 `tasks/` 是任務記錄、`reviews/` 是已發布回看 JSON、`videos.json` 是已登錄影片。切換 matches 目錄會切換這個 profile；分析進行中或 worker 狀態未確認時不可切換。正式版不寫入 Electron 程式目錄或 asar。

桌面版會連接設定相容的現有本機 Python 服務，否則使用 argv 啟動自己的服務。關閉視窗時，分析中會提示「留在背景」並保留 main、host 與 Python worker；可從系統匣重新開啟。任務閒置時才會退出並清理本程式啟動的服務；不終止原本已存在的服務。非正常退出後，存活的 worker 由任務 lock 辨識，狀態顯示恢復中並阻擋重複分析；不自動重跑或取消。

開發時仍可按下方「日常操作」啟動 Vite 與 Python 服務，在瀏覽器快速調整 UI。桌面正式版以 `dist/` 為唯一靜態來源。人工驗收應包含原生資料夾選擇、背景系統匣、影片前／中／後段 seek，以及一場實際分析後的 Review 更新。

## 日常操作（Windows PowerShell）

首次安裝：

```powershell
cd <repository>\ui
npm.cmd ci
```

在第一個 PowerShell 視窗啟動分析任務服務（預設只綁定 `127.0.0.1:8765`）：

```powershell
cd <repository>
$env:BADMINTON_MATCHES_DIR='C:\path\to\matches'
uv run python -m modules.local_tasks.service
```

第二個視窗以相同 `BADMINTON_MATCHES_DIR` 啟動 Vue：

```powershell
cd <repository>\ui
$env:BADMINTON_MATCHES_DIR='C:\path\to\matches'
npm.cmd run dev -- --port 5173 --strictPort
```

Matches 頁的「分析比賽」可選真實 stages、預覽 continue／rerun-selected 計畫、開始與查看任務。任務在獨立 Python worker 中執行；關閉瀏覽器不會中止任務。成功後由既有匯入流程更新 Review。若更新失敗，分析任務仍為 succeeded，按「重新整理回看資料」可重試。服務重啟時若 worker lock 仍被持有，任務會顯示恢復中並阻擋新任務；lock 釋放後若無法確認結果，才標為 interrupted，不自動重跑。執行中的任務可在分析面板或任務頁按「取消分析」：服務會終止 worker 及其子程序（含 ffmpeg），任務標為 cancelled；已完成的階段保留，被中止的階段標為 failed，下次會重新執行。

私有任務資料預設在 `<repository>/.local/pipeline-tasks/`，可用 `BADMINTON_TASKS_DIR` 覆寫。`--ui-origin` 預設為 `http://127.0.0.1:5173`；改用其他 Vue 連接埠時需在啟動 Python 服務時設定。Vite 將同源 `/api/pipeline/*` 代理至 loopback 服務。未啟動服務時，已匯入 Review 仍可讀取。

API 範例：

```text
POST /api/pipeline/plan
{"matchId":"Kunlavut","stages":["audio_highlight"],"mode":"continue"}

POST /api/pipeline/tasks
{"matchId":"Kunlavut","stages":["audio_highlight"],"mode":"continue","planId":"<planId>"}
```

plan 回應包含 `planId`、`stages`（run／skip 與原因）、`affectedOutsideScope`、`includesGemini`。來源變動時 start 回傳 409，需重新預覽。`GET /api/pipeline/tasks/<id>` 取得狀態，`GET /api/pipeline/tasks/<id>/logs?offset=0&limit=100` 分頁讀 log。`POST /api/pipeline/tasks/<id>/cancel`（body 為 `{}`）取消本服務啟動的執行中任務；任務已結束回傳 400，屬於其他服務程序的 worker 回傳 409。

啟動後在 Matches 頁按「匯入比賽」，從 `<repository>/matches/` 選擇已完成分析的資料夾。這會沿用命令列匯入器，驗證 stage 指紋與影片，產生前端快取；不重新執行分析。缺少影片、分段結果或完成狀態的資料夾會列出原因，無法點選。

若使用獨立 worktree，而分析資料仍在另一個 checkout，依上方「日常操作」設定 `BADMINTON_MATCHES_DIR` 為該 checkout 的 `matches/` 絕對路徑。命令列匯入也使用同一設定：`npm.cmd run import:match -- <資料夾名稱>`。

開啟 `http://127.0.0.1:5173/matches`。啟動本身不分析、不下載模型、不呼叫外部 API。瀏覽器開發模式使用本機 Vite dev／preview 的匯入與影片路由；Electron 正式模式由同源本機 host 共用底層匯入與影片處理。單獨部署靜態 `dist/` 時需要另接本機服務。已登錄的 `/local-video/<id>` 路由支援 Range，不接受任意本機路徑。

示範資料：

```powershell
npm.cmd run demo
npm.cmd run dev -- --port 5173 --strictPort
```

## 資料與語意

- 真實 catalog：`public/matches/`；影片登錄：`.local/`。兩者為本機生成資料且不進 Git。
- fixtures：`fixtures/`，由 `scripts/generate-data.ts` 產生互動驗證資料。
- Score 只呈現 backend 的 segment 記分板觀察，不以前後片段推導，也不延展到片段外。
- Highlight 是後端 ranking score，不是機率或自動選片。
- Cheer confidence 是訊號值，不宣稱 calibrated probability；`cheer_intensity: null` 不等於 `0`。
- Identity 缺映射時維持畫面上方／下方／未知，不猜測姓名。
- Commentary summary 是無時間的 Rally 內容；只有 production Commentary event 能成為 Timeline marker。

## 目錄

- `scripts/`：真實匯入、受限影片路由，以及 fixture 專用 adapter 與合成示範資料。
- `src/domain/`：canonical frontend model 與 capabilities。
- `src/data/`：catalog／MatchModel repository boundary。
- `src/state/`：review workspace temporal/selection state。
- `src/temporal/`：timeline viewport 與座標映射。
- `src/components/workspace|inspector|timeline/`：純 presentation 與使用者事件。
- `src/styles/`：design tokens 與 workspace、timeline、inspector、responsive styles。
- `tests/`：adapter、資料匯入、playback 與 temporal invariants。

## UI engineering rules

- `src/styles/tokens.css` 是顏色、字級、間距、控制尺寸、圓角、陰影、層級與 motion 的唯一系統來源；元件樣式使用語意 token。
- `AppIcon.vue` 是介面圖示的單一 `currentColor` outline/fill 系統。不要加入 emoji、第二套 icon library 或任意 inline SVG。
- 重要控制至少使用 compact 36px hit area；小型 timeline 視覺 marker 的互動面積與圖形本身分開。
- Match Library 是 overlay navigation，不是常駐 workspace 欄。桌面 Analysis 使用 Player／Inspector 並在下方共用 Timeline；900px 以下 Analysis 順序為 media／inspector／timeline，Watch 順序為 media／timeline／inspector。
- Presentation component 只讀 `MatchModel`。Backend snake_case 只能存在於 `scripts/adapter.ts`，或 `matchParser.ts` 明確支援的舊 cache normalization。
- `active` 表示影片播放位置，`selected` 表示使用者正在查看；不得用播放更新覆蓋 inspection selection。

## 驗證

```powershell
npx.cmd --no-install vue-tsc --noEmit --noUnusedLocals --noUnusedParameters
npm.cmd test
npm.cmd run build
npm.cmd ls --depth=0
```

人工驗證至少涵蓋：真實匯入影片的前／中／後段 Range seek；Rally、Score、Stroke、Cheer、Highlight 的選取與時間定位；active playback 與 selected analysis 狀態分離；缺失或錯誤 optional stage；鍵盤與 IME；以及 375×812、900×800、1280×800、1536×900 下無水平溢出。瀏覽器 console 不應有 error／warning，首屏不得下載 pose 或 shuttle artifact。
