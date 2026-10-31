import { $, ICON, stage, video } from "./dom.js";
import { togglePlay } from "./gate.js";

// The control bar: shown while paused or pointed at, hidden a moment into playback; and its play
// button.
const holds = [];
let hideTimer = 0;
/** Keeps the controls up while held() is true (a drag on the seek bar, the open menu). */
export function holdControlsWhile(held) {
  holds.push(held);
}
export function showControls() {
  stage.classList.add("controls-on");
  clearTimeout(hideTimer);
  if (!video.paused) hideControlsSoon();
}
export function hideControlsSoon() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (!video.paused && !holds.some((held) => held())) stage.classList.remove("controls-on");
  }, 2800);
}
/** The controls are up (on touch screens in portrait they always are). */
export function controlsShown() {
  return getComputedStyle($("controls")).opacity > 0.5;
}

function renderPlayIcon() {
  $("playBtn").innerHTML = video.paused ? ICON.play : ICON.pause;
  $("playBtn").setAttribute("aria-label", video.paused ? "Play" : "Pause");
}

export function setupControls() {
  // a mouse only: after a tap, browsers also send a mousemove, which would undo the tap's toggle
  stage.addEventListener("pointermove", (e) => e.pointerType === "mouse" && showControls());
  video.addEventListener("play", () => {
    renderPlayIcon();
    hideControlsSoon();
  });
  video.addEventListener("pause", () => {
    renderPlayIcon();
    showControls();
  });
  $("playBtn").onclick = (e) => {
    e.stopPropagation();
    togglePlay();
  };
  renderPlayIcon();
}
