"use strict";
const vid = location.pathname.split("/").pop();
const params = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);
const video = $("video"),
  stage = $("stage");
const media = `/media/${vid}`;
const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/></svg>',
  fs: '<svg viewBox="0 0 24 24"><path d="M5 9V5h4v2H7v2zm10-4h4v4h-2V7h-2zM5 15h2v2h2v2H5zm12 0h2v4h-4v-2h2z"/></svg>',
  unfs: '<svg viewBox="0 0 24 24"><path d="M9 5v4H5V7h2V5zm6 0h2v2h2v2h-4zM5 15h4v4H7v-2H5zm10 0h4v2h-2v2h-2z"/></svg>',
};

let status = null,
  info = { head_start_sec: 120 },
  lines = [],
  hls = null;
let started = false,
  waitingDub = false,
  userPaused = false,
  lang = "ja";
const variantMode = params.get("mode") === "variants";
let showJa = true,
  showEn = false,
  tracksAdded = false;
let rate = { t: Date.now(), done: 0, secPerChunk: null };
const startAt = parseFloat(params.get("t") || "0") || 0;
try {
  const p = JSON.parse(localStorage.getItem("ytjp-prefs") || "{}");
  showJa = p.ja ?? true;
  showEn = p.en ?? false;
} catch (e) {}

// ------------------------------------------------------------------ helpers
function fmt(s) {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600),
    m = Math.floor((s % 3600) / 60),
    x = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
}
function overlay(title, text, { spin = true, play = false } = {}) {
  $("overlay").hidden = false;
  $("ovTitle").textContent = title;
  $("ovText").textContent = text || "";
  $("spin").hidden = !spin;
  $("ovPlay").hidden = !play;
}
function hideOverlay() {
  $("overlay").hidden = true;
}
function duration() {
  return (status && status.duration) || video.duration || 0;
}

function chunkIndexAt(t) {
  const c = status.chunks;
  for (let i = 0; i < c.length; i++) if (t >= c[i][0] && t < c[i][1]) return i;
  return c.length - 1;
}
/** End of the contiguous dubbed region that contains (or starts at) t. */
function dubbedUntil(t) {
  if (!status || !status.chunks.length) return 0;
  const c = status.chunks;
  let i = chunkIndexAt(t);
  if (!c[i][2]) return t >= c[i][0] ? c[i][0] : 0;
  while (i < c.length && c[i][2]) i++;
  return i >= c.length ? status.duration : c[i][0];
}
function needAhead(t) {
  return Math.min(info.head_start_sec, Math.max(0, (status.duration || 0) - t - 0.5));
}
function etaText(t) {
  const c = status.chunks;
  let missing = 0;
  for (let i = chunkIndexAt(t); i < c.length && c[i][0] < t + needAhead(t) && missing < 50; i++)
    if (!c[i][2]) missing++;
  if (!rate.secPerChunk || !missing) return "";
  return `ready in ~${Math.ceil(missing * rate.secPerChunk)} s`;
}

// ------------------------------------------------------------------ status polling
async function poll() {
  try {
    const r = await fetch(`/api/jobs/${vid}`, { cache: "no-store" });
    if (r.ok) onStatus(await r.json());
  } catch (e) {}
  setTimeout(poll, status && status.stage === "done" ? 10000 : 2000);
}

function onStatus(s) {
  const prevDone = status ? status.chunks_done : 0;
  status = s;
  document.title = (s.title || vid) + " · yt-jp-redub";
  $("title").textContent = $("topTitle").textContent = s.title || "";
  if (s.chunks_done > prevDone) {
    const now = Date.now();
    if (rate.done) rate.secPerChunk = (now - rate.t) / 1000 / Math.max(1, s.chunks_done - rate.done);
    rate = { t: now, done: s.chunks_done, secPerChunk: rate.secPerChunk };
  }
  renderDubbed();
  renderStatus();
  if (!s.subs_complete || !lines.length) loadLines();
  if (s.subs_complete) addTracks();
  if (s.stage === "error") {
    overlay("Processing failed", s.error || "", { spin: false });
    return;
  }
  if (!hls && !video.src && s.ready) startPlayer();
  if (!started) maybeStart();
  else checkCatchUp();
}

