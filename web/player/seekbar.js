import { $, video } from "./dom.js";
import { duration, status } from "./job.js";
import { holdControlsWhile, hideControlsSoon } from "./controls.js";
import { fmt } from "../common.js";

// The seek bar (with the dubbed parts marked along it) and the time.
let dragging = false;

export function renderTime() {
  const d = duration(),
    t = video.currentTime || 0,
    p = d ? Math.min(100, (t / d) * 100) : 0;
  $("played").style.width = p + "%";
  $("knob").style.left = p + "%";
  $("seek").setAttribute("aria-valuemax", Math.round(d));
  $("seek").setAttribute("aria-valuenow", Math.round(t));
  $("seek").setAttribute("aria-valuetext", `${fmt(t)} of ${fmt(d)}`);
  $("time").textContent = `${fmt(t)} / ${fmt(d)}`;
}
export function renderDubbed() {
  const d = duration();
  if (!d || !status) return;
  $("dubbed").innerHTML = status.chunks
    .filter((c) => c[2])
    .map(
      (c) =>
        `<div class="dubbed" style="left:${(c[0] / d) * 100}%;width:${((c[1] - c[0]) / d) * 100}%"></div>`,
    )
    .join("");
}

function seekTo(e) {
  const r = $("seek").getBoundingClientRect(),
    d = duration();
  if (!d) return;
  const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  video.currentTime = x * d;
  renderTime();
}

export function setupSeekbar() {
  const seek = $("seek");
  holdControlsWhile(() => dragging);
  seek.addEventListener("pointerdown", (e) => {
    e.stopPropagation();
    dragging = true;
    seek.classList.add("dragging");
    seek.setPointerCapture(e.pointerId);
    seekTo(e);
  });
  seek.addEventListener("pointermove", (e) => {
    if (dragging) seekTo(e);
  });
  for (const type of ["pointerup", "pointercancel"])
    seek.addEventListener(type, () => {
      dragging = false;
      seek.classList.remove("dragging");
      hideControlsSoon();
    });
  for (const type of ["timeupdate", "seeking"]) video.addEventListener(type, renderTime);
  video.addEventListener("durationchange", () => {
    renderTime();
    renderDubbed();
  });
}
