#!/bin/zsh
# Usage: ./run.sh "https://www.youtube.com/watch?v=..." [--model medium] [--spanish]
cd "$(dirname "$0")"
exec .venv/bin/python translate.py "$@"