function renderStatus() {
  const s = status,
    parts = [];
  const names = {
    queued: "Queued",
    downloading: "Downloading",
    transcribing: "Transcribing",
    translating: "Translating",
    dubbing: "Dubbing",
    done: "Dub complete",
    error: "Failed",
  };
  let st = names[s.stage] || s.stage;
  if (s.stage === "queued" && s.position) st += ` (#${s.position + 1} in line)`;
  parts.push(st);
  if (s.total_lines && s.translated < s.total_lines) parts.push(`subtitles ${s.translated}/${s.total_lines}`);
  if (s.chunks_total && s.stage !== "done") parts.push(`dubbed ${s.chunks_done}/${s.chunks_total} parts`);
  $("status").textContent = parts.join(" · ");
}

function renderDubbed() {
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
function renderTime() {
  const d = duration(),
    t = video.currentTime || 0,
    pct = d ? Math.min(100, (t / d) * 100) : 0;
  $("played").style.width = pct + "%";
  $("knob").style.left = pct + "%";
  $("time").textContent = `${fmt(t)} / ${fmt(d)}`;
}

async function loadLines() {
  try {
    const r = await fetch(`/api/jobs/${vid}/lines`, { cache: "no-store" });
    lines = (await r.json()).lines;
    shownId = undefined;
    renderSubs();
  } catch (e) {}
}

// ------------------------------------------------------------------ playback
function startPlayer() {
  const src = variantMode ? `${media}/master.m3u8?audio=${lang}` : `${media}/master.m3u8`;
  if (window.Hls && Hls.isSupported()) {
    const retry = { maxNumRetry: 1000, retryDelayMs: 1000, maxRetryDelayMs: 4000 };
    hls = new Hls({
      maxBufferLength: 60,
      maxMaxBufferLength: 120,
      startPosition: startAt,
      fragLoadPolicy: {
        default: {
          maxTimeToFirstByteMs: 30000,
          maxLoadTimeMs: 60000,
          timeoutRetry: retry,
          errorRetry: retry,
        },
      },
    });
    hls.on(Hls.Events.MANIFEST_PARSED, () => maybeStart());
    hls.on(Hls.Events.AUDIO_TRACKS_UPDATED, () => setAudio(lang, true));
    hls.on(Hls.Events.ERROR, (_, d) => {
      if (!d.fatal) return;
      if (d.type === Hls.ErrorTypes.NETWORK_ERROR) setTimeout(() => hls.startLoad(), 2000);
      else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
      else console.error(d);
    });
    hls.loadSource(src);
    hls.attachMedia(video);
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = src;
    video.addEventListener(
      "loadedmetadata",
      () => {
        if (startAt) video.currentTime = startAt;
        maybeStart();
      },
      { once: true },
    );
  } else {
    overlay("This browser can't play HLS", "", { spin: false });
  }
}

function maybeStart() {
  if (started || !status || status.stage === "error") return;
  if (!hls && !video.src) {
    const pre = { queued: "Queued", downloading: "Downloading", transcribing: "Transcribing" }[status.stage];
    overlay(
      pre ? pre + "…" : "Preparing…",
      status.stage_progress != null ? Math.round(status.stage_progress * 100) + "%" : "",
    );
    return;
  }
  const t = startAt,
    until = dubbedUntil(t);
  if (lang === "ja" && until - t < needAhead(t)) {
    const have = Math.max(0, until - t),
      need = needAhead(t),
      eta = etaText(t);
    overlay(
      "Dubbing the first part…",
      `${Math.round(have)} / ${Math.round(need)} s ready${eta ? " · " + eta : ""}`,
    );
    return;
  }
  started = true;
  overlay("Ready", "", { spin: false, play: true });
  video
    .play()
    .then(hideOverlay)
    .catch(() => {}); // autoplay may need a tap
}

function checkCatchUp() {
  if (!status || lang !== "ja" || !started) return;
  const t = video.currentTime,
    until = dubbedUntil(t);
  const remaining = (status.duration || 0) - t;
  if (!waitingDub && !video.paused && until < status.duration && until - t < 1.5) {
    waitingDub = true;
    video.pause();
  }
  if (waitingDub) {
    const enough = until - t >= Math.min(30, remaining - 0.5) || until >= status.duration;
    if (enough) {
      waitingDub = false;
      hideOverlay();
      if (!userPaused) video.play().catch(() => overlay("Ready", "", { spin: false, play: true }));
    } else {
      overlay("Dubbing…", etaText(t) || "almost there");
    }
  }
}

function togglePlay() {
  if (video.paused) {
    userPaused = false;
    if (!waitingDub) video.play().catch(() => {});
  } else {
    userPaused = true;
    video.pause();
  }
}
$("ovPlay").onclick = (e) => {
  e.stopPropagation();
  userPaused = false;
  video.play().then(hideOverlay);
};
$("playBtn").onclick = (e) => {
  e.stopPropagation();
  togglePlay();
};
video.addEventListener("play", () => {
  userPaused = false;
  if (!waitingDub) hideOverlay();
  renderPlayIcon();
  hideControlsSoon();
});
video.addEventListener("pause", () => {
  if (!waitingDub) userPaused = true;
  renderPlayIcon();
  showControls();
});
video.addEventListener("timeupdate", () => {
  renderSubs();
  checkCatchUp();
  renderTime();
});
video.addEventListener("durationchange", () => {
  renderTime();
  renderDubbed();
});
video.addEventListener("seeking", () => {
  reportPlayhead();
  renderSubs();
  renderTime();
  if (waitingDub) checkCatchUp();
});
video.addEventListener("seeked", () => {
  if (!started || lang !== "ja") return;
  const t = video.currentTime,
    until = dubbedUntil(t);
  if (until - t < 1.5 && until < status.duration) {
    waitingDub = true;
    video.pause();
    checkCatchUp();
  }
});
function renderPlayIcon() {
  $("playBtn").innerHTML = video.paused ? ICON.play : ICON.pause;
}
renderPlayIcon();

// ------------------------------------------------------------------ controls visibility
let hideTimer = 0;
function showControls() {
  stage.classList.add("controls-on");
  clearTimeout(hideTimer);
  if (!video.paused) hideControlsSoon();
}
function hideControlsSoon() {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (!video.paused && !seeking) stage.classList.remove("controls-on");
  }, 2800);
}
stage.addEventListener("mousemove", showControls);
// tap on the picture: on touch screens it shows/hides the controls, with a mouse it plays/pauses
stage.addEventListener("click", (e) => {
  if (e.target !== video && e.target !== stage) return;
  if (matchMedia("(hover: none)").matches) {
    if (stage.classList.contains("controls-on") && !video.paused) stage.classList.remove("controls-on");
    else showControls();
  } else {
    togglePlay();
    showControls();
  }
});

