#!/bin/zsh
# VoiceForge — local voice cloning studio
cd "$(dirname "$0")"
exec .venv/bin/python backend/server.py
