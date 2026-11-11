#!/usr/bin/env python3
"""
Download a YouTube video, listen to the Spanish audio with Whisper (runs locally
on your Mac's GPU via MLX), and produce English subtitles.

Output (in ./output/):
  <title>.mp4        original video
  <title>.en.srt     English subtitles
  <title>.en.mp4     video with the English subtitles embedded (toggle in QuickTime/VLC/IINA)

Usage:
  ./run.sh "https://www.youtube.com/watch?v=..."
  ./run.sh URL --model medium        # faster, slightly less accurate
  ./run.sh URL --spanish             # also write a Spanish transcript (.es.srt)
"""
import argparse
import subprocess
import sys
from pathlib import Path

import mlx_whisper
import yt_dlp

MODELS = {
    # Note: the "turbo" models were not trained for translation, so we use full models.
    "large": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
}

OUT_DIR = Path(__file__).parent / "output"


def download(url: str) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    opts = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(OUT_DIR / "%(title).150B.%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    print(f"\nDownloaded: {path}")
    return path


def fmt_ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments, path: Path):
    lines, n = [], 0
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        n += 1
        lines += [str(n), f"{fmt_ts(seg['start'])} --> {fmt_ts(seg['end'])}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {n} subtitles -> {path}")


def transcribe(video: Path, model: str, task: str):
    print(f"\nRunning Whisper ({model}, task={task}) - first run downloads the model...")
    result = mlx_whisper.transcribe(
        str(video),
        path_or_hf_repo=MODELS[model],
        language="es",
        task=task,
        verbose=False,  # shows a progress bar
        condition_on_previous_text=False,  # reduces repetition/hallucination loops
    )
    return result["segments"]


def embed(video: Path, srt: Path) -> Path:
    out = video.with_name(video.stem + ".en.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(srt),
         "-map", "0", "-map", "1", "-c", "copy", "-c:s", "mov_text",
         "-metadata:s:s:0", "language=eng", "-disposition:s:0", "default", str(out)],
        check=True,
    )
    print(f"Video with embedded subtitles -> {out}")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", help="YouTube URL or path to a local video/audio file")
    p.add_argument("--model", choices=MODELS, default="large")
    p.add_argument("--spanish", action="store_true", help="also write a Spanish transcript")
    p.add_argument("--no-embed", action="store_true", help="don't create the .en.mp4 copy")
    args = p.parse_args()

    src = Path(args.source)
    video = src if src.exists() else download(args.source)

    en_srt = video.with_suffix(".en.srt")
    write_srt(transcribe(video, args.model, "translate"), en_srt)

    if args.spanish:
        write_srt(transcribe(video, args.model, "transcribe"), video.with_suffix(".es.srt"))

    if not args.no_embed and video.suffix.lower() in {".mp4", ".mov", ".m4v"}:
        out = embed(video, en_srt)
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(out)])


if __name__ == "__main__":
    main()

