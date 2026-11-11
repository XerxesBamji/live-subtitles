#!/usr/bin/env python3
"""
Live Spanish -> English subtitle server.

The Chrome extension (./extension) captures the audio of the tab you're watching
and streams it here as 16 kHz mono float32 PCM over a WebSocket. This server
chops the audio at natural pauses, runs Whisper locally (MLX, Apple GPU) with
task="translate", and sends English text back to be drawn over the video.

Messages sent to the extension (JSON):
  {"type": "partial", "text": "..."}   # in-progress guess for the current sentence
  {"type": "final",   "text": "..."}   # settled line
  {"type": "status",  "text": "..."}

Usage:
  ./live.sh                    # default: medium (~0.8s per line on an M4)
  ./live.sh --model large      # slightly better translation, ~2x slower
  ./live.sh --model small      # fastest (~0.3s), rougher translation
  ./live.sh --no-partials      # only show settled lines (less GPU work)
"""
import argparse
import asyncio
import difflib
import os
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import websockets
import mlx_whisper

try:
    from resemblyzer import VoiceEncoder  # voiceprints for telling speakers apart
except ImportError:
    VoiceEncoder = None

try:
    import webrtcvad  # proper speech detector; copes with audience/background noise
except ImportError:
    webrtcvad = None

SR = 16000
VAD_FRAME = 480  # 30 ms at 16 kHz

MODELS = {
    # "turbo" models aren't trained for translation, so we use the full ones.
    "large": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
}

# Classic Whisper hallucinations on music / silence.
BANNED = re.compile(
    r"(thanks? (you )?for watching|subscribe|subtitles? by|amara\.org|"
    r"see you in the next|transcribed by|translated by)",
    re.I,
)


class Transcriber:
    """Runs every MLX call on one dedicated thread (MLX streams are per-thread)."""

    def __init__(self, repo: str, max_tokens: int = 80):
        self.repo = repo
        self.max_tokens = max_tokens
        self.executor = ThreadPoolExecutor(max_workers=1)

    def _run(self, audio: np.ndarray, prompt: str = None) -> str:
        result = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self.repo,
            language="es",
            task="translate",
            temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=prompt or None,  # previous line(s) for continuity
            sample_len=self.max_tokens,     # stop runaway/hallucinated output early
            verbose=None,
        )
        out = []
        for seg in result["segments"]:
            if seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -1.0:
                continue
            if seg.get("compression_ratio", 0) > 2.4:
                continue
            text = seg["text"].strip()
            if text and not BANNED.search(text):
                out.append(text)
        return " ".join(out)

    async def __call__(self, audio: np.ndarray, prompt: str = None) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self._run, audio, prompt)


LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live.log")


