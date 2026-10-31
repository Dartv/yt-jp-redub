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
  gear: '<svg viewBox="0 0 24 24"><path d="M19.14 12.94a7.1 7.1 0 0 0 0-1.88l2.03-1.58a.5.5 0 0 0 .12-.61l-1.92-3.32a.5.5 0 0 0-.59-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84a.48.48 0 0 0-.47.41l-.36 2.54a7.3 7.3 0 0 0-1.62.94l-2.39-.96a.5.5 0 0 0-.59.22L2.74 8.87a.5.5 0 0 0 .12.61l2.03 1.58a7.1 7.1 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54a7 7 0 0 0 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.5.5 0 0 0-.12-.61zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z"/></svg>',
  dl: '<svg viewBox="0 0 24 24"><path d="M11 4h2v8.2l3.3-3.3 1.4 1.4L12 16l-5.7-5.7 1.4-1.4 3.3 3.3zM5 18h14v2H5z"/></svg>',
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
  speed = 1, // playback rate
  subScale = 1, // subtitle size
  tracksAdded = false;
// where playback starts: ?t= if given, else where it was left off (set from the first status)
let startAt = params.has("t") ? parseFloat(params.get("t")) || 0 : null;
let positioned = false; // playback has reached startAt, so the position is worth saving
try {
  const p = JSON.parse(localStorage.getItem("ytjp-prefs") || "{}");
  showJa = p.ja ?? true;
  showEn = p.en ?? false;
  speed = p.rate ?? 1;
  subScale = p.size ?? 1;
} catch (e) {}
function savePrefs() {
  try {
    localStorage.setItem(
      "ytjp-prefs",
      JSON.stringify({ ja: showJa, en: showEn, rate: speed, size: subScale }),
    );
  } catch (e) {}
}

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
// The server estimates when each chunk will be dubbed; the estimates are rough, so they get a margin.
const ETA_MARGIN = 1.2,
  ETA_SLACK = 3;
/** Seconds to wait before playback from t, at the current speed, can run to the end without
 * catching up with the dub (Infinity while the server can't tell yet). */
function waitNeeded(t) {
  const c = status.chunks;
  let wait = 0;
  for (let i = chunkIndexAt(t); i < c.length; i++) {
    if (c[i][2]) continue;
    if (c[i][3] == null) return Infinity;
    wait = Math.max(wait, c[i][3] * ETA_MARGIN + ETA_SLACK - Math.max(0, c[i][0] - t) / speed);
  }
  return wait;
}
/** Playback from t may go on: by the estimates it won't catch up with the dub, or at least `lead`
 * seconds past t are dubbed (when the dub is slower than playback, waiting for all of it would
 * take too long). */
function canPlay(t, lead) {
  const until = dubbedUntil(t);
  if (until >= status.duration - 0.5) return true;
  return until - t >= Math.min(lead, status.duration - t - 0.5) || (until - t >= 1.5 && waitNeeded(t) <= 0);
}
function etaText(t) {
  const w = waitNeeded(t);
  return w > 0 && w < Infinity ? `ready in ~${Math.ceil(w)} s` : "";
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
  status = s;
  if (startAt === null) {
    const r = s.resume_at || 0;
    startAt = s.duration && r > s.duration - 10 ? 0 : r; // finished last time: start over
  }
  document.title = (s.title || vid) + " · yt-jp-redub";
  $("title").textContent = $("topTitle").textContent = s.title || "";
  $("title").href = $("topTitle").href = s.url;
  renderDubbed();
  renderStatus();
  renderDownload();
  if (!s.subs_complete || !lines.length) loadLines();
  if (s.subs_complete) addTracks();
  $("ovRetry").hidden = s.stage !== "error";
  if (s.stage === "error") {
    overlay("Processing failed", s.error || "", { spin: false });
    return;
  }
  if (!hls && !video.src && s.ready) startPlayer();
  if (!started) maybeStart();
  else checkCatchUp();
}

