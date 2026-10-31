import { video } from "./dom.js";

// The video being watched and its job status, polled from the server. Other modules subscribe to
// the status instead of being called from here, so a new view needs no change to this file.
export const vid = location.pathname.split("/").pop();
export const media = `/media/${vid}`;
export const params = new URLSearchParams(location.search);
export let status = null;
export let info = { head_start_sec: 120 };

const listeners = [];
/** Calls fn with each new status, in the order subscribed. */
export function onStatus(fn) {
  listeners.push(fn);
}
export function duration() {
  return (status && status.duration) || video.duration || 0;
}

async function poll() {
  try {
    let s = null;
    try {
      const r = await fetch(`/api/jobs/${vid}`, { cache: "no-store" });
      if (r.ok) s = await r.json();
    } catch (e) {} // the server is restarting
    if (s) {
      status = s;
      for (const fn of listeners) fn(s);
    }
  } finally {
    setTimeout(poll, status && status.stage === "done" ? 10000 : 2000);
  }
}
export function startPolling() {
  fetch("/api/info")
    .then((r) => r.json())
    .then((i) => {
      info = i;
    })
    .finally(poll);
}
