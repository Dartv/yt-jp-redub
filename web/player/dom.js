// The page's elements and the bits of markup the player draws.
export const $ = (id) => document.getElementById(id);
export const video = $("video"),
  stage = $("stage");
export const touchUI = matchMedia("(hover: none)").matches;

export const ICON = {
  play: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13l11-6.5z"/></svg>',
  pause: '<svg viewBox="0 0 24 24"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z"/></svg>',
  fs: '<svg viewBox="0 0 24 24"><path d="M5 9V5h4v2H7v2zm10-4h4v4h-2V7h-2zM5 15h2v2h2v2H5zm12 0h2v4h-4v-2h2z"/></svg>',
  unfs: '<svg viewBox="0 0 24 24"><path d="M9 5v4H5V7h2V5zm6 0h2v2h2v2h-4zM5 15h4v4H7v-2H5zm10 0h4v2h-2v2h-2z"/></svg>',
  vol: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9zm12.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z"/></svg>',
  mute: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9zm16.6 3 2-2-1.4-1.4-2 2-2-2-1.4 1.4 2 2-2 2 1.4 1.4 2-2 2 2 1.4-1.4z"/></svg>',
};

/** A toggle button's state, shown and announced. */
export function setOn(button, on) {
  button.classList.toggle("on", on);
  button.setAttribute("aria-pressed", on);
}
