"""HLS VOD packaging.

- Video: fMP4 segments cut from source.mp4 with -c copy (keyframe aligned), done once after download.
- Audio: one packed-AAC (ADTS + ID3 timestamp) segment per dub chunk, for both `ja` and `en`.
  Chunk boundaries fall in silences, so per-segment encoder priming is inaudible.
  Every segment is listed up front so the whole timeline is seekable from the start.
"""

from __future__ import annotations

import struct
from pathlib import Path

from ..models import LinesDoc, VideoPaths
from . import ff

AAC_PRIMING = 1024  # samples the ffmpeg AAC encoder prepends


def prepare_video(paths: VideoPaths, seg_sec: int) -> None:
    vdir = paths.hls / "video"
    playlist = paths.video_playlist
    if playlist.exists():
        return
    vdir.mkdir(parents=True, exist_ok=True)
    # run inside video/ so the init segment and fragments land there (ffmpeg resolves
    # -hls_fmp4_init_filename against its working directory)
    ff.run_ffmpeg(
        [
            "-i",
            str(paths.video.resolve()),
            "-map",
            "0:v:0",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-f",
            "hls",
            "-hls_time",
            str(seg_sec),
            "-hls_playlist_type",
            "vod",
            "-hls_segment_type",
            "fmp4",
            "-hls_fmp4_init_filename",
            "init.mp4",
            "-hls_segment_filename",
            "%05d.m4s",
            "-hls_flags",
            "independent_segments",
            "index.m3u8",
        ],
        cwd=vdir,
    )
    fixed = []
    for line in (vdir / "index.m3u8").read_text(encoding="utf-8").splitlines():
        if line.startswith("#EXT-X-MAP:"):
            line = '#EXT-X-MAP:URI="video/init.mp4"'
        elif line and not line.startswith("#"):
            line = "video/" + line.strip()
        fixed.append(line)
    tmp = paths.hls / "video.tmp.m3u8"
    tmp.write_text("\n".join(fixed) + "\n", encoding="utf-8")
    tmp.replace(playlist)
    (vdir / "index.m3u8").unlink()


def _id3_timestamp(pts90k: int) -> bytes:
    """ID3v2.4 tag with the Apple HLS transportStreamTimestamp PRIV frame (33-bit PTS @ 90 kHz)."""
    owner = b"com.apple.streaming.transportStreamTimestamp\x00"
    payload = owner + struct.pack(">Q", pts90k & ((1 << 33) - 1))
    frame = b"PRIV" + _syncsafe(len(payload)) + b"\x00\x00" + payload
    return b"ID3\x04\x00\x00" + _syncsafe(len(frame)) + frame


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def encode_segment(wav: Path, out: Path, start_sec: float, bitrate: str = "160k", sr: int = ff.SR) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.aac")
    ff.run_ffmpeg(
        ["-i", str(wav), "-c:a", "aac", "-b:a", bitrate, "-ar", str(sr), "-ac", "2", "-f", "adts", str(tmp)]
    )
    pts = max(0, round((start_sec - AAC_PRIMING / sr) * 90000))
    with open(out.with_suffix(".part"), "wb") as f:
        f.write(_id3_timestamp(pts))
        f.write(tmp.read_bytes())
    tmp.unlink()
    out.with_suffix(".part").replace(out)


def encode_en_segments(paths: VideoPaths, doc: LinesDoc) -> None:
    """Original-audio rendition, cut on the same chunk boundaries as the dub."""
    tmp = paths.hls / "_en_tmp.wav"
    for c in doc.chunks:
        out = paths.seg("en", c.id)
        if out.exists():
            continue
        data, sr = ff.read_slice(paths.audio_wav, c.start, c.end)
        ff.write_wav(tmp, data, sr)
        encode_segment(tmp, out, c.start)
    tmp.unlink(missing_ok=True)


def audio_playlist(doc: LinesDoc, lang: str) -> str:
    target = max(1, int(max((c.duration for c in doc.chunks), default=1) + 0.999))
    out = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
    ]
    for c in doc.chunks:
        out += [f"#EXTINF:{c.duration:.3f},", f"{lang}/{c.id:04d}.aac"]
    out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n"


def _avc_codec(stream: dict) -> str:
    prof = {"Baseline": 0x42, "Constrained Baseline": 0x42, "Main": 0x4D, "High": 0x64}.get(
        stream.get("profile"), 0x64
    )
    constraint = 0x40 if stream.get("profile") == "Constrained Baseline" else 0
    return f"avc1.{prof:02x}{constraint:02x}{int(stream.get('level', 40)):02x}"


def master_playlist(paths: VideoPaths, langs: tuple[str, ...] = ("ja", "en")) -> str:
    v = ff.stream(paths.video, "video")
    codecs = f"{_avc_codec(v)},mp4a.40.2" if v.get("codec_name") == "h264" else "mp4a.40.2"
    names = {"ja": "日本語", "en": "English"}
    out = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for i, lang in enumerate(langs):
        out.append(
            f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="{names[lang]}",LANGUAGE="{lang}",'
            f'DEFAULT={"YES" if i == 0 else "NO"},AUTOSELECT=YES,CHANNELS="2",URI="audio_{lang}.m3u8"'
        )
    bw = int(float(v.get("bit_rate") or 4_000_000)) + 200_000
    res = f",RESOLUTION={v['width']}x{v['height']}" if v.get("width") else ""
    out.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},CODECS="{codecs}"{res},AUDIO="aud"')
    out.append("video.m3u8")
    return "\n".join(out) + "\n"
