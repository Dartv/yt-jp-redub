// Shared by the library and the player.
// the server takes posts only as JSON (or from htmx), so a cross-site form can't make them
export const JSON_TYPE = { "Content-Type": "application/json" };

/** Seconds as h:mm:ss or m:ss. */
export function fmt(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600),
    m = Math.floor((s % 3600) / 60),
    x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
}
export function mb(bytes) {
  return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(bytes / 1e6)) + " MB";
}
export function pct(n, total) {
  return Math.floor((100 * n) / (total || 1));
}
/** A failed request's message: JSON {error} from the API, else the plain-text body. */
export function errorMessage(text, status) {
  try {
    return JSON.parse(text).error || text;
  } catch (e) {
    return text || `HTTP ${status}`;
  }
}
export async function errorText(r) {
  return errorMessage(await r.text().catch(() => ""), r.status);
}

/** One tap saves everything needed offline: the .mp4 and its Japanese and English .srt files
 * (asbplayer takes subtitles as separate files). The server packages the .mp4 as it's requested,
 * in a second or two. */
export function downloadOffline(vid) {
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
