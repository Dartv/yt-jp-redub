import { $, setOn } from "./dom.js";
import * as gate from "./gate.js";
import * as source from "./source.js";

// The voice switch: Japanese dub or the original English.
function setAudio(lang) {
  setOn($("audJa"), lang === "ja");
  setOn($("audEn"), lang === "en");
  gate.setLang(lang);
  source.setAudio(lang);
  if (lang === "ja") gate.checkCatchUp();
}

export function setupAudio() {
  $("audJa").onclick = () => setAudio("ja");
  $("audEn").onclick = () => setAudio("en");
}
