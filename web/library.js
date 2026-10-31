import { downloadOffline, errorMessage, errorText } from "./common.js";

// The cards and their delete/retry buttons go through htmx (see index.html); this is what htmx
// doesn't do: alerting failed actions, the download button, dubbing a pasted link, and settings.
const grid = document.getElementById("grid");
// a redraw drops hover and can swallow a click, so an unchanged poll isn't swapped in
let shown = null;
grid.addEventListener("htmx:beforeSwap", (e) => {
  if (e.detail.target !== grid) return;
  if (e.detail.serverResponse === shown) e.detail.shouldSwap = false;
  else shown = e.detail.serverResponse;
});
// a failed poll stays quiet (the server is restarting); a failed action says why
document.body.addEventListener("htmx:responseError", (e) => {
  if (e.detail.requestConfig.verb !== "get") alert(errorMessage(e.detail.xhr.responseText, e.detail.xhr.status));
});
document.body.addEventListener("htmx:sendError", (e) => {
  if (e.detail.requestConfig.verb !== "get") alert("The server isn't reachable");
});
grid.addEventListener("click", (e) => {
  const dl = e.target.closest("button.dl");
  if (dl) downloadOffline(dl.closest(".card").dataset.id);
});

// the form posts through htmx, and the server sends the browser on to the player; a pasted link
// skips the form's check, as the server's is the one that counts (it takes links without https://)
const urlInput = document.getElementById("url");
function dub(url) {
  urlInput.value = url;
  htmx.ajax("POST", "/api/jobs", { values: { url }, swap: "none" });
}
// pasting a YouTube video or Twitch VOD/clip link dubs it right away (the server has the exact rules)
const VIDEO_LINK =
  /(youtube\.com|youtu\.be)\/|twitch\.tv\/(videos\/|[^/?#]+\/(clip|v)\/|\?(.*&)?video=)|clips\.twitch\.tv\//;
urlInput.addEventListener("paste", (e) => {
  const text = e.clipboardData.getData("text").trim();
  if (!VIDEO_LINK.test(text)) return;
  e.preventDefault();
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
    if (VIDEO_LINK.test(text)) return dub(text);
  } catch (e) {}
  urlInput.focus();
});

// settings: saved into config.toml and .env, editable only on the PC (the server checks too)
const settings = document.getElementById("settings"),
  sform = document.getElementById("settingsForm"),
  serror = document.getElementById("settingsError"),
  KEY_MASK = "••••••••••••"; // stands for the saved key, which the browser never gets
document.getElementById("gear").addEventListener("click", async () => {
  const r = await fetch("/api/settings", { cache: "no-store" }).catch(() => null);
  if (!r || !r.ok) return alert(r ? await errorText(r) : "The server isn't reachable");
  const s = await r.json();
  for (const el of sform.elements) {
    if (el.name) el.value = el.name === "llm_api_key" ? (s.llm_api_key ? KEY_MASK : "") : s[el.name];
    el.setCustomValidity?.("");
  }
  document.getElementById("fields").disabled = !s.editable;
  document.getElementById("readonly").hidden = s.editable;
  document.getElementById("saveSettings").hidden = !s.editable;
  serror.textContent = "";
  settings.showModal();
});
sform.addEventListener("input", (e) => e.target.setCustomValidity?.(""));
// typing a new key replaces the mask rather than adding to it
sform.elements.llm_api_key.addEventListener("focus", (e) => e.target.value === KEY_MASK && e.target.select());
sform.addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return; // Close (or Escape) just closes it
  e.preventDefault();
  const values = Object.fromEntries(new FormData(sform));
  if (values.llm_api_key === KEY_MASK) values.llm_api_key = ""; // empty keeps the saved key
  const r = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(values),
  }).catch(() => null);
  if (!r || !r.ok) {
    const err = r ? await r.clone().json().catch(async () => ({ error: await errorText(r) })) : {};
    const field = err.field && sform.elements[err.field];
    if (field) {
      field.setCustomValidity(err.error);
      field.reportValidity();
    } else serror.textContent = err.error || "The server isn't reachable";
    return;
  }
  settings.close();
  if ((await r.json()).restart) alert("Saved. Restart yt-jp-redub to use the new port.");
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
