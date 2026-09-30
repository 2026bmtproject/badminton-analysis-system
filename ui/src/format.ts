export function formatTime(value: number) {
  const s = Math.max(0, Math.floor(value));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function formatPreciseTimeParts(value: number) {
  const clamped = Number.isFinite(value) ? Math.max(0, value) : 0;
  const roundingGuard = Number.EPSILON * Math.max(1, Math.abs(clamped)) * 4;
  const totalCentiseconds = Math.round((clamped + roundingGuard) * 100);
  const minutes = Math.floor(totalCentiseconds / 6_000);
  const seconds = Math.floor((totalCentiseconds % 6_000) / 100);
  const hundredths = totalCentiseconds % 100;
  const whole = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  const fraction = String(hundredths).padStart(2, "0");
  return { whole, fraction, text: `${whole}.${fraction}` };
}

export function formatPreciseTime(value: number) {
  return formatPreciseTimeParts(value).text;
}

export function formatTimelineAxisTime(value: number, duration: number) {
  return duration <= 10 ? formatPreciseTime(value) : formatTime(value);
}

export function scoreText(score: readonly number[] | null) {
  return score ? `${score[0]}:${score[1]}` : "未提供";
}

export function playerName(value: string, fallback = "選手") {
  return value.replaceAll("（記分板列）", "").trim() || fallback;
}
