const labels: Record<string, string> = {
  match_segmentation: "回合切割", audio_highlight: "音訊歡呼訊號",
  highlight_ranking: "精彩片段排序", score_recognition: "比分辨識",
  court_detection: "球場邊界辨識", shuttle_tracking: "羽球軌跡",
  pose: "骨架標記", event_detection: "擊球偵測",
  stroke_classification: "球種辨識", player_identity: "球員身分對應",
  commentary: "賽評生成",
};
export function stageLabel(name: string) { return labels[name] ?? name; }
