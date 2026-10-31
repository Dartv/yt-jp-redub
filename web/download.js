"use strict";
/** One tap saves everything needed offline: the .mp4 and its Japanese and English .srt files
 * (asbplayer takes subtitles as separate files). The server packages the .mp4 as it's requested,
 * in a second or two. Used by the library and the player. */
function downloadOffline(vid) {
  const m = `/media/${vid}`;
  [`${m}/download.mp4`, `${m}/download.ja.srt`, `${m}/download.en.srt`].forEach((url, i) =>
    // spaced out: browsers may drop downloads started in the same instant
    setTimeout(() => {
      const a = document.createElement("a");
      a.href = url;
      a.download = "";
      document.body.appendChild(a);
      a.click();
      a.remove();
    }, i * 400),
  );
}
