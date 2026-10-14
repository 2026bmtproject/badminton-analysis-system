# 音訊方法與實驗證據對照

本文件提供 [正式報告](report.md) 音訊方法、實驗數字與限制的來源，記錄 2026-10-14 唯讀核對的程式版本與研究結果 snapshot。正式報告的方法描述以 production 程式為準，實驗指標以對應研究 JSON 為準。

## 核對基準

- Production 程式基準：`0e6578a70b831a64dd9887e389031f9136a4531f`。
- Research 程式／凍結文件：[badminton-audio-highlight](https://github.com/2026bmtproject/badminton-audio-highlight) commit `f72eb4c36d2f47a2c187122ebdfe26cfbb63eed2`；唯讀核對時 tracked working tree 乾淨。
- 凍結分類器：`yamnet_mean_lr_v1`，模型 SHA-256 `c5257098315fe51db163039cca95917dd482d1612e1f08b95d301a0dbf8f79f8`。Production 的 [metadata](../models/yamnet_mean_lr_v1/metadata.json) 保存四份訓練 features 的 SHA-256、樣本數、設定與訓練套件版本。
- 研究 `artifacts/` 與 `local_data/` 目前未被 Git 追蹤。Research commit 固定的是程式與文件，**不是以下所有原始資料**；表中的內容雜湊用於辨識本次實際核對的本機 JSON snapshot。需要相同 labels／features／評估設定才能重現，不能聲稱只 clone repository 就能取得全部實驗資料。

## 數字來源

以下路徑以研究 repository 為根目錄；不保存個人絕對路徑、不複製原始分析 artifact。

| 來源 | Research artifact | 內容 SHA-256 |
| --- | --- | --- |
| LOMO | `artifacts/cross_match/evaluation/cross_match_metrics.json` | `9fbec871aede4ddf13cb5ad97bd4f3372cb2c3f562949398b4c6f1be02113055` |
| Calibration | `artifacts/cross_match/evaluation/calibration/calibration_metrics.json` | `3fb6cfef805a126a3faa5a4124374e8679e6201bfbc08a12dfd51d468a643049` |
| External | `artifacts/match_005/external_validation/metrics.json` | `7a408ed3ee24ca719bd136e78143c1726cad1d3f69a7a1e520af5840e3d700ab` |
| Baselines | `artifacts/baseline_comparison/zero_shot_v1/metrics.json` | `fc27fe7c39275bc5bd397f3caa648c8bf9c673d2cd3b235943c3edec5f4d712d` |
| Intensity | `artifacts/match_005/intensity_pairwise/evaluation.json` | `9a335b72f603b7289c59ab34df0ff128c34364c3ca4b92c6ef4a8a9070bc68f1` |
| Detection aggregation | `artifacts/match_005/segment_human_validation/segment_detection_metrics.json` | `5135be5dabd42c10b7284da2ddc56884b5331ca8981b5f5c0b5afff201a72f55` |
| Peak aggregation | `artifacts/match_005/segment_human_validation/segment_intensity_evaluation.json` | `acbda2c36bde669e151b6e76c288286432e1ec017460f6703a35691ecde64d66` |
| Sensitivity | `artifacts/match_005/segment_aggregation_study/summary.json` | `f3217c64cf5496f0d648eef37eb0de8956cc5e4e55beaf7af4a5e328db0ced49` |

LOMO／external 表格直接依各 JSON 的 accuracy、precision、recall、f1、roc_auc、average_precision 欄位核對；macro 是四場指標的算術平均，不是 pooled metric。混淆矩陣順序為 `[[TN,FP],[FN,TP]]`。正式部署的 400-window fit 與各 fold 的 300-window training 是不同用途，沒有用 training-set accuracy 代替 held-out performance。

Intensity 的 strict concordance 排除人工平手，並以特徵非平手的明確配對為分母；coverage 說明此分母占人工明確配對的比例，因此 `temporal_occupancy` 的 0.457 對應 16/35，不是 16/39。Segment peak-pair concordance 則用 45 組明確結果，max／p95 為 32/45，top-2／top-3 為 33/45。

Sensitivity 表使用 `leave_one_out_summary.detection` 的 `median_max_abs_delta` 與 `q95_max_abs_delta`；這是移除單一窗口對 detector aggregation 的影響，不是 intensity 或分類準確率。較早 summary 的 `production_method=not_selected`、人工 metrics 的 `selected_aggregator=null` 與較晚凍結文件並非同一時間點。最終 p95 的選擇以研究 `docs/experiments/segment_aggregation_v1.md` 第 7 節及 production 實作為準，不據較早診斷 artifact 宣稱統計勝出。

## 實作對照

| 報告語意 | Production 來源 |
|---|---|
| 3 秒完整窗口、1 秒 hop、3 秒後置延伸、絕對時間 | [windows.py](../modules/audio_highlight/windows.py) |
| 16 kHz mono 正規化 | [audio.py](../modules/audio_highlight/audio.py) |
| 凍結 YAMNet 與 mean pooling | [yamnet.py](../modules/audio_highlight/yamnet.py) |
| Scaler、LR、固定 0.5 gate | [detector.py](../modules/audio_highlight/detector.py)、[metadata](../models/yamnet_mean_lr_v1/metadata.json) |
| log RMS、同場 average rank、null／single-positive 語意 | [intensity.py](../modules/audio_highlight/intensity.py) |
| 全窗口 confidence p95、非空值 intensity p95 | [aggregation.py](../modules/audio_highlight/aggregation.py) |
| 0.5／0.5 heuristic 與排序 tie-break | [policy.py](../modules/highlight_ranking/policy.py) |
| Window center、缺 windows 相容 | [exporter.py](../modules/review_export/exporter.py) |
| 三窗口顯示平滑、共同時間映射與 gap | [cheerCurve.ts](../ui/src/temporal/cheerCurve.ts) |
| Commentary 選用 highlight 背景 | [upstream.py](../modules/commentary/adapters/upstream.py)、[planner.py](../modules/commentary/generation/planner.py) |

## 重現與核對

在已備妥的 production Python 環境執行下列既有離線測試，可驗證窗口、intensity、p95、ranking 與契約語意；不需神經模型 inference 或 Gemini：

```powershell
uv run --no-sync pytest tests/test_audio_highlight.py tests/test_audio_highlight_audio.py tests/test_audio_highlight_contracts.py tests/test_highlight_ranking.py -q
```

研究指標必須另備原始 labels／features；研究 CLI 的 `evaluate` 會對指定 feature datasets 重新 fitting LOMO 的 scaler 與 LR，不會從影片重新提取 YAMNet features。四份 frozen feature hashes 見 production metadata，外部 `match_005` feature SHA-256 為 `489f46fdbd708b17bfc85fed596ce333200dc80c0050b47a9541d3e0b08b285c`。範例在研究 repository 執行，需提供完整四場資料：

```powershell
uv run audio-highlight evaluate artifacts/match_001/features/features.npz artifacts/match_002/features/features.npz artifacts/match_003/features/features.npz artifacts/match_004/features/features.npz --output-dir artifacts/reproduced_evaluation
Get-FileHash artifacts/cross_match/evaluation/cross_match_metrics.json -Algorithm SHA256
```

本次只讀取既有 JSON 與凍結文件、核對數字和執行 production 離線測試，沒有重新訓練、模型 inference、下載模型、改寫標註或呼叫外部分析 API。

## 尚需研究者補齊

- `match_001`–`match_004` 的真實賽事名稱、影片來源與分割角色；不得假定它們對應 production 16 支影片的同一分割。
- 完整 cheer／no-cheer／ambiguous 規範：掌聲、單人喊聲、播報、音樂與混合聲音的邊界，以及部分窗口歡呼的判定方式。
- 多標註者一致性；現有單一主要標註者／配對設計不足以估計 inter-rater agreement。
- labels／features／metrics 的可重現封存。研究程式 commit 與 snapshot hashes 不能替代資料發布。
- 更多 untouched external matches、highlight 排序真值與固定 0.5／0.5 權重消融。

文獻補充使用 [YAMNet 官方文件](https://github.com/tensorflow/models/tree/master/research/audioset/yamnet) 與 [AudioSet 原始研究](https://research.google/pubs/audio-set-an-ontology-and-human-labeled-dataset-for-audio-events/)。YAMNet 的預訓練能力不直接等於本系統歡呼任務的表現。
