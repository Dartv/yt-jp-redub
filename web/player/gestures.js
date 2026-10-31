import { $, stage, touchUI, video } from "./dom.js";
import { controlsShown, showControls } from "./controls.js";
import { pauseByViewer, togglePlay, waitingDub } from "./gate.js";
import { seekLine, skipBy } from "./navigate.js";

// Taps, swipes and pointing on the picture and its subtitles.
let lastTap = { t: 0, side: 0 },
  tapTimer = 0,
  swipedAt = 0,
  swipe = null,
  hoverPaused = false,
  unhoverTimer = 0;
const justSwiped = () => Date.now() - swipedAt < 500;

function tapPicture() {
  if (controlsShown()) togglePlay();
  showControls();
}

// tap on the picture plays/pauses. On touch screens, while the controls are hidden (fullscreen or
// sideways, during playback) it brings them up instead; in portrait they always show. A double tap
// on the left or right part skips 5 s.
function onPictureClick(e) {
  if (e.target !== video && e.target !== stage) return;
  if (justSwiped()) return;
  if (!touchUI) {
    togglePlay();
    showControls();
    return;
  }
  const r = stage.getBoundingClientRect(),
    x = (e.clientX - r.left) / r.width,
    side = x < 0.35 ? -1 : x > 0.65 ? 1 : 0,
    now = Date.now();
  clearTimeout(tapTimer);
  if (side && side === lastTap.side && now - lastTap.t < 320) {
    // second tap (or a further one in the same burst): skip, and keep the burst going
    lastTap = { t: now, side };
    skipBy(side * 5);
    return;
  }
  lastTap = { t: now, side };
  // on the sides, wait to see whether this is the first tap of a double tap
  tapTimer = setTimeout(tapPicture, side ? 320 : 0);
}

// swipe right / left on the picture: next / previous subtitle (the way a seek bar drags)
function onTouchStart(e) {
  const onPicture = e.target === video || e.target === stage || $("subs").contains(e.target);
  swipe =
    onPicture && e.touches.length === 1 ? { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() } : null;
}
function onTouchEnd(e) {
  if (!swipe) return;
  const p = e.changedTouches[0],
    dx = p.clientX - swipe.x,
    dy = p.clientY - swipe.y,
    dt = Date.now() - swipe.t;
  swipe = null;
  if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy) && dt < 800) {
    swipedAt = Date.now();
    clearTimeout(tapTimer);
    seekLine(dx > 0 ? 1 : -1);
  }
}

export function setupGestures() {
  stage.addEventListener("click", onPictureClick);
  stage.addEventListener("touchstart", onTouchStart, { passive: true });
  stage.addEventListener("touchend", onTouchEnd, { passive: true });
  // tapping (or clicking) a subtitle pauses; it stays paused until play is pressed
  $("subs").addEventListener("click", (e) => {
    e.stopPropagation();
    if (justSwiped()) return; // the end of a swipe that started on a subtitle
    hoverPaused = false; // the click makes a hover pause stick
    pauseByViewer();
  });
  // with a mouse, pointing at a subtitle (to look words up) pauses, and moving off it resumes, as
  // asbplayer does; the delay bridges the gap between the Japanese and English lines
  $("subs").addEventListener("pointerover", (e) => {
    if (e.pointerType !== "mouse") return;
    clearTimeout(unhoverTimer);
    if (!video.paused && !waitingDub) {
      hoverPaused = true;
      video.pause();
    }
  });
  $("subs").addEventListener("pointerout", (e) => {
    if (e.pointerType !== "mouse" || !hoverPaused) return;
    clearTimeout(unhoverTimer);
    unhoverTimer = setTimeout(() => {
      if (hoverPaused) video.play().catch(() => {});
    }, 150);
  });
  video.addEventListener("play", () => {
    hoverPaused = false;
  });
}