// ------------------------------------------------------------------ seek bar
let seeking = false;
function seekTo(e) {
  const r = $("seek").getBoundingClientRect(),
    d = duration();
  if (!d) return;
  const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  video.currentTime = x * d;
  renderTime();
}
$("seek").addEventListener("pointerdown", (e) => {
  e.stopPropagation();
  seeking = true;
  $("seek").classList.add("dragging");
  $("seek").setPointerCapture(e.pointerId);
  seekTo(e);
});
$("seek").addEventListener("pointermove", (e) => {
  if (seeking) seekTo(e);
});
$("seek").addEventListener("pointerup", () => {
  seeking = false;
  $("seek").classList.remove("dragging");
  hideControlsSoon();
});
$("controls").addEventListener("click", (e) => e.stopPropagation());

// ------------------------------------------------------------------ audio switch
function setAudio(l, initial = false) {
  lang = l;
  $("audJa").classList.toggle("on", l === "ja");
  $("audEn").classList.toggle("on", l === "en");
  if (l === "en" && waitingDub) {
    waitingDub = false;
    hideOverlay();
    if (!userPaused) video.play().catch(() => {});
  }
  if (!hls) return;
  if (!variantMode) {
    const idx = hls.audioTracks.findIndex((t) => t.lang === l);
    if (idx >= 0 && hls.audioTrack !== idx) hls.audioTrack = idx;
  } else if (!initial) {
    // fallback: separate variant streams; reload at the current time
    const t = video.currentTime,
      playing = !video.paused;
    hls.loadSource(`${media}/master.m3u8?audio=${l}`);
    hls.once(Hls.Events.MANIFEST_PARSED, () => {
      video.currentTime = t;
      if (playing) video.play().catch(() => {});
    });
  }
  if (l === "ja") checkCatchUp();
}
$("audJa").onclick = () => setAudio("ja");
$("audEn").onclick = () => setAudio("en");

