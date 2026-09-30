// Metres from modules.common.court_geometry. The rectangle [0,1]² is the
// marked court, not a promise that a player's feet stay within the lines.
export const COURT_WIDTH_M = 6.1;
export const COURT_LENGTH_M = 13.41;

// On the audited video the visible far baseline and automatic calibration
// differ by up to ~15 px (~0.7 m there). A 0.8 m adjacent band covers that
// uncertainty; farther airborne projections were visibly not ground points.
export const COURT_ADJACENT_MARGIN_M = 0.8;

export function distanceOutsideCourtM(x: number, y: number): number {
  const dx = Math.max(0, -x, x - 1) * COURT_WIDTH_M;
  const dy = Math.max(0, -y, y - 1) * COURT_LENGTH_M;
  return Math.hypot(dx, dy);
}