function pct(n, total) {
  return Math.floor((100 * n) / (total || 1));
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
  if (s.total_lines && s.translated < s.total_lines) parts.push(`subtitles ${pct(s.translated, s.total_lines)}%`);
  if (s.chunks_total && s.stage !== "done") parts.push(`dubbed ${pct(s.chunks_done, s.chunks_total)}%`);
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
  const t = startAt;
  if (lang === "ja" && !canPlay(t, info.head_start_sec)) {
    const parts = [];
    if (status.total_lines && status.translated < status.total_lines)
      parts.push(`translating ${pct(status.translated, status.total_lines)}%`);
    parts.push(etaText(t) || "starting soon");
    overlay("Dubbing the first part…", parts.join(" · "));
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
  if (!waitingDub && !video.paused && until < status.duration && until - t < 1.5) {
    waitingDub = true;
    video.pause();
  }
  if (waitingDub) {
    if (canPlay(t, 30)) {
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
$("ovRetry").onclick = async (e) => {
  e.stopPropagation();
  $("ovRetry").hidden = true;
  await fetch(`/api/jobs/${vid}/retry`, { method: "POST" }).catch(() => {});
  location.reload();
};
$("ovPlay").onclick = (e) => {
  e.stopPropagation();
  userPaused = false;
  video.play().then(hideOverlay);
};
$("playBtn").onclick = (e) => {
  e.stopPropagation();
  togglePlay();
};
video.addEventListener("playing", () => {
  if (!positioned) {
    positioned = true;
    reportPlayhead();
  }
});
video.addEventListener("play", () => {
  userPaused = false;
  if (!waitingDub) hideOverlay();
  renderPlayIcon();
  hideControlsSoon();
});
video.addEventListener("pause", () => {
  if (!waitingDub) userPaused = true;
  reportPlayhead();
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

// the title opens the original video at the current time; YouTube and Twitch VODs both read t=1h2m3s
for (const a of [$("title"), $("topTitle")])
  a.addEventListener("click", () => {
    if (!status || !status.url || status.url.includes("clips.twitch.tv")) return;
    const s = Math.floor(video.currentTime || 0),
      h = s >= 3600 ? `${Math.floor(s / 3600)}h` : "",
      m = s >= 60 ? `${Math.floor(s / 60) % 60}m` : "",
      u = new URL(status.url);
    u.searchParams.set("t", `${h}${m}${s % 60}s`);
    a.href = u.href;
  });

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
    if (!video.paused && !seeking && !menuOpen()) stage.classList.remove("controls-on");
  }, 2800);
}
stage.addEventListener("mousemove", showControls);
// tap on the picture: on touch screens it shows/hides the controls (double tap on the left or
// right part skips 5 s), with a mouse it plays/pauses
const touchUI = matchMedia("(hover: none)").matches;
let lastTap = { t: 0, side: 0 },
  tapTimer = 0,
  swipedAt = 0;
function toggleControlsTap() {
  if (stage.classList.contains("controls-on") && !video.paused) stage.classList.remove("controls-on");
  else showControls();
}
stage.addEventListener("click", (e) => {
  if (e.target !== video && e.target !== stage) return;
  if (Date.now() - swipedAt < 500) return;
  if (!touchUI) {
    togglePlay();
    showControls();
    return;
  }
  const r = stage.getBoundingClientRect(),
    x = (e.clientX - r.left) / r.width,
    side = x < 0.35 ? -1 : x > 0.65 ? 1 : 0,
    now = Date.now();
  clearTimeout(tapTimer);
  if (side && side === lastTap.side && now - lastTap.t < 320) {
    // second tap (or a further one in the same burst): skip, and keep the burst going
    lastTap = { t: now, side };
    skipBy(side * 5);
    return;
  }
  lastTap = { t: now, side };
  // on the sides, wait to see whether this is the first tap of a double tap
  tapTimer = setTimeout(toggleControlsTap, side ? 320 : 0);
});

// ------------------------------------------------------------------ skipping and subtitle navigation
let skipHint = { el: null, total: 0, timer: 0 };
function skipBy(sec) {
  const d = duration();
  if (!d) return;
  video.currentTime = Math.min(d, Math.max(0, video.currentTime + sec));
  renderTime();
  const el = sec < 0 ? $("skipBack") : $("skipFwd");
  if (skipHint.el !== el) skipHint = { el, total: 0, timer: skipHint.timer };
  skipHint.total += sec;
  el.textContent = sec < 0 ? `« ${-skipHint.total} s` : `${skipHint.total} s »`;
  el.style.top = video.offsetTop + video.offsetHeight / 2 + "px"; // the picture's middle, even in portrait
  el.classList.add("on");
  clearTimeout(skipHint.timer);
  skipHint.timer = setTimeout(() => {
    el.classList.remove("on");
    skipHint = { el: null, total: 0, timer: 0 };
  }, 700);
}
/** The subtitle to jump to, as in asbplayer: next is the first one starting after now; previous
 * is the one before the current, or the last one played when between subtitles; current is the
 * one playing (or the last one played). By index, so slightly overlapping lines aren't skipped. */
function adjacentLine(dir) {
  const t = video.currentTime + 0.05;
  if (dir > 0) return lines.find((l) => l.start > t) || null;
  let i = -1;
  while (i + 1 < lines.length && lines[i + 1].start <= t) i++;
  if (i < 0) return null;
  const inside = video.currentTime < lines[i].end || t - lines[i].start < 0.1;
  return lines[dir < 0 && inside ? i - 1 : i] || null;
}
function seekLine(dir) {
  const l = adjacentLine(dir);
  if (!l) return;
  video.currentTime = l.start;
  shownId = undefined;
  renderSubs();
  renderTime();
}
// swipe left / right on the picture: next / previous subtitle
let swipe = null;
stage.addEventListener(
  "touchstart",
  (e) => {
    const onPicture = e.target === video || e.target === stage || $("subs").contains(e.target);
    swipe =
      onPicture && e.touches.length === 1
        ? { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() }
        : null;
  },
  { passive: true },
);
stage.addEventListener(
  "touchend",
  (e) => {
    if (!swipe) return;
    const p = e.changedTouches[0],
      dx = p.clientX - swipe.x,
      dy = p.clientY - swipe.y,
      dt = Date.now() - swipe.t;
    swipe = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy) && dt < 800) {
      swipedAt = Date.now();
      clearTimeout(tapTimer);
      seekLine(dx < 0 ? 1 : -1);
    }
  },
  { passive: true },
);

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
  savePrefs();
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
  if (Date.now() - swipedAt < 500) return; // the end of a swipe that started on a subtitle
  if (!video.paused) {
    userPaused = true;
    video.pause();
  }
});

