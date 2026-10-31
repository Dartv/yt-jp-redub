"use strict";
// Shared by the library and the player.
const STAGE = {
  queued: "Queued",
  downloading: "Downloading",
  transcribing: "Transcribing",
  translating: "Translating",
  dubbing: "Dubbing",
  done: "Dub complete",
  error: "Failed",
};
const DL_ICON =
  '<svg viewBox="0 0 24 24"><path d="M11 4h2v8.2l3.3-3.3 1.4 1.4L12 16l-5.7-5.7 1.4-1.4 3.3 3.3zM5 18h14v2H5z"/></svg>';

/** Seconds as h:mm:ss or m:ss. */
function fmt(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600),
    m = Math.floor((s % 3600) / 60),
    x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
}
function mb(bytes) {
  return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(bytes / 1e6)) + " MB";
}
/** A failed request's message: JSON {error} from the API, else the plain-text body. */
async function errorText(r) {
  const text = await r.text().catch(() => "");
  try {
    return JSON.parse(text).error || text;
  } catch (e) {
    return text || `HTTP ${r.status}`;
  }
}

/** One tap saves everything needed offline: the .mp4 and its Japanese and English .srt files
 * (asbplayer takes subtitles as separate files). The server packages the .mp4 as it's requested,
 * in a second or two. */
function downloadOffline(vid) {
  const m = `/media/${vid}`;
  [`${m}/download.mp4`, `${m}/download.ja.srt`, `${m}/download.en.srt`].forEach((url, i) =>
    // spaced out: browsers may drop downloads started in the same instant
    setTimeout(() => {
      const a = document.createElement("a");
      a.href = url;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
    }, i * 400),
  );
}
