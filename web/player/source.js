import { video } from "./dom.js";
import { media, params } from "./job.js";
import { overlay } from "./overlay.js";

// Where the picture and sound come from. Two ways to play HLS, behind one interface:
// { setAudio(lang), stop() }. The rest of the player doesn't know which one it has.
const variantMode = params.get("mode") === "variants"; // one audio track per source, as on Safari
const single = (lang) => `${media}/master.m3u8?audio=${lang}`;

let current = null;
export let unsupported = false; // the browser can play HLS neither natively nor through hls.js

/** Loads the other voice's source and carries on from the same spot. `load(resume)` must call
 * resume once the new source is ready. */
function reloadAt(load) {
  const t = video.currentTime,
    playing = !video.paused;
  load(() => {
    video.currentTime = t;
    if (playing) video.play().catch(() => {});
  });
}

/** hls.js (Chrome, Firefox, Android): both voices in one source, switched in place. */
function hlsJs(startAt, lang, onReady) {
  const Hls = window.Hls;
  const retry = { maxNumRetry: 1000, retryDelayMs: 1000, maxRetryDelayMs: 4000 };
  const hls = new Hls({
    maxBufferLength: 60,
    maxMaxBufferLength: 120,
    startPosition: startAt,
    fragLoadPolicy: {
      default: { maxTimeToFirstByteMs: 30000, maxLoadTimeMs: 60000, timeoutRetry: retry, errorRetry: retry },
    },
  });
  const pickTrack = () => {
    const idx = hls.audioTracks.findIndex((t) => t.lang === lang);
    if (idx >= 0 && hls.audioTrack !== idx) hls.audioTrack = idx;
  };
  hls.on(Hls.Events.MANIFEST_PARSED, onReady);
  if (!variantMode) hls.on(Hls.Events.AUDIO_TRACKS_UPDATED, pickTrack);
  hls.on(Hls.Events.ERROR, (_, d) => {
    if (!d.fatal) return;
    if (d.type === Hls.ErrorTypes.NETWORK_ERROR) setTimeout(() => hls.startLoad(), 2000);
    else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
    else console.error(d);
  });
  hls.loadSource(variantMode ? single(lang) : `${media}/master.m3u8`);
  hls.attachMedia(video);
  return {
    setAudio(l) {
      lang = l;
      if (!variantMode) return pickTrack();
      reloadAt((resume) => {
        hls.loadSource(single(l));
        hls.once(Hls.Events.MANIFEST_PARSED, resume);
      });
    },
    stop: () => hls.stopLoad(),
  };
}

/** Native HLS (Safari): one voice per source, switched by reloading. */
function native(startAt, lang, onReady) {
  video.src = single(lang);
  video.addEventListener(
    "loadedmetadata",
    () => {
      if (startAt) video.currentTime = startAt;
      onReady();
    },
    { once: true },
  );
  return {
    setAudio: (l) =>
      reloadAt((resume) => {
        video.src = single(l);
        video.addEventListener("loadedmetadata", resume, { once: true });
      }),
    stop() {},
  };
}

export function loaded() {
  return current !== null;
}
/** Starts playing from startAt in the given voice; onReady runs once it can play. */
export function start(startAt, lang, onReady) {
  if (window.Hls && Hls.isSupported()) current = hlsJs(startAt, lang, onReady);
  else if (video.canPlayType("application/vnd.apple.mpegurl")) current = native(startAt, lang, onReady);
  else {
    unsupported = true;
    overlay("This browser can't play HLS", "", { spin: false });
  }
}
export function setAudio(lang) {
  current?.setAudio(lang);
}
export function stop() {
  current?.stop();
}
