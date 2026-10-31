import { $, video } from "./dom.js";
import { media, status, vid } from "./job.js";
import { JSON_TYPE } from "../common.js";

// asbplayer, the subtitle reader. PC: the server pushes subtitles over asbplayer's WebSocket client;
// the button (shown once the subtitles are complete) re-sends them. Android: asbplayer discovers
// <track> elements and asks the page via its event protocol.
let tracksAdded = false;

function trackList() {
  return ["ja", "en"].map((l) => ({
    id: `${vid}-${l}`,
    label: l === "ja" ? "日本語" : "English",
    language: l,
    url: new URL(`${media}/subs/${l}.vtt`, location.href).href,
    extension: "vtt",
  }));
}
/** Adds the <track> elements once the subtitles are complete. */
export function addTracks(s) {
  if (tracksAdded || !s.subs_complete) return;
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

export function setupAsbplayer() {
  $("asbBtn").onclick = async () => {
    let clients = 0;
    try {
      const r = await fetch(`/api/jobs/${vid}/asbplayer`, { method: "POST", headers: JSON_TYPE });
      if (r.ok) ({ clients } = await r.json());
    } catch (e) {}
    $("asbBtn").textContent = clients ? "Sent to asbplayer ✓" : "asbplayer isn't connected";
  };
  video.addEventListener("asbplayer-get-synced-data", (e) => {
    e.stopPropagation();
    onSyncRequest();
  });
  document.addEventListener("asbplayer-get-synced-data", (e) => {
    if (e.target === document) onSyncRequest();
  });
}
