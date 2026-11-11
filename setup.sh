#!/bin/zsh
# One-command setup on a new Apple Silicon Mac:
#   git clone <your repo url> ~/Documents/translatoryoutube
#   cd ~/Documents/translatoryoutube && ./setup.sh
set -e
cd "$(dirname "$0")"

if [[ "$(uname -m)" != "arm64" ]]; then
  echo "❌ This needs an Apple Silicon Mac (M1/M2/M3/M4)."
  exit 1
fi

if ! xcode-select -p >/dev/null 2>&1; then
  echo "📦 Installing Apple's Command Line Tools – click Install in the window that pops up."
  xcode-select --install || true
  echo "👉 When that finishes, run ./setup.sh again."
  exit 1
fi

echo "🐍 Creating Python environment…"
[[ -d .venv ]] || /usr/bin/python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
echo "📦 Installing packages (about 1 GB, a few minutes)…"
.venv/bin/pip install -q -r requirements.txt
.venv/bin/pip install -q --no-deps resemblyzer==0.1.4

echo "🧠 Downloading the speech model (Medium, ~1.4 GB)…"
PYTHONWARNINGS=ignore .venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/whisper-medium-mlx')" >/dev/null

echo "🔨 Building the app…"
./build_app.sh

cat <<'EOF'

✅ Done! Next steps:
  1. Open "Live Subtitles" from Spotlight (Cmd+Space).
  2. Chrome: go to chrome://extensions → turn on Developer mode → Load unpacked →
     choose the "extension" folder in this project. Pin it.
  3. Safari / other apps: in the app window pick what to listen to and press Start.
     macOS will ask for Screen Recording permission the first time – allow it,
     then quit and reopen the app.
EOF

