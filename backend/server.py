"""VoiceForge — local voice cloning studio.

FastAPI app serving the REST API + static frontend. Runs plain HTTP for
desktop and HTTPS (self-signed) so iPhones on the LAN get mic access.
"""
import asyncio
import os
import socket
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import engine
import storage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(ROOT, "static")
CERT_DIR = os.path.join(ROOT, "certs")

HTTP_PORT = 7860
HTTPS_PORT = 7861

MIN_SAMPLE_SECONDS = 4.0
MAX_TEXT_CHARS = 3000

app = FastAPI(title="VoiceForge")


# --------------------------------------------------------------------- status

@app.get("/api/status")
def api_status():
    return engine.status()


# --------------------------------------------------------------------- voices

@app.get("/api/voices")
def api_voices():
    return storage.list_voices()


@app.post("/api/voices")
async def api_create_voice(name: str = Form(...), audio: UploadFile = File(...)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "Voice name is required")
    raw = await audio.read()
    if len(raw) < 1000:
        raise HTTPException(400, "Audio sample is empty or too short")
    voice = storage.create_voice(name, 0.0)
    try:
        duration = await run_in_threadpool(
            engine.convert_to_wav, raw, storage.sample_path(voice["id"]))
    except ValueError as e:
        storage.delete_voice(voice["id"])
        raise HTTPException(400, str(e))
    if duration < MIN_SAMPLE_SECONDS:
        storage.delete_voice(voice["id"])
        raise HTTPException(400,
            f"Sample is {duration:.1f}s — record at least {MIN_SAMPLE_SECONDS:.0f} seconds")
    voice["sample_duration"] = round(duration, 2)
    # Qwen3-TTS cloning needs the reference transcript — produce it locally
    try:
        voice["ref_text"] = await run_in_threadpool(
            engine.transcribe_file, storage.sample_path(voice["id"]))
    except Exception:
        voice["ref_text"] = ""
    if not voice["ref_text"]:
        storage.delete_voice(voice["id"])
        raise HTTPException(400,
            "Couldn't hear any speech in that sample — try recording again closer to the mic")
    storage._write_meta(voice["id"], voice)
    return voice


@app.get("/api/voices/{voice_id}")
def api_get_voice(voice_id: str):
    voice = storage.get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "Voice not found")
    return voice


class VoicePatch(BaseModel):
    name: str | None = None
    config: dict | None = None
    finalized: bool | None = None


@app.patch("/api/voices/{voice_id}")
def api_update_voice(voice_id: str, patch: VoicePatch):
    voice = storage.update_voice(
        voice_id, name=patch.name, config=patch.config, finalized=patch.finalized)
    if not voice:
        raise HTTPException(404, "Voice not found")
    return voice


@app.delete("/api/voices/{voice_id}")
def api_delete_voice(voice_id: str):
    if not storage.delete_voice(voice_id):
        raise HTTPException(404, "Voice not found")
    return {"ok": True}


@app.get("/api/voices/{voice_id}/sample")
def api_voice_sample(voice_id: str):
    path = storage.sample_path(voice_id)
    if not os.path.exists(path):
        raise HTTPException(404, "Sample not found")
    return FileResponse(path, media_type="audio/wav", filename="sample.wav")


# ----------------------------------------------------------------- generation

class GenerateBody(BaseModel):
    text: str
    kind: str = "clip"          # "clip" | "preview"
    config: dict | None = None  # per-request overrides