// ------------------------------------------------------------------ built-in subtitles
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
let shownId = null;
function setSub(el, text) {
  el.textContent = "";
  if (!text) return;
  const span = document.createElement("span");
  span.textContent = text;
  el.appendChild(span);
}
function renderSubs() {
  const l = lineAt(video.currentTime);
  const id = l ? l.id + (l.ja ? "j" : "") + (showJa ? 1 : 0) + (showEn ? 1 : 0) : null;
  if (id === shownId) return;
  shownId = id;
  setSub($("subJa"), l && showJa ? l.ja : "");
  setSub($("subEn"), l && showEn ? l.en : "");
}
function toggleSub(which) {
  if (which === "ja") showJa = !showJa;
  else showEn = !showEn;
  $("subBtnJa").classList.toggle("on", showJa);
  $("subBtnEn").classList.toggle("on", showEn);
  try {
    localStorage.setItem("ytjp-prefs", JSON.stringify({ ja: showJa, en: showEn }));
  } catch (e) {}
  shownId = undefined;
  renderSubs();
}
$("subBtnJa").classList.toggle("on", showJa);
$("subBtnEn").classList.toggle("on", showEn);
$("subBtnJa").onclick = () => toggleSub("ja");
$("subBtnEn").onclick = () => toggleSub("en");
// tapping a subtitle pauses; it stays paused until play is pressed
$("subs").addEventListener("click", (e) => {
  e.stopPropagation();
  if (!video.paused) {
    userPaused = true;
    video.pause();
  }
});

// ------------------------------------------------------------------ fullscreen
function isFs() {
  return (
    document.fullscreenElement === stage ||
    document.webkitFullscreenElement === stage ||
    stage.classList.contains("pseudo-fs")
  );
}
async function enterFs() {
  const req = stage.requestFullscreen || stage.webkitRequestFullscreen;
  let ok = false;
  if (req) {
    try {
      const timeout = new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 800));
      await Promise.race([Promise.resolve(req.call(stage, { navigationUI: "hide" })), timeout]);
      ok = true;
    } catch (e) {}
  }
  if (!ok && !document.fullscreenElement && !document.webkitFullscreenElement) {
    // no element fullscreen (iPhone Safari, refused, or never answered): fill the window instead
    stage.classList.add("pseudo-fs");
    document.body.classList.add("has-pseudo-fs");
  }
  try {
    await screen.orientation.lock("landscape");
  } catch (e) {}
  renderFsIcon();
}
async function exitFs() {
  if (stage.classList.contains("pseudo-fs")) {
    stage.classList.remove("pseudo-fs");
    document.body.classList.remove("has-pseudo-fs");
  } else if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
  else if (document.webkitFullscreenElement) document.webkitExitFullscreen();
  try {
    screen.orientation.unlock();
  } catch (e) {}
  renderFsIcon();
}
function renderFsIcon() {
  $("fsBtn").innerHTML = isFs() ? ICON.unfs : ICON.fs;
}
$("fsBtn").onclick = (e) => {
  e.stopPropagation();
  isFs() ? exitFs() : enterFs();
};
document.addEventListener("fullscreenchange", () => {
  if (document.fullscreenElement) {
    stage.classList.remove("pseudo-fs");
    document.body.classList.remove("has-pseudo-fs");
  } else {
    try {
      screen.orientation.unlock();
    } catch (e) {}
  }
  renderFsIcon();
});
renderFsIcon();
// turning a phone sideways goes fullscreen, the way video apps do
matchMedia("(orientation: landscape)").addEventListener("change", (ev) => {
  if (!matchMedia("(hover: none)").matches) return;
  if (ev.matches && !isFs() && started) enterFs();
});

