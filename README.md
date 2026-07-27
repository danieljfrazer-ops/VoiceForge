# 🎙️ VoiceForge

A polished, mobile-friendly web app for cloning voices and generating speech — running **100% locally** on your Mac. No cloud, no API keys; your voice never leaves your machine.

- **TTS / voice cloning:** [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) 1.7B Base running natively on Apple Silicon via [mlx-audio](https://github.com/Blaizzy/mlx-audio)
- **Speech-to-text dictation:** faster-whisper (base model, CPU)
- **Backend:** FastAPI · **Frontend:** vanilla JS single-page app

## Run it

```bash
./run.sh
```

Then open:

| Where | URL |
|---|---|
| Desktop Chrome | http://localhost:7860 |
| iPhone (same Wi-Fi) | https://YOUR-MAC-IP:7861 |

The exact iPhone URL (with your Mac's LAN IP) is printed in the terminal on startup.

### iPhone notes
- iOS requires **HTTPS** for microphone access, so use the `https://…:7861` address.
- The certificate is self-signed: Safari will warn — tap **Show Details → visit this website**. One-time only.
- Add to Home Screen for a full-screen app feel.

## Using the app

1. **New voice** → name it → record 10–30 s of natural speech (or upload an audio file) → optionally fine-tune → **Finalise**.
2. Type text (or tap the **mic** to dictate — transcribed locally by Whisper) → **Generate speech**.
3. Clips are saved per voice — play, download (`.wav`), or delete them any time.
4. Switch voices with the chips at the top; **Fine-tune** adjusts variability and speaking speed per clip or as saved defaults.

## Storage

Everything lives in `data/voices/<id>/` — the reference sample, config and generated clips. Delete a folder (or use the app) to remove a voice.

## First run

The Qwen3-TTS MLX weights (~3.4 GB) and Whisper base model download from Hugging Face on first load and are cached in `~/.cache/huggingface`. Subsequent starts are fast; the model loads in the background at startup.

## Responsible use

Only clone voices you have permission to clone (yours, or with the speaker's consent). Voice samples are transcribed locally by Whisper (Qwen3-TTS cloning needs the reference transcript). Generated audio previously carried Resemble's inaudible Perth watermark; Qwen3-TTS output is unwatermarked, so use it responsibly.
