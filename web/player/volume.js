import { $, ICON, video } from "./dom.js";
import { prefs, savePrefs } from "./prefs.js";

// Volume and mute (PC; phones use their buttons).
function setVolume(v, mute = prefs.muted) {
  const vol = Math.min(1, Math.max(0, v)),
    muted = mute || vol === 0;
  video.volume = vol;
  video.muted = muted;
  $("vol").value = muted ? 0 : vol;
  $("volBtn").innerHTML = muted ? ICON.mute : ICON.vol;
  $("volBtn").setAttribute("aria-label", muted ? "Unmute" : "Mute");
  savePrefs({ vol, muted });
}
export function toggleMute() {
  setVolume(prefs.muted && prefs.vol === 0 ? 1 : prefs.vol, !prefs.muted);
}

export function setupVolume() {
  $("volBtn").onclick = toggleMute;
  $("vol").oninput = () => setVolume(+$("vol").value, false);
  setVolume(prefs.vol);
}
