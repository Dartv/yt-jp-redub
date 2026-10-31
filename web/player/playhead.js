import { video } from "./dom.js";
import { params, status, vid } from "./job.js";

// Where playback starts, and reports of where the viewer is: the server dubs from there first and
// saves it as the point the video resumes from.

/** ?t= if given, else where it was left off (set from the first status). */
export let startAt = params.has("t") ? parseFloat(params.get("t")) || 0 : null;
// playback has reached startAt, so the position is worth saving (before that, currentTime is still
// 0 and would overwrite the saved position)
let positioned = false,
  lastReport = 0;

export function resolveStart(s) {
  if (startAt !== null) return;
  const r = s.resume_at || 0;
  startAt = s.duration && r > s.duration - 10 ? 0 : r; // finished last time: start over
}

function body() {
  const t = positioned ? video.currentTime : startAt || 0;
  return JSON.stringify({ t, save: positioned, asbplayer: true });
}
function report() {
  if (!status || startAt === null) return;
  lastReport = Date.now();
  fetch(`/api/jobs/${vid}/playhead`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body(),
  }).catch(() => {});
}

/** viewerPaused(): the viewer paused it, so nothing moves and there's nothing to report. */
export function setupPlayhead({ viewerPaused }) {
  video.addEventListener("playing", () => {
    if (!positioned) {
      positioned = true;
      report();
    }
  });
  video.addEventListener("pause", report);
  video.addEventListener("seeked", report); // not on seeking: a drag along the seek bar would send dozens
  setInterval(() => {
    if (Date.now() - lastReport > 5000 && !viewerPaused()) report();
  }, 1000);
  // closing the tab or leaving the page saves the latest position
  addEventListener("pagehide", () => {
    if (positioned)
      navigator.sendBeacon(`/api/jobs/${vid}/playhead`, new Blob([body()], { type: "application/json" }));
  });
}
