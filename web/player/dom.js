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
  gear: '<svg viewBox="0 0 24 24"><path d="M19.14 12.94a7.1 7.1 0 0 0 0-1.88l2.03-1.58a.5.5 0 0 0 .12-.61l-1.92-3.32a.5.5 0 0 0-.59-.22l-2.39.96a7 7 0 0 0-1.62-.94l-.36-2.54a.48.48 0 0 0-.48-.41h-3.84a.48.48 0 0 0-.47.41l-.36 2.54a7.3 7.3 0 0 0-1.62.94l-2.39-.96a.5.5 0 0 0-.59.22L2.74 8.87a.5.5 0 0 0 .12.61l2.03 1.58a7.1 7.1 0 0 0 0 1.88l-2.03 1.58a.5.5 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54a7 7 0 0 0 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32a.5.5 0 0 0-.12-.61zM12 15.6a3.6 3.6 0 1 1 0-7.2 3.6 3.6 0 0 1 0 7.2z"/></svg>',
};

/** A toggle button's state, shown and announced. */
export function setOn(button, on) {
  button.classList.toggle("on", on);
  button.setAttribute("aria-pressed", on);
}
