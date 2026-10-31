// How far the dub has got along the video, from a job status. Pure functions, no page access.
// status.chunks: [start, end, dubbed, seconds until dubbed (null while unknown)]

function chunkIndexAt(status, t) {
  const c = status.chunks;
  for (let i = 0; i < c.length; i++) if (t >= c[i][0] && t < c[i][1]) return i;
  return c.length - 1;
}
/** End of the contiguous dubbed region that contains (or starts at) t. */
export function dubbedUntil(status, t) {
  if (!status || !status.chunks.length) return 0;
  const c = status.chunks;
  let i = chunkIndexAt(status, t);
  if (!c[i][2]) return t >= c[i][0] ? c[i][0] : 0;
  while (i < c.length && c[i][2]) i++;
  return i >= c.length ? status.duration : c[i][0];
}
// The server estimates when each chunk will be dubbed; the estimates are rough, so they get a margin.
const ETA_MARGIN = 1.2,
  ETA_SLACK = 3;
/** Seconds to wait before playback from t, at `speed`, can run to the end without catching up with
 * the dub (Infinity while the server can't tell yet). */
function waitNeeded(status, t, speed) {
  const c = status.chunks;
  let wait = 0;
  for (let i = chunkIndexAt(status, t); i < c.length; i++) {
    if (c[i][2]) continue;
    if (c[i][3] == null) return Infinity;
    wait = Math.max(wait, c[i][3] * ETA_MARGIN + ETA_SLACK - Math.max(0, c[i][0] - t) / speed);
  }
  return wait;
}
/** Playback from t may go on: by the estimates it won't catch up with the dub, or at least `lead`
 * seconds past t are dubbed (when the dub is slower than playback, waiting for all of it would
 * take too long). */
export function canPlay(status, t, lead, speed) {
  const until = dubbedUntil(status, t);
  if (until >= status.duration - 0.5) return true;
  return (
    until - t >= Math.min(lead, status.duration - t - 0.5) ||
    (until - t >= 1.5 && waitNeeded(status, t, speed) <= 0)
  );
}
export function etaText(status, t, speed) {
  const w = waitNeeded(status, t, speed);
  if (!(w > 0 && w < Infinity)) return "";
  return w < 5 ? "almost ready" : `ready in ~${Math.ceil(w)} s`; // short estimates are the least reliable
}
