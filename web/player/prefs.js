// The viewer's choices, kept in this browser between videos.
const KEY = "ytjp-prefs";
export const prefs = { ja: true, en: false, rate: 1, size: 1, vol: 1, muted: false };
try {
  Object.assign(prefs, JSON.parse(localStorage.getItem(KEY) || "{}"));
} catch (e) {}

export function savePrefs(changes) {
  Object.assign(prefs, changes);
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs));
  } catch (e) {}
}
