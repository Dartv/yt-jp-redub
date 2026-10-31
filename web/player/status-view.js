import { $, touchUI, video } from "./dom.js";
import { status, vid } from "./job.js";
import { overlay } from "./overlay.js";
import { DL_ICON, downloadOffline, mb, pct } from "../common.js";

// What the page says about the job: the title, the progress line, the download button, and a
// failure with its retry.
export function showStatus(s) {
  document.title = (s.title || vid) + " · yt-jp-redub";
  $("topTitle").textContent = s.title || "";
  $("topTitle").href = s.url;
  renderProgress(s);
  renderDownload(s);
  $("ovRetry").hidden = $("ovErr").hidden = s.stage !== "error";
  $("controls").hidden = s.stage === "error";
  $("asbBtn").hidden = !s.subs_complete || touchUI;
  if (s.stage === "error") {
    const [what, fix] = s.error_hint || ["Processing failed", ""];
    overlay(what, fix, { spin: false });
    $("ovErrText").textContent = s.error || "";
  }
}

function renderProgress(s) {
  let st = s.label;
  if (s.stage === "queued" && s.position) st += ` (#${s.position + 1} in line)`;
  const parts = [st];
  if (s.total_lines && s.translated < s.total_lines) parts.push(`subtitles ${pct(s.translated, s.total_lines)}%`);
  if (s.chunks_total && s.stage !== "done") parts.push(`dubbed ${pct(s.chunks_done, s.chunks_total)}%`);
  $("status").textContent = parts.join(" · ");
}

// One tap saves the video (.mp4) and its subtitles (.srt) to watch without the server; see
// common.js.
function renderDownload(s) {
  const b = $("dl");
  b.hidden = s.stage !== "done" || !s.ready;
  if (b.hidden) return;
  b.innerHTML = DL_ICON + "<span></span>";
  b.lastChild.textContent = s.download_size ? `Download · ${mb(s.download_size)}` : "Download";
  b.title = "Saves the video (.mp4) and its Japanese and English subtitles (.srt) to watch offline";
}

export function setupStatusView() {
  $("dl").onclick = () => downloadOffline(vid);
  $("ovRetry").onclick = async (e) => {
    e.stopPropagation();
    $("ovRetry").hidden = true;
    await fetch(`/api/jobs/${vid}/retry`, { method: "POST" }).catch(() => {});
    location.reload();
  };
  // the title opens the original video at the current time; YouTube and Twitch VODs both read t=1h2m3s
  $("topTitle").addEventListener("click", (e) => {
    if (!status || !status.url || status.url.includes("clips.twitch.tv")) return;
    const sec = Math.floor(video.currentTime || 0),
      h = sec >= 3600 ? `${Math.floor(sec / 3600)}h` : "",
      m = sec >= 60 ? `${Math.floor(sec / 60) % 60}m` : "",
      u = new URL(status.url);
    u.searchParams.set("t", `${h}${m}${sec % 60}s`);
    e.currentTarget.href = u.href;
  });
}