def log(msg: str):
    with open(LOG_PATH, "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def is_repeat(text: str, history) -> bool:
    """Whisper sometimes just echoes the context prompt back; catch near-duplicates."""
    t = text.lower().strip(" .…")
    for h in history[-2:]:
        h = h.lower().strip(" .…")
        if t in h or difflib.SequenceMatcher(None, t, h).ratio() > 0.8:
            return True
    return False


class SpeakerTracker:
    """Online speaker clustering: each line's voiceprint is matched to the
    closest known voice, or starts a new speaker if nothing is similar enough."""

    def __init__(self, threshold: float, max_speakers: int):
        self.encoder = VoiceEncoder("cpu", verbose=False)
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.threshold = threshold
        self.max_speakers = max_speakers
        self.encoder.embed_utterance(np.zeros(SR, np.float32))  # warm-up (JIT)
        self.reset()

    def reset(self):
        self.centroids, self.counts = [], []

    def _embed(self, audio):
        if len(audio) < int(0.8 * SR):
            return None  # too short to judge reliably
        return self.encoder.embed_utterance(audio)

    def _match(self, emb):
        if not self.centroids:
            return None, 0.0
        sims = [float(c @ emb) for c in self.centroids]
        i = int(np.argmax(sims))
        return i, sims[i]

    async def assign(self, audio, update=True):
        """Return (speaker_id or None, similarity)."""
        loop = asyncio.get_running_loop()
        emb = await loop.run_in_executor(self.executor, self._embed, audio)
        if emb is None:
            return None, 0.0
        i, sim = self._match(emb)
        if i is not None and (sim >= self.threshold or len(self.centroids) >= self.max_speakers):
            if update:
                n = min(self.counts[i], 20)  # keep adapting to the voice
                c = self.centroids[i] * n + emb
                self.centroids[i] = c / np.linalg.norm(c)
                self.counts[i] += 1
            return i, sim
        if not update:
            return None, sim
        if len(audio) < int(1.5 * SR):
            # Too short to trust as a brand-new voice (often laughter/noise):
            # attribute it to the closest known speaker, or leave it unknown.
            return i, sim
        self.centroids.append(emb / np.linalg.norm(emb))
        self.counts.append(1)
        return len(self.centroids) - 1, sim


class Session:
    def __init__(self, ws, tr: Transcriber, args, speakers=None):
        self.ws, self.tr, self.a = ws, tr, args
        self.t_start = time.monotonic()
        self.received = 0.0         # seconds of audio received from the browser
        self.buf = np.zeros(0, np.float32)
        self.silence = 0.0          # seconds of trailing non-speech
        self.heard_speech = False   # does buf contain anything worth transcribing?
        self.last_partial = 0.0
        self.infer_time = 0.5       # rolling estimate of how long Whisper takes
        self.history = []           # recent English lines, used as context
        self.speakers = speakers
        self.last_speaker = None
        if speakers:
            speakers.reset()
        self.vad = webrtcvad.Vad(args.vad) if webrtcvad else None
        self.vad_rem = np.zeros(0, np.float32)
        self.closed = False

    def _is_speech(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(frame ** 2)))
        if rms < self.a.silence:
            return False
        if self.vad is None:
            return True
        pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16).tobytes()
        return self.vad.is_speech(pcm, SR)

    def feed(self, chunk: np.ndarray):
        if not len(chunk):
            return
        self.received += len(chunk) / SR
        self.buf = np.concatenate([self.buf, chunk])
        frames = np.concatenate([self.vad_rem, chunk])
        n = len(frames) // VAD_FRAME * VAD_FRAME
        for i in range(0, n, VAD_FRAME):
            if self._is_speech(frames[i:i + VAD_FRAME]):
                self.silence = 0.0
                self.heard_speech = True
            else:
                self.silence += VAD_FRAME / SR
        self.vad_rem = frames[n:]
        # Nothing but silence so far: keep only a short lead-in.
        if not self.heard_speech and len(self.buf) > SR:
            self.buf = self.buf[-SR // 2:]

    def quiet_cut(self) -> int:
        """Index of the quietest 100 ms in the last 1 s (avoid cutting mid-word)."""
        w, n = SR // 10, len(self.buf)
        best, best_i = None, n
        for i in range(max(0, n - SR), n - w + 1, w // 2):
            e = float(np.mean(self.buf[i:i + w] ** 2))
            if best is None or e < best:
                best, best_i = e, i + w // 2
        return best_i

    def prompt(self) -> str:
        if not self.a.context or not self.history:
            return None
        return " ".join(self.history)[-200:]

    async def run(self, audio):
        t0 = time.monotonic()
        text = await self.tr(audio, self.prompt())
        dt = time.monotonic() - t0
        self.infer_time = 0.7 * self.infer_time + 0.3 * dt
        return text, dt

    async def send(self, kind: str, text: str, speaker=None):
        msg = {"type": kind, "text": text}
        if speaker is not None:
            msg["speaker"] = speaker
        await self.ws.send(json.dumps(msg))

    async def run_with_speaker(self, audio, update=True):
        """Transcribe and identify the speaker at the same time."""
        if not self.speakers:
            text, dt = await self.run(audio)
            return text, dt, None, 0.0
        (text, dt), (spk, sim) = await asyncio.gather(
            self.run(audio), self.speakers.assign(audio, update))
        return text, dt, spk, sim

    async def loop(self):
        try:
            while not self.closed:
                await asyncio.sleep(0.05)
                if not self.heard_speech:
                    continue
                dur = len(self.buf) / SR
                cut = None
                if dur >= self.a.min_chunk and self.silence >= self.a.pause:
                    cut = len(self.buf)
                elif dur >= self.a.max_chunk:
                    cut = self.quiet_cut()

                if cut is not None:
                    reason = "pause" if cut == len(self.buf) else "max"
                    audio, self.buf = self.buf[:cut], self.buf[cut:]
                    self.heard_speech = len(self.buf) > SR // 10
                    # Stay live: if we've fallen behind, drop the oldest audio
                    # instead of letting the delay snowball.
                    limit = int(self.a.max_chunk * SR)
                    skipped = 0.0
                    if len(audio) > limit:
                        skipped = (len(audio) - limit) / SR
                        print(f"  (behind - skipped {skipped:.1f}s of audio)", flush=True)
                        audio = audio[-limit:]
                    text, dt, spk, sim = await self.run_with_speaker(audio)
                    if spk is None:
                        spk = self.last_speaker  # too short to tell: assume same person
                    raw = text
                    if text and is_repeat(text, self.history):
                        text = ""  # echo of the previous line
                    if text:
                        self.history = (self.history + [text])[-3:]
                        self.last_speaker = spk
                        who = f"S{spk + 1}: " if spk is not None else ""
                        print(f"[{dt:4.1f}s] {who}{text}", flush=True)
                    wall = time.monotonic() - self.t_start
                    log(f"FINAL audio={len(audio) / SR:.1f}s infer={dt:.2f}s "
                        f"reason={reason} "
                        f"recv={self.received:.1f}s wall={wall:.1f}s drift={wall - self.received:.1f}s "
                        f"backlog={len(self.buf) / SR:.1f}s skipped={skipped:.1f} "
                        f"speaker={spk} sim={sim:.2f} "
                        f"text={raw!r}{' (DROPPED as repeat)' if raw and not text else ''}")
                    await self.send("final", text, spk)  # empty final clears the partial
                    self.last_partial = time.monotonic()
                elif self.a.partials and 0.6 <= dur < self.a.max_chunk - self.infer_time:
                    # Don't let partials hog the GPU / delay the real line.
                    every = max(self.a.partial_every, 1.5 * self.infer_time)
                    if time.monotonic() - self.last_partial >= every:
                        text, dt, spk, _ = await self.run_with_speaker(self.buf.copy(), update=False)
                        log(f"partial audio={dur:.1f}s infer={dt:.2f}s speaker={spk} text={text!r}")
                        self.last_partial = time.monotonic()
                        if text:
                            await self.send("partial", text, spk)
        except websockets.ConnectionClosed:
            pass


async def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", choices=MODELS, default="medium")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--silence", type=float, default=0.005, help="RMS below this counts as silence")
    p.add_argument("--pause", type=float, default=0.25, help="seconds of non-speech that end a line")
    p.add_argument("--min-chunk", type=float, default=0.8, help="min seconds of audio per line")
    p.add_argument("--max-chunk", type=float, default=3.0, help="force a line after this many seconds")
    p.add_argument("--partial-every", type=float, default=0.7)
    p.add_argument("--max-tokens", type=int, default=80, help="max tokens per line")
    p.add_argument("--vad", type=int, default=2, choices=[0, 1, 2, 3], help="speech detector strictness")
    p.add_argument("--no-context", dest="context", action="store_false",
                   help="don't feed the previous line to Whisper")
    p.add_argument("--no-partials", dest="partials", action="store_false")
    p.add_argument("--no-speakers", dest="speakers", action="store_false",
                   help="don't colour subtitles by speaker")
    p.add_argument("--speaker-threshold", type=float, default=0.72,
                   help="voice similarity needed to count as the same person (0-1)")
    p.add_argument("--max-speakers", type=int, default=6)
    p.add_argument("--exit-with-parent", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args()

    if args.exit_with_parent:
        # Launched by the menu bar app: quit if it goes away.
        parent = os.getppid()

        async def watchdog():
            while True:
                await asyncio.sleep(2)
                if os.getppid() != parent:
                    os._exit(0)
        asyncio.get_running_loop().create_task(watchdog())

    tr = Transcriber(MODELS[args.model], args.max_tokens)
    print(f"Loading Whisper '{args.model}' (first run downloads it)...", flush=True)
    await tr(np.zeros(SR, np.float32))  # warm-up so the first caption is fast
    speakers = None
    if args.speakers and VoiceEncoder is not None:
        print("Loading speaker detection...", flush=True)
        speakers = SpeakerTracker(args.speaker_threshold, args.max_speakers)

    async def handler(ws):
        print("Browser connected.", flush=True)
        open(LOG_PATH, "w").write(f"model={args.model} max_chunk={args.max_chunk} pause={args.pause}\n")
        s = Session(ws, tr, args, speakers)
        task = asyncio.create_task(s.loop())
        try:
            await s.send("status", "Listening…")
            async for msg in ws:
                if isinstance(msg, bytes):
                    s.feed(np.frombuffer(msg, dtype=np.float32))
        except websockets.ConnectionClosed:
            pass
        finally:
            s.closed = True
            task.cancel()
            print("Browser disconnected.", flush=True)

    async with websockets.serve(handler, "localhost", args.port, max_size=None):
        print(f"Ready on ws://localhost:{args.port} — click the extension icon on your YouTube tab.", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

