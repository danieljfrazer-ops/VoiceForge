#!/bin/zsh
# VoiceForge — local voice cloning studio
cd "$(dirname "$0")"

# MLX (Qwen3-TTS) only runs on Apple Silicon.
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "VoiceForge requires an Apple Silicon Mac (MLX needs Metal) — this machine is $(uname -s)/$(uname -m)." >&2
  exit 1
fi

# First run: create the venv with uv. Deliberately uv-managed rather than
# pyenv's 3.12 — pyenv's build is missing the _lzma module that some deps need.
if [[ ! -x .venv/bin/python ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to set up VoiceForge's Python environment." >&2
    echo "  brew install uv" >&2
    exit 1
  fi
  echo "Setting up .venv (first run)…"
  uv venv --python 3.12 .venv || exit 1
  uv pip install --python .venv/bin/python -r requirements.txt || exit 1
fi

# External tools used at runtime.
missing=()
command -v sox >/dev/null 2>&1 || missing+=(sox)
command -v ffmpeg >/dev/null 2>&1 || missing+=(ffmpeg)
if (( ${#missing[@]} )); then
  echo "Missing required tool(s): ${missing[*]}" >&2
  echo "  brew install sox ffmpeg" >&2
  exit 1
fi

exec .venv/bin/python backend/server.py
