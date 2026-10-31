"use strict";
const grid = document.getElementById("grid");

function esc(s) {
  return String(s ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}
/** Finished videos: one tap saves the .mp4 and its subtitles (see common.js). */
function downloadButton(j) {
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
  if (j.error_hint) stage += ` · ${j.error_hint[0]}`;
  if (j.chunks_done > 0 && !["done", "error"].includes(j.stage)) stage += " · watchable";
  if (j.stage === "done") stage = j.download_size ? `Ready · ${mb(j.download_size)}` : "Ready";
  return `<article class="card ${j.stage}" data-id="${j.video_id}">
    <a class="thumb" href="/watch/${j.video_id}" style="background-image:url('/media/${j.video_id}/thumb.jpg')">
      ${j.duration ? `<span class="dur">${fmt(j.duration)}</span>` : ""}<span class="bar"><i style="width:${pct}%"></i></span>${watched(j)}</a>
    <a class="title" href="/watch/${j.video_id}" title="${esc(j.title)}">${esc(j.title || j.video_id)}</a>
    <div class="meta"><span class="state" title="${esc(j.error || "")}">${esc(stage)}</span>
      ${j.stage === "error" ? `<button class="retry" title="Retry" aria-label="Retry">↻</button>` : ""}
      ${downloadButton(j)}<button class="del" title="Delete" aria-label="Delete">✕</button></div>
  </article>`;
}

// redrawn only when something changed (a redraw drops keyboard focus), one request at a time
let shown = "",
  refreshing = false;
async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const { jobs } = await (await fetch("/api/jobs", { cache: "no-store" })).json();
    const html = jobs.length
      ? jobs.map(card).join("")
      : `<div class="empty">Paste a YouTube or Twitch link to dub your first video.</div>`;
    if (html !== shown) grid.innerHTML = shown = html;
  } catch (e) {
    /* server restarting */
  }
  refreshing = false;
}

grid.addEventListener("click", async (e) => {
  if (e.target.closest("button.dl")) {
    downloadOffline(e.target.closest(".card").dataset.id);
    return;
  }
  if (e.target.classList.contains("retry")) {
    const r = await fetch(`/api/jobs/${e.target.closest(".card").dataset.id}/retry`, {
      method: "POST",
    }).catch(() => null);
    if (!r || !r.ok) alert(r ? await errorText(r) : "The server isn't reachable");
    refresh();
    return;
  }
  if (!e.target.classList.contains("del")) return;
  const id = e.target.closest(".card").dataset.id;
  if (!confirm("Delete this video and its dub?")) return;
  const r = await fetch(`/api/jobs/${id}`, { method: "DELETE" }).catch(() => null);
  if (!r || !r.ok) alert(r ? await errorText(r) : "The server isn't reachable");
  refresh();
});

const urlInput = document.getElementById("url");
async function dub(url) {
  const r = await fetch("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  }).catch(() => null);
  if (!r || !r.ok) {
    alert(r ? await errorText(r) : "The server isn't reachable");
    return;
  }
  location.href = `/watch/${(await r.json()).video_id}`;
}
document.getElementById("add").addEventListener("submit", (e) => {
  e.preventDefault();
  dub(urlInput.value.trim());
});
// pasting a YouTube video or Twitch VOD/clip link dubs it right away (the server has the exact rules)
const VIDEO_LINK =
  /(youtube\.com|youtu\.be)\/|twitch\.tv\/(videos\/|[^/?#]+\/(clip|v)\/|\?(.*&)?video=)|clips\.twitch\.tv\//;
urlInput.addEventListener("paste", (e) => {
  const text = e.clipboardData.getData("text").trim();
  if (!VIDEO_LINK.test(text)) return;
  e.preventDefault();
  urlInput.value = text;
  dub(text);
});
// so does a link inserted in one go some other way: the phone keyboard's suggestion of the copied
// link, or autofill (typing grows the box a character or a word at a time)
let lastLen = 0;
urlInput.addEventListener("input", () => {
  const grew = urlInput.value.length - lastLen,
    text = urlInput.value.trim();
  lastLen = urlInput.value.length;
  if (grew >= 10 && VIDEO_LINK.test(text)) dub(text);
});
// the paste button reads the clipboard, which browsers allow only over HTTPS or on this PC; over
// the network's plain http it couldn't do anything, so it isn't shown
const paste = document.getElementById("paste");
paste.hidden = !navigator.clipboard?.readText;
paste.addEventListener("click", async () => {
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
    const local = info.base_url.startsWith("http://127.0.0.1"); // bound to this PC only: no phone access
    document.getElementById("phoneLine").hidden = local;
    document.getElementById("phone").hidden = local || matchMedia("(hover: none)").matches;
    document.getElementById("ws").textContent = `ws://127.0.0.1:${info.ws_port}/ws`;
  });
refresh();
setInterval(refresh, 3000);
