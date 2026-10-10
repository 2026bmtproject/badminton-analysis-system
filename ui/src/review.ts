export function hitStatus(status: string | undefined, count: number | null) {
  if (status === "error") return "擊球資料讀取失敗";
  if (status === "stale") return "擊球資料已過期";
  if (count === null) return "未提供擊球資料";
  return count === 0 ? "未偵測到擊球" : `${count} 拍`;
}
