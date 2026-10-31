import { $, ICON, stage, touchUI } from "./dom.js";

// Fullscreen, with a fill-the-window fallback where the browser has none (iPhone Safari).
export function isFs() {
  return (
    document.fullscreenElement === stage ||
    document.webkitFullscreenElement === stage ||
    stage.classList.contains("pseudo-fs")
  );
}
function setPseudoFs(on) {
  stage.classList.toggle("pseudo-fs", on);
  document.body.classList.toggle("has-pseudo-fs", on);
}
function unlockOrientation() {
  try {
    screen.orientation.unlock();
  } catch (e) {}
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
  // no element fullscreen (iPhone Safari, refused, or never answered): fill the window instead
  if (!ok && !document.fullscreenElement && !document.webkitFullscreenElement) setPseudoFs(true);
  try {
    await screen.orientation.lock("landscape");
  } catch (e) {}
  renderFsIcon();
}
export async function exitFs() {
  if (stage.classList.contains("pseudo-fs")) setPseudoFs(false);
  else if (document.fullscreenElement) await document.exitFullscreen().catch(() => {});
  else if (document.webkitFullscreenElement) document.webkitExitFullscreen();
  unlockOrientation();
  renderFsIcon();
}
export function toggleFs() {
  isFs() ? exitFs() : enterFs();
}
function renderFsIcon() {
  $("fsBtn").innerHTML = isFs() ? ICON.unfs : ICON.fs;
}

/** playing(): playback has started, so turning a phone sideways goes fullscreen, the way video
 * apps do. */
export function setupFullscreen({ playing }) {
  $("fsBtn").onclick = (e) => {
    e.stopPropagation();
    toggleFs();
  };
  document.addEventListener("fullscreenchange", () => {
    if (document.fullscreenElement) setPseudoFs(false);
    else unlockOrientation();
    renderFsIcon();
  });
  matchMedia("(orientation: landscape)").addEventListener("change", (ev) => {
    if (touchUI && ev.matches && !isFs() && playing()) enterFs();
  });
  renderFsIcon();
}
