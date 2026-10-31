"use strict";
const STAGE = {
  queued: "Queued",
  downloading: "Downloading",
  transcribing: "Transcribing",
  translating: "Translating",
  dubbing: "Dubbing",
  done: "",
  error: "Failed",
};
const grid = document.getElementById("grid");
const DL_ICON =
  '<svg viewBox="0 0 24 24"><path d="M11 4h2v8.2l3.3-3.3 1.4 1.4L12 16l-5.7-5.7 1.4-1.4 3.3 3.3zM5 18h14v2H5z"/></svg>';

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}
function fmt(s) {
  s = Math.round(s);
  const h = Math.floor(s / 3600),
    m = Math.floor((s % 3600) / 60),
    x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
}

function mb(bytes) {
  return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(bytes / 1e6)) + " MB";
}
/** Finished videos: one tap saves the .mp4 and its subtitles (see download.js). */
function download(j) {
  if (j.stage !== "done" || !j.ready) return "";
  const size = j.download_size ? ` (${mb(j.download_size)})` : "";
  return `<button class="dl" title="Download the video and its subtitles${size}" aria-label="Download">${DL_ICON}</button>`;
}

/** How far it has been watched, as a red line along the thumbnail's bottom (as on YouTube). */
function watched(j) {
  if (j.stage !== "done" || !j.resume_at || !j.duration) return "";
  const p = Math.min(100, Math.max(2, (100 * j.resume_at) / j.duration));
  return `<span class="watched"><i style="width:${p}%"></i></span>`;
}

function card(j) {
  const pct = j.percent || 0;
  let stage = STAGE[j.stage] ?? j.stage;
  if (j.stage === "queued" && j.position) stage += ` · #${j.position + 1}`;
  if (!["queued", "error", "done"].includes(j.stage)) stage += ` · ${pct}%`;
  if (j.chunks_done > 0 && j.stage !== "done") stage += " · watchable";
  if (j.stage === "done") stage = "Ready";
  return `<article class="card ${j.stage}" data-id="${j.video_id}">
    <a class="thumb" href="/watch/${j.video_id}" style="background-image:url('/media/${j.video_id}/thumb.jpg')">
      ${j.duration ? `<span class="dur">${fmt(j.duration)}</span>` : ""}<span class="bar"><i style="width:${pct}%"></i></span>${watched(j)}</a>
    <a class="title" href="/watch/${j.video_id}" title="${esc(j.title)}">${esc(j.title || j.video_id)}</a>
    <div class="meta"><span class="state" title="${esc(j.error || "")}">${esc(stage)}</span>
      ${j.stage === "error" ? `<button class="retry" title="Retry" aria-label="Retry">↻</button>` : ""}
      ${download(j)}<button class="del" title="Delete" aria-label="Delete">✕</button></div>
  </article>`;
}

async function refresh() {
  try {
    const { jobs } = await (await fetch("/api/jobs", { cache: "no-store" })).json();
    grid.innerHTML = jobs.length
      ? jobs.map(card).join("")
      : `<div class="empty">Paste a YouTube or Twitch link to dub your first video.</div>`;
  } catch (e) {
    /* server restarting */
  }
}

grid.addEventListener("click", async (e) => {
  if (e.target.closest("button.dl")) {
    downloadOffline(e.target.closest(".card").dataset.id);
    return;
  }
  if (e.target.classList.contains("retry")) {
    const r = await fetch(`/api/jobs/${e.target.closest(".card").dataset.id}/retry`, { method: "POST" });
    if (!r.ok) alert("Failed");
    refresh();
    return;
  }
  if (!e.target.classList.contains("del")) return;
  const id = e.target.closest(".card").dataset.id;
  if (!confirm("Delete this video and its dub?")) return;
  const r = await fetch(`/api/jobs/${id}`, { method: "DELETE" });
  if (!r.ok) alert((await r.json()).error || "Failed");
  refresh();
});

const urlInput = document.getElementById("url");
async function dub(url) {
  const r = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  const data = await r.json();
  if (!r.ok) {
    alert(data.error || "Failed");
    return;
  }
  location.href = `/watch/${data.video_id}`;
}
document.getElementById("add").addEventListener("submit", (e) => {
  e.preventDefault();
  dub(urlInput.value.trim());
});
// pasting a YouTube video or Twitch VOD/clip link dubs it right away
const VIDEO_LINK = /(youtube\.com|youtu\.be)\/|twitch\.tv\/(videos\/|[^/?#]+\/clip\/)|clips\.twitch\.tv\//;
urlInput.addEventListener("paste", (e) => {
  const text = e.clipboardData.getData("text").trim();
  if (!VIDEO_LINK.test(text)) return;
  e.preventDefault();
  urlInput.value = text;
  dub(text);
});
// the paste button reads the clipboard where the browser allows it (HTTPS or this PC). Over the
// network's plain http a page can't, so it opens the box instead: the phone keyboard offers the
// copied link as a suggestion, and tapping it dubs the video.
document.getElementById("paste").addEventListener("click", async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (VIDEO_LINK.test(text)) {
      urlInput.value = text;
      return dub(text);
    }
  } catch (e) {}
  urlInput.focus();
});

fetch("/api/info")
  .then((r) => r.json())
  .then((info) => {
    document.getElementById("bm").href = info.bookmarklet;
    document.getElementById("base").textContent = info.base_url;
    // the QR code is for a phone to scan from this PC's screen
    document.getElementById("phone").hidden =
      matchMedia("(hover: none)").matches || info.base_url.startsWith("http://127.0.0.1");
    document.getElementById("ws").textContent = `ws://127.0.0.1:${info.ws_port}/ws`;
  });
refresh();
setInterval(refresh, 3000);
