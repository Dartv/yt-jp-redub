import { $, video } from "./dom.js";
import { status } from "./job.js";
import { hideOverlay, overlay } from "./overlay.js";
import { startAt } from "./playhead.js";
import { prefs } from "./prefs.js";
import * as source from "./source.js";
import { canPlay, dubbedUntil, etaText } from "./timeline.js";
import { pct } from "../common.js";

// When playback may run. With the Japanese voice, it starts as soon as the part at the start point is
// dubbed, and pauses when it catches up with the dub; the viewer's own pauses are kept apart from those.
const LEAD = 1.5; // seconds of dub ahead of the playhead that playback needs to go on
export let started = false, // past the first wait for the dub
  waitingDub = false, // paused by the player, until more is dubbed
  userPaused = false, // paused by the viewer
  lang = "ja";

export function onStatus(s) {
  if (s.stage === "error") {
    video.pause();
    source.stop();
    return;
  }
  if (!source.loaded() && !source.unsupported && s.ready) source.start(startAt, lang, maybeStart);
  if (source.unsupported) return;
  if (!started) maybeStart();
  else checkCatchUp();
}

export function maybeStart() {
  if (started || !status || status.stage === "error") return;
  if (!source.loaded()) {
    const pre = ["queued", "downloading", "transcribing"].includes(status.stage) && status.label;
    overlay(
      pre ? pre + "…" : "Preparing…",
      status.stage_progress != null ? Math.round(status.stage_progress * 100) + "%" : "",
    );
    return;
  }
  if (lang === "ja" && !canPlay(status, startAt, LEAD, prefs.rate)) {
    const parts = [];
    if (status.total_lines && status.translated < status.total_lines)
      parts.push(`translating ${pct(status.translated, status.total_lines)}%`);
    parts.push(etaText(status, startAt, prefs.rate) || "starting soon");
    overlay("Dubbing the first part…", parts.join(" · "));
    return;
  }
  started = true;
  overlay("Ready", "", { spin: false, play: true });
  video
    .play()
    .then(hideOverlay)
    .catch(() => {}); // autoplay may need a tap
}

export function checkCatchUp() {
  if (!status || lang !== "ja" || !started) return;
  const t = video.currentTime,
    until = dubbedUntil(status, t);
  if (!waitingDub && !video.paused && until < status.duration && until - t < LEAD) {
    waitingDub = true;
    video.pause();
  }
  if (waitingDub) {
    if (canPlay(status, t, 30, prefs.rate)) {
      waitingDub = false;
      hideOverlay();
      if (!userPaused) video.play().catch(() => overlay("Ready", "", { spin: false, play: true }));
    } else {
      overlay("Dubbing…", etaText(status, t, prefs.rate) || "almost there");
    }
  }
}

export function togglePlay() {
  if (video.paused) {
    userPaused = false;
    if (!waitingDub) video.play().catch(() => {});
  } else pauseByViewer();
}
export function pauseByViewer() {
  if (video.paused) return;
  userPaused = true;
  video.pause();
}
/** The voice changed: the English one never waits for the dub. */
export function setLang(l) {
  lang = l;
  if (l === "en" && waitingDub) {
    waitingDub = false;
    hideOverlay();
    if (!userPaused) video.play().catch(() => {});
  }
}

export function setupGate() {
  $("ovPlay").onclick = (e) => {
    e.stopPropagation();
    userPaused = false;
    video
      .play()
      .then(hideOverlay)
      .catch(() => {});
  };
  video.addEventListener("play", () => {
    userPaused = false;
    if (!waitingDub) hideOverlay();
  });
  video.addEventListener("pause", () => {
    if (!waitingDub) userPaused = true;
  });
  video.addEventListener("timeupdate", checkCatchUp);
  video.addEventListener("seeking", () => waitingDub && checkCatchUp());
  // a seek past the dubbed part waits there for the dub
  video.addEventListener("seeked", () => {
    if (!started || lang !== "ja") return;
    const t = video.currentTime,
      until = dubbedUntil(status, t);
    if (until - t < LEAD && until < status.duration) {
      waitingDub = true;
      video.pause();
      checkCatchUp();
    }
  });
}
