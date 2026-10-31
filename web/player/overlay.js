import { $ } from "./dom.js";

// The message over the picture: waiting, ready to play, or failed.
export function overlay(title, text, { spin = true, play = false } = {}) {
  $("overlay").hidden = false;
  $("ovTitle").textContent = title;
  $("ovText").textContent = text || "";
  $("spin").hidden = !spin;
  $("ovPlay").hidden = !play;
}
export function hideOverlay() {
  $("overlay").hidden = true;
}
