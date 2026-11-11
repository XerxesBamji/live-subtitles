#!/bin/zsh
# Start the live translation server. Usage: ./live.sh [--model medium] [--no-partials]
cd "$(dirname "$0")"
PYTHONWARNINGS=ignore exec .venv/bin/python live_server.py "$@"