@app.post("/api/voices/{voice_id}/generate")
async def api_generate(voice_id: str, body: GenerateBody):
    voice = storage.get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "Voice not found")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Text is required")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(400, f"Text too long (max {MAX_TEXT_CHARS} characters)")

    config = dict(voice.get("config", {}))
    if body.config:
        for key in storage.DEFAULT_CONFIG:
            if key in body.config:
                config[key] = float(body.config[key])

    # voices created before the Qwen engine lack a stored transcript — backfill
    ref_text = voice.get("ref_text", "")
    if not ref_text:
        try:
            ref_text = await run_in_threadpool(
                engine.transcribe_file, storage.sample_path(voice_id))
        except Exception:
            ref_text = ""
        if not ref_text:
            raise HTTPException(400, "Could not transcribe this voice's sample — re-create the voice")
        storage.update_voice(voice_id, ref_text=ref_text)

    clip = storage.add_clip(voice_id, text, 0.0, config, kind=body.kind)
    out_path = storage.clip_path(voice_id, clip["id"])
    try:
        duration = await run_in_threadpool(
            engine.generate_speech, text, storage.sample_path(voice_id), ref_text,
            config, out_path)
    except Exception as e:
        storage.delete_clip(voice_id, clip["id"])
        raise HTTPException(500, f"Generation failed: {e}")

    clip["duration"] = round(duration, 2)
    # persist the measured duration
    meta = storage.get_voice(voice_id)
    for c in meta.get("clips", []):
        if c["id"] == clip["id"]:
            c["duration"] = clip["duration"]
    storage._write_meta(voice_id, meta)
    return clip


@app.get("/api/voices/{voice_id}/clips")
def api_clips(voice_id: str):
    voice = storage.get_voice(voice_id)
    if not voice:
        raise HTTPException(404, "Voice not found")
    return voice.get("clips", [])


@app.get("/api/voices/{voice_id}/clips/{clip_id}/audio")
def api_clip_audio(voice_id: str, clip_id: str):
    path = storage.clip_path(voice_id, clip_id)
    if not os.path.exists(path):
        raise HTTPException(404, "Clip not found")
    return FileResponse(path, media_type="audio/wav", filename=f"{clip_id}.wav")


@app.delete("/api/voices/{voice_id}/clips/{clip_id}")
def api_delete_clip(voice_id: str, clip_id: str):
    if not storage.delete_clip(voice_id, clip_id):
        raise HTTPException(404, "Clip not found")
    return {"ok": True}


# -------------------------------------------------------------- transcription

@app.post("/api/transcribe")
async def api_transcribe(audio: UploadFile = File(...)):
    raw = await audio.read()
    if len(raw) < 1000:
        raise HTTPException(400, "No audio captured")
    try:
        text = await run_in_threadpool(engine.transcribe, raw)
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")
    return {"text": text}


# ------------------------------------------------------------------- frontend

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


@app.exception_handler(404)
async def not_found(request, exc):
    if request.url.path.startswith("/api/"):
        detail = getattr(exc, "detail", "Not found")
        return JSONResponse({"detail": detail}, status_code=404)
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ------------------------------------------------------------------ bootstrap

def ensure_certs():
    cert = os.path.join(CERT_DIR, "cert.pem")
    key = os.path.join(CERT_DIR, "key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key
    os.makedirs(CERT_DIR, exist_ok=True)
    ip = lan_ip()
    san = f"subjectAltName=DNS:localhost,DNS:*.local,IP:127.0.0.1,IP:{ip}"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-days", "3650",
         "-nodes", "-keyout", key, "-out", cert,
         "-subj", "/CN=VoiceForge", "-addext", san],
        check=True, capture_output=True)
    return cert, key


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


async def serve():
    import uvicorn
    cert, key = ensure_certs()
    engine.preload_async()

    http_cfg = uvicorn.Config(app, host="0.0.0.0", port=HTTP_PORT, log_level="info")
    https_cfg = uvicorn.Config(app, host="0.0.0.0", port=HTTPS_PORT,
                               ssl_certfile=cert, ssl_keyfile=key, log_level="warning")
    ip = lan_ip()
    print(f"\n  VoiceForge is running:")
    print(f"    Desktop:  http://localhost:{HTTP_PORT}")
    print(f"    iPhone:   https://{ip}:{HTTPS_PORT}   (accept the certificate warning)\n")
    await asyncio.gather(
        uvicorn.Server(http_cfg).serve(),
        uvicorn.Server(https_cfg).serve(),
    )


if __name__ == "__main__":
    asyncio.run(serve())
