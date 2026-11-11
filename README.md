# Live Subtitles (Spanish → English, runs on your Mac)

Translates what people *say* in a video into English subtitles live, using
Whisper running locally on Apple Silicon. No existing subtitles needed; nothing
is uploaded anywhere.

## Setup (Apple Silicon Mac)

```bash
git clone <your repo url> ~/Documents/translatoryoutube
cd ~/Documents/translatoryoutube
./setup.sh
```

Then open **Live Subtitles** from Spotlight.

## Two ways to watch

| | How | Subtitles appear |
|---|---|---|
| **Chrome** | Load `extension/` at `chrome://extensions` (Developer mode → Load unpacked), then click its button on the YouTube tab | On the video itself (also fullscreen) |
| **Safari / any app** | In the app window choose *Listen to* → **Start** (needs Screen Recording permission once) | In a floating bar above everything; untick *Lock* to drag/resize it |

Each speaker gets their own colour; a leading "–" marks a change of speaker.

## Files

- `app.py` – the desktop app (control window, Dock icon); starts the translator
- `live_server.py` – the translator (Whisper + speaker detection), WebSocket on `localhost:8765`
- `desktop_capture.py` – Safari/app audio capture + floating subtitle window
- `extension/` – Chrome extension (tab audio capture + on-video overlay)
- `build_app.sh` – builds `~/Applications/Live Subtitles.app` (re-run if you move the folder)
- `translate.py` / `run.sh` – offline mode: download a video and make an `.srt` (needs `brew install ffmpeg`)

## Tuning

Model choice is in the app window (Small = fastest, Medium = balanced, Large = most accurate).
Advanced options: `.venv/bin/python live_server.py --help`.

