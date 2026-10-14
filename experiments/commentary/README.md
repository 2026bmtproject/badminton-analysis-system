# Commentary 離線效能與輸入實驗

本目錄比較多段賽評資料讀取的成本，以及減量 generator view 是否保留可引用證據與必要上下文。實驗使用既有分析 artifacts，沒有模型 inference 或 provider 呼叫；prototype 只在實驗程序內作用。

## 執行

在專案根目錄、已準備好的 uv 環境中執行：

```powershell
New-Item -ItemType Directory -Force .local/benchmark-runs | Out-Null
uv run --no-sync python -B -m experiments.commentary.commentary_inputs --match matches/TTYvsASY --selection 1:a 92:b --strategy compare | Out-File -Encoding utf8 .local/benchmark-runs/inputs.json
uv run --no-sync python -B -m experiments.commentary.commentary_payloads --match matches/TTYvsASY --selection 1:a 92:b | Out-File -Encoding utf8 .local/benchmark-runs/payloads.json
```

也支援專案既有的 script 入口：`uv run --no-sync python experiments/commentary/commentary_inputs.py --help` 與 `commentary_payloads.py --help`。選取值 `1:a 92:b` 使用零起算 segment index，冒號後是該段明確確認的 TOP 球員。換比賽時必須提供其自身的映射；court policy 固定為 strict。

兩支 CLI 都輸出 program commit、dirty-source flag、來源 SHA-256、data version、Python／平台與 lockfile hash。原始 run JSON 保存於忽略的 `.local/`，真實比賽輸入仍留在 `matches/`。`results/` 保存已核對的摘要與限制。

## 方法與結果

- [Experiment A：輸入讀取](results/input-performance.md)：baseline／batch-local reuse 的完整輸出 hashes 相等；2026-10-14 單次兩段量測為 106.694 → 53.283 秒，pose scans 2 → 1。第一段仍需完整掃描，並增加 peak working set。
- [Experiment B：generator view](results/payload-view.md)：34,832 → 24,380 UTF-8 JSON bytes（減少 30.01%），目前可引用欄位零遺失，canonical inputs 不變。這不等於 token／LLM latency 節省或語意等價。

歷史結果保留原程式 commit 與其當時的 `benchmarks.*` 重現指令。檔案移動不會改寫歷史量測；以目前入口跑新量測時，CLI 會記錄新版本。從 fresh checkout 執行合成資料測試不需要真實影片，但重現實際量測需要結果文件所列的相同 artifact bytes。

## 驗證

```powershell
uv run --no-sync pytest tests/test_commentary_input_benchmark.py tests/test_commentary_payload_benchmark.py -q
```

測試驗證 reader／完整 envelope tail、source-change detection、copy isolation、輸出 parity、payload context 與 evidence coverage。測試不以耗時作門檻，也不會把 prototype 啟用於 production。