// ------------------------------------------------------------------ keyboard (desktop)
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === " " || e.key === "k") {
    e.preventDefault();
    togglePlay();
  } else if (e.key === "ArrowLeft") {
    video.currentTime = Math.max(0, video.currentTime - 5);
    showControls();
  } else if (e.key === "ArrowRight") {
    video.currentTime = Math.min(duration(), video.currentTime + 5);
    showControls();
  } else if (e.key === "f") isFs() ? exitFs() : enterFs();
  else if (e.key === "Escape" && stage.classList.contains("pseudo-fs")) exitFs();
});

// ------------------------------------------------------------------ playhead reports (priority hints)
let lastReport = 0;
function reportPlayhead() {
  if (!status) return;
  lastReport = Date.now();
  fetch(`/api/jobs/${vid}/playhead`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ t: video.currentTime || startAt, asbplayer: true }),
  }).catch(() => {});
}
setInterval(() => {
  if (Date.now() - lastReport > 5000) reportPlayhead();
}, 1000);

// ------------------------------------------------------------------ asbplayer
// PC: the server pushes subtitles over asbplayer's WebSocket client; this button re-sends them.
$("asbBtn").hidden = matchMedia("(hover: none)").matches;
$("asbBtn").onclick = async () => {
  const r = await fetch(`/api/jobs/${vid}/asbplayer`, { method: "POST" });
  const { clients } = await r.json();
  $("asbBtn").textContent = clients ? "Sent to asbplayer ✓" : "asbplayer isn't connected";
};
// Android: asbplayer discovers <track> elements and asks the page via its event protocol.
function trackList() {
  return ["ja", "en"].map((l) => ({
    id: `${vid}-${l}`,
    label: l === "ja" ? "日本語" : "English",
    language: l,
    url: new URL(`${media}/subs/${l}.vtt`, location.href).href,
    extension: "vtt",
  }));
}
function addTracks() {
  if (tracksAdded) return;
  tracksAdded = true;
  for (const t of trackList()) {
    const el = document.createElement("track");
    Object.assign(el, { kind: "subtitles", label: t.label, srclang: t.language, src: t.url });
    video.appendChild(el);
    el.track.mode = "disabled"; // rendered by the page itself, not the browser
  }
}
function onSyncRequest() {
  const detail =
    status && status.subs_complete
      ? { error: "", basename: status.title || vid, subtitles: trackList() }
      : {
          error: "Subtitles are still being translated; try again in a moment.",
          basename: status ? status.title || vid : vid,
          subtitles: [],
        };
  const ev = () => new CustomEvent("asbplayer-synced-data", { detail });
  video.dispatchEvent(ev());
  document.dispatchEvent(ev());
}
video.addEventListener("asbplayer-get-synced-data", (e) => {
  e.stopPropagation();
  onSyncRequest();
});
document.addEventListener("asbplayer-get-synced-data", (e) => {
  if (e.target === document) onSyncRequest();
});

fetch("/api/info")
  .then((r) => r.json())
  .then((i) => {
    info = i;
  })
  .finally(poll);
