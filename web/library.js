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

function card(j) {
  const pct = j.percent || 0;
  let stage = STAGE[j.stage] ?? j.stage;
  if (j.stage === "queued" && j.position) stage += ` · #${j.position + 1}`;
  if (j.stage === "dubbing" || j.stage === "translating") stage += ` · ${pct}%`;
  if (j.chunks_done > 0 && j.stage !== "done") stage += " · watchable";
  if (j.stage === "done") stage = "Ready";
  return `<article class="card ${j.stage}" data-id="${j.video_id}">
    <a class="thumb" href="/watch/${j.video_id}" style="background-image:url('/media/${j.video_id}/thumb.jpg')">
      ${j.duration ? `<span class="dur">${fmt(j.duration)}</span>` : ""}<span class="bar"><i style="width:${pct}%"></i></span></a>
    <a class="title" href="/watch/${j.video_id}" title="${esc(j.title)}">${esc(j.title || j.video_id)}</a>
    <div class="meta"><span class="state" title="${esc(j.error || "")}">${esc(stage)}</span>
      <button class="del" title="Delete" aria-label="Delete">✕</button></div>
  </article>`;
}

async function refresh() {
  try {
    const { jobs } = await (await fetch("/api/jobs", { cache: "no-store" })).json();
    grid.innerHTML = jobs.length
      ? jobs.map(card).join("")
      : `<div class="empty">Paste a YouTube link to dub your first video.</div>`;
  } catch (e) {
    /* server restarting */
  }
}

grid.addEventListener("click", async (e) => {
  if (!e.target.classList.contains("del")) return;
  const id = e.target.closest(".card").dataset.id;
  if (!confirm("Delete this video and its dub?")) return;
  const r = await fetch(`/api/jobs/${id}`, { method: "DELETE" });
  if (!r.ok) alert((await r.json()).error || "Failed");
  refresh();
});

document.getElementById("add").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = document.getElementById("url").value.trim();
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
});

fetch("/api/info")
  .then((r) => r.json())
  .then((info) => {
    document.getElementById("bm").href = info.bookmarklet;
    document.getElementById("base").textContent = info.base_url;
    document.getElementById("ws").textContent = `ws://127.0.0.1:${info.ws_port}/ws`;
  });
refresh();
setInterval(refresh, 3000);
