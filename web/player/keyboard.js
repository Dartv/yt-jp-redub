import { stage } from "./dom.js";
import { showControls } from "./controls.js";
import { exitFs, toggleFs } from "./fullscreen.js";
import { togglePlay } from "./gate.js";
import { stepRate } from "./menu.js";
import { seekLine, skipBy } from "./navigate.js";
import { toggleMute } from "./volume.js";

// Keyboard shortcuts (desktop), after asbplayer's and YouTube's.
function onKey(e) {
  if (e.target.tagName === "INPUT" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === " " || e.key === "k") {
    e.preventDefault();
    togglePlay();
  } else if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "ArrowDown") {
    // previous / next subtitle, start of the current one (asbplayer's keys)
    e.preventDefault();
    seekLine(e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0);
    showControls();
  } else if (e.key === "a" || e.key === "d") {
    skipBy(e.key === "a" ? -5 : 5);
    showControls();
  } else if (e.key === "<" || e.key === ">") {
    stepRate(e.key === ">" ? 1 : -1); // slower / faster, as on YouTube
    showControls();
  } else if (e.key === "m") {
    toggleMute();
    showControls();
  } else if (e.key === "f") toggleFs();
  else if (e.key === "Escape" && stage.classList.contains("pseudo-fs")) exitFs();
}

export function setupKeyboard() {
  document.addEventListener("keydown", onKey);
}