// ------------------------------------------------------------------ settings menu: speed, subtitle size
const RATES = [0.5, 0.75, 1, 1.25, 1.5];
function setRate(r) {
  speed = r;
  video.defaultPlaybackRate = video.playbackRate = r; // the default survives a reload of the source
  for (const b of $("rates").children) b.classList.toggle("on", +b.dataset.v === r);
  savePrefs();
}
function setSubScale(s) {
  subScale = s;
  $("subs").style.setProperty("--sub-scale", s);
  for (const b of $("sizes").children) b.classList.toggle("on", +b.dataset.v === s);
  savePrefs();
}
function menuOpen() {
  return !$("menu").hidden;
}
function toggleMenu(open = !menuOpen()) {
  $("menu").hidden = !open;
  $("setBtn").classList.toggle("on", open);
  if (open) showControls();
  else hideControlsSoon();
}
$("setBtn").innerHTML = ICON.gear;
$("setBtn").onclick = (e) => {
  e.stopPropagation();
  toggleMenu();
};
$("rates").onclick = (e) => e.target.dataset.v && setRate(+e.target.dataset.v);
$("sizes").onclick = (e) => e.target.dataset.v && setSubScale(+e.target.dataset.v);
document.addEventListener("click", (e) => {
  if (menuOpen() && !$("menu").contains(e.target)) toggleMenu(false);
});
setRate(RATES.includes(speed) ? speed : 1);
setSubScale(subScale);

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
  } else if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "ArrowDown") {
    // previous / next subtitle, start of the current one (asbplayer's keys)
    e.preventDefault();
    seekLine(e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0);
    showControls();
  } else if (e.key === "a" || e.key === "d") {
    skipBy(e.key === "a" ? -5 : 5);
    showControls();
  } else if (e.key === "<" || e.key === ">") {
    // slower / faster, as on YouTube
    const i = RATES.indexOf(speed) + (e.key === ">" ? 1 : -1);
    setRate(RATES[Math.min(RATES.length - 1, Math.max(0, i))]);
    showControls();
  } else if (e.key === "f") isFs() ? exitFs() : enterFs();
  else if (e.key === "Escape" && stage.classList.contains("pseudo-fs")) exitFs();
});

// ------------------------------------------------------------------ playhead reports (priority hints)
// They also save the position the video resumes from, once playback has reached the start point
// (before that, currentTime is still 0 and would overwrite the saved position).
let lastReport = 0;
function playheadBody() {
  const t = positioned ? video.currentTime : startAt || 0;
  return JSON.stringify({ t, save: positioned, asbplayer: true });
}
function reportPlayhead() {
  if (!status || startAt === null) return;
  lastReport = Date.now();
  fetch(`/api/jobs/${vid}/playhead`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: playheadBody(),
  }).catch(() => {});
}
setInterval(() => {
  if (Date.now() - lastReport > 5000) reportPlayhead();
}, 1000);
// closing the tab or leaving the page saves the latest position
addEventListener("pagehide", () => {
  if (positioned)
    navigator.sendBeacon(
      `/api/jobs/${vid}/playhead`,
      new Blob([playheadBody()], { type: "application/json" }),
    );
});

// ------------------------------------------------------------------ offline download
// One tap saves the video (.mp4) and its subtitles (.srt) to watch without the server; see
// download.js.
function mb(bytes) {
  return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(bytes / 1e6)) + " MB";
}
function renderDownload() {
  const b = $("dl");
  b.hidden = status.stage !== "done" || !status.ready;
  if (b.hidden) return;
  b.innerHTML = ICON.dl + "<span></span>";
  b.lastChild.textContent = status.download_size ? `Download · ${mb(status.download_size)}` : "Download";
  b.title = "Saves the video (.mp4) and its Japanese and English subtitles (.srt) to watch offline";
}
$("dl").onclick = () => downloadOffline(vid);

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
