import { $, video } from "./dom.js";
import { hideControlsSoon, holdControlsWhile, showControls } from "./controls.js";
import { prefs, savePrefs } from "./prefs.js";

// The settings menu: playback speed and subtitle size.
const RATES = [0.5, 0.75, 1, 1.25, 1.5];

export function setRate(r) {
  video.defaultPlaybackRate = video.playbackRate = r; // the default survives a reload of the source
  for (const b of $("rates").children) b.classList.toggle("on", +b.dataset.v === r);
  savePrefs({ rate: r });
}
/** One step slower (-1) or faster (1). */
export function stepRate(dir) {
  const i = RATES.indexOf(prefs.rate) + dir;
  setRate(RATES[Math.min(RATES.length - 1, Math.max(0, i))]);
}
function setSubScale(s) {
  $("subs").style.setProperty("--sub-scale", s);
  for (const b of $("sizes").children) b.classList.toggle("on", +b.dataset.v === s);
  savePrefs({ size: s });
}

function menuOpen() {
  return !$("menu").hidden;
}
function toggleMenu(open = !menuOpen()) {
  $("menu").hidden = !open;
  $("setBtn").classList.toggle("on", open);
  if (open) showControls();
  else hideControlsSoon();
}

export function setupMenu() {
  holdControlsWhile(menuOpen);
  $("setBtn").onclick = (e) => {
    e.stopPropagation();
    toggleMenu();
  };
  $("rates").onclick = (e) => e.target.dataset.v && setRate(+e.target.dataset.v);
  $("sizes").onclick = (e) => e.target.dataset.v && setSubScale(+e.target.dataset.v);
  // clicks on the controls don't reach the picture (which would play/pause), but do close the menu
  $("controls").addEventListener("click", (e) => {
    e.stopPropagation();
    if (menuOpen() && !$("menu").contains(e.target)) toggleMenu(false);
  });
  document.addEventListener("click", (e) => {
    if (menuOpen() && !$("menu").contains(e.target)) toggleMenu(false);
  });
  setRate(RATES.includes(prefs.rate) ? prefs.rate : 1);
  setSubScale(prefs.size);
}
