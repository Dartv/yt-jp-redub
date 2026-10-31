import { $, video } from "./dom.js";
import { duration } from "./job.js";
import { renderTime } from "./seekbar.js";
import { adjacentLine, redrawSubs } from "./subtitles.js";

// Jumps: a few seconds back or forward (with a hint of the total over the picture), or to the
// previous, current or next subtitle.
let hint = { el: null, total: 0, timer: 0 };

export function skipBy(sec) {
  const d = duration();
  if (!d) return;
  video.currentTime = Math.min(d, Math.max(0, video.currentTime + sec));
  renderTime();
  const el = sec < 0 ? $("skipBack") : $("skipFwd");
  if (hint.el !== el) {
    if (hint.el) hint.el.classList.remove("on"); // its hide timer is about to be cleared
    hint = { el, total: 0, timer: hint.timer };
  }
  hint.total += sec;
  el.textContent = sec < 0 ? `« ${-hint.total} s` : `${hint.total} s »`;
  el.style.top = video.offsetTop + video.offsetHeight / 2 + "px"; // the picture's middle, even in portrait
  el.classList.add("on");
  clearTimeout(hint.timer);
  hint.timer = setTimeout(() => {
    el.classList.remove("on");
    hint = { el: null, total: 0, timer: 0 };
  }, 700);
}

/** dir: -1 previous, 0 start of the current one, 1 next. */
export function seekLine(dir) {
  const l = adjacentLine(dir);
  if (!l) return;
  video.currentTime = l.start;
  redrawSubs();
  renderTime();
}
