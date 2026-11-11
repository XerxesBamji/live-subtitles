# Live Subtitles (Spanish → English, runs on your Mac)

Translates what people *say* in a video into English subtitles live, using
Whisper running locally on your Mac's Apple Silicon chip. It doesn't need any
existing subtitles, and nothing is uploaded anywhere.

- **Chrome:** subtitles appear on the YouTube video itself (fullscreen too).
- **Safari or any other app:** subtitles appear in a floating bar above everything.
- **Speakers:** each person gets their own colour, and a leading "–" marks a change of speaker.

> Requires an **Apple Silicon Mac** (M1/M2/M3/M4).

---

## 1. Install on a Mac

Open **Terminal** and run:

```bash
git clone https://github.com/XerxesBamji/live-subtitles.git ~/Documents/translatoryoutube
cd ~/Documents/translatoryoutube && ./setup.sh
```

- **Sign-in:** the repository is private, so `git clone` asks you to sign in to
  GitHub. If it asks for a password, use a **personal access token**, not your
  account password. Create one on GitHub under Settings → Developer settings →
  Personal access tokens.
- **Apple's developer tools:** if they aren't installed, `setup.sh` opens their
  installer. Click **Install**, wait for it to finish, then run `./setup.sh` again.
- **What `setup.sh` does:**
  - installs everything (about 1 GB)
  - downloads the Medium speech model (about 1.4 GB)
  - builds the **Live Subtitles** app in `~/Applications`

## 2. Open the app

Open **Live Subtitles** from Spotlight (Cmd+Space). A small window opens and
the translator starts in the background.

| Status | Meaning |
|---|---|
| 🟠 Loading | The speech model is loading (a few seconds) |
| 🟢 Ready | Waiting for audio from Chrome or Safari |
| 🔴 Translating | Subtitles are being produced |

**Using the window:**
- **Close button (×):** hides the window. The translator keeps running.
- **Dock icon:** click it to bring the window back.
- **Quit (or Cmd+Q):** stops everything.

## 3a. Watching in Chrome (subtitles on the video)

One-time setup:
1. Go to `chrome://extensions` and turn on **Developer mode** (top right).
2. Click **Load unpacked** and choose the `extension` folder inside this project.
3. Click the puzzle-piece icon in Chrome's toolbar and **pin** "Live English Subtitles".

Each time:
1. Make sure the Live Subtitles app is open.
2. On the YouTube tab, click the extension button. It shows a red **ON** badge.
3. Click it again to stop.

## 3b. Watching in Safari or any app (floating subtitles)

1. In the app window, choose **Listen to: Safari** (or Google Chrome, or All system audio).
2. Press **Start**.
3. **First time only:** macOS asks for **Screen Recording** permission.
   1. Allow the app in System Settings → Privacy & Security → *Screen & System Audio Recording*. It may be listed as "Python".
   2. Quit the app (Cmd+Q), reopen it, and press **Start** again.
4. **Position the subtitle bar:**
   1. Untick **Lock subtitle position**.
   2. Drag the bar over the bottom of your video. Resize it from the edges.
   3. Tick **Lock** again. Once locked, it's click-through and remembers its position.

The floating bar also shows over fullscreen video.

## 4. Settings in the app window

- **Model:**
  - *Small:* fastest, rougher translation
  - *Medium:* balanced (recommended)
  - *Large:* most accurate, about 2× slower
- **Open at login:** starts the app automatically when you log in.
- **Restart / Open Log:** use these if something seems stuck.

---

## Updating

On any Mac, run this in the project folder:

```bash
cd ~/Documents/translatoryoutube
git pull && ./build_app.sh
```

Then reopen the app. If the Chrome extension changed:
1. Click **↻** on it at `chrome://extensions`.
2. Refresh the YouTube tab.

## Troubleshooting

| Problem | Fix |
|---|---|
| Subtitles are several seconds behind | Switch the model to **Medium** or **Small** |
| Nothing appears in Chrome | Make sure the app shows 🟢/🔴. Click ↻ on the extension at `chrome://extensions`, refresh the tab, then click the extension button again |
| Nothing appears for Safari | Check Screen Recording permission (step 3b), then quit and reopen the app. Make sure the subtitle position is **locked** |
| "Port in use" | Something else is running the translator (e.g. `./live.sh` in Terminal). Close it, then press **Restart** |
| Two people get the same colour, or one person switches colours | Voices that sound alike can merge. Adjust `--speaker-threshold` in `live_server.py` (default 0.72; higher means more speakers are told apart) |
| Moved the project folder | Run `./build_app.sh` again |

**Logs (in the project folder):**
- `server_output.log`: what the translator heard and translated
- `live.log`: timing for each line
- `desktop_errors.log`: problems with the floating subtitles

---

## How it works

```
Chrome tab audio ─(extension)─┐
                              ├─► live_server.py (Whisper + speaker detection) ─► English captions
Safari / app audio ─(app)─────┘         on localhost:8765                          │
                                                                                   ├─► overlay on the video (Chrome)
                                                                                   └─► floating bar (Safari / any app)
```

## Files

| File | What it is |
|---|---|
| `app.py` | The desktop app (control window, Dock icon). Starts the translator |
| `live_server.py` | The translator: Whisper translation, pause detection, speaker colours |
| `desktop_capture.py` | Safari/app audio capture and the floating subtitle bar |
| `extension/` | Chrome extension: tab audio capture and the overlay on the video |
| `setup.sh` | One-command install on a new Mac |
| `build_app.sh` | Builds `~/Applications/Live Subtitles.app` |
| `live.sh` | Runs the translator in Terminal instead of the app (for debugging) |
| `translate.py` / `run.sh` | Offline mode: download a video and make an `.srt` file (needs `brew install ffmpeg`) |

Advanced translator options: `.venv/bin/python live_server.py --help`
