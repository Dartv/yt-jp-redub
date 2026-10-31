// The player page, put together. Each module does one job; this is the only place that knows them
// all, and the order the job status reaches them in.
import { addTracks, setupAsbplayer } from "./asbplayer.js";
import { setupAudio } from "./audio.js";
import { setupControls } from "./controls.js";
import { video } from "./dom.js";
import { setupFullscreen } from "./fullscreen.js";
import * as gate from "./gate.js";
import { setupGestures } from "./gestures.js";
import { media, onStatus, startPolling } from "./job.js";
import { setupKeyboard } from "./keyboard.js";
import { setupMenu } from "./menu.js";
import { resolveStart, setupPlayhead } from "./playhead.js";
import { renderDubbed, setupSeekbar } from "./seekbar.js";
import { setupStatusView, showStatus } from "./status-view.js";
import { setupSubtitles, updateLines } from "./subtitles.js";
import { setupVolume } from "./volume.js";

video.poster = `${media}/thumb.jpg`; // behind the waiting overlay

gate.setupGate();
setupPlayhead({ viewerPaused: () => gate.started && gate.userPaused });
setupControls();
setupSeekbar();
setupGestures();
setupSubtitles();
setupAudio();
setupVolume();
setupMenu();
setupFullscreen({ playing: () => gate.started });
setupKeyboard();
setupStatusView();
setupAsbplayer();

onStatus(resolveStart); // first: the rest start from where it resolves to
onStatus(showStatus);
onStatus(renderDubbed);
onStatus(updateLines);
onStatus(addTracks);
onStatus(gate.onStatus); // last: it may start playback

startPolling();
