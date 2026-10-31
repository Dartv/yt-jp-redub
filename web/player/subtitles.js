import { $, setOn, video } from "./dom.js";
import { vid } from "./job.js";
import { prefs, savePrefs } from "./prefs.js";

// The player's own Japanese and English subtitles: fetched as they're translated, drawn over the
// picture, and switched on or off.
let lines = [],
  linesFor = null, // how many were translated when they were fetched
  loading = false,
  shownId = null;

/** Fetches the lines again when more are translated, one request at a time. */
export async function updateLines(s) {
  if (s.translated === linesFor || loading) return;
  loading = true;
  try {
    const r = await fetch(`/api/jobs/${vid}/lines`, { cache: "no-store" });
    if (r.ok) {
      lines = (await r.json()).lines;
      linesFor = s.translated;
      redrawSubs();
    }
  } catch (e) {}
  loading = false;
}

function lineAt(t) {
  let lo = 0,
    hi = lines.length - 1,
    best = -1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (lines[m].start <= t) {
      best = m;
      lo = m + 1;
    } else hi = m - 1;
  }
  if (best < 0) return null;
  const l = lines[best],
    next = lines[best + 1];
  const end = Math.max(l.end + 0.6, l.start + 1.2);
  return t <= (next ? Math.min(end, next.start) : end) ? l : null;
}
/** The subtitle to jump to, as in asbplayer: next is the first one starting after now; previous
 * is the one before the current, or the last one played when between subtitles; current is the
 * one playing (or the last one played). By index, so slightly overlapping lines aren't skipped. */
export function adjacentLine(dir) {
  const t = video.currentTime + 0.05;
  if (dir > 0) return lines.find((l) => l.start > t) || null;
  let i = -1;
  while (i + 1 < lines.length && lines[i + 1].start <= t) i++;
  if (i < 0) return null;
  const inside = video.currentTime < lines[i].end || t - lines[i].start < 0.1;
  return lines[dir < 0 && inside ? i - 1 : i] || null;
}

function setSub(el, text) {
  el.textContent = "";
  if (!text) return;
  const span = document.createElement("span");
  span.textContent = text;
  el.appendChild(span);
}
/** Draws the line at the current time, if it isn't already showing. */
export function renderSubs() {
  const l = lineAt(video.currentTime);
  const id = l ? l.id + (l.ja ? "j" : "") + (prefs.ja ? 1 : 0) + (prefs.en ? 1 : 0) : null;
  if (id === shownId) return;
  shownId = id;
  setSub($("subJa"), l && prefs.ja ? l.ja : "");
  setSub($("subEn"), l && prefs.en ? l.en : "");
}
/** Draws it even if the same line is showing (its text or the time jumped). */
export function redrawSubs() {
  shownId = undefined;
  renderSubs();
}

function toggleSub(which) {
  savePrefs({ [which]: !prefs[which] });
  setOn($("subBtnJa"), prefs.ja);
  setOn($("subBtnEn"), prefs.en);
  redrawSubs();
}
export function setupSubtitles() {
  setOn($("subBtnJa"), prefs.ja);
  setOn($("subBtnEn"), prefs.en);
  $("subBtnJa").onclick = () => toggleSub("ja");
  $("subBtnEn").onclick = () => toggleSub("en");
  for (const type of ["timeupdate", "seeking"]) video.addEventListener(type, renderSubs);
}
