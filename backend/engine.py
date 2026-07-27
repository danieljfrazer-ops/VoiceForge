"""Local model engine: Qwen3-TTS via MLX (voice cloning) + faster-whisper (STT).

Qwen3-TTS-12Hz-1.7B-Base runs natively on Apple Silicon through mlx-audio.
Cloning needs the reference audio AND its transcript (ref_text) — we produce
the transcript automatically with Whisper when a voice is created.

Models are lazy-loaded on first use and shared across requests. Generation is
serialized with a lock (single GPU pipeline).
"""
import io
import os
import re
import subprocess
import threading

FFMPEG = "ffmpeg"
TTS_MODEL_ID = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"

_state = {
    "tts": None,
    "stt": None,
    "tts_loading": False,
    "stt_loading": False,
    "tts_error": None,
}
_tts_lock = threading.Lock()
_stt_lock = threading.Lock()
_gen_lock = threading.Lock()


def status() -> dict:
    return {
        "tts_loaded": _state["tts"] is not None,
        "tts_loading": _state["tts_loading"],
        "tts_error": _state["tts_error"],
        "stt_loaded": _state["stt"] is not None,
        "stt_loading": _state["stt_loading"],
        "device": "mlx",
        "engine": "Qwen3-TTS",
        "model": TTS_MODEL_ID,
    }


def get_tts():
    with _tts_lock:
        if _state["tts"] is None:
            _state["tts_loading"] = True
            _state["tts_error"] = None
            try:
                from mlx_audio.tts.utils import load_model
                _state["tts"] = load_model(TTS_MODEL_ID)
            except Exception as e:  # surface load errors to the UI
                _state["tts_error"] = f"{type(e).__name__}: {e}"
                raise
            finally:
                _state["tts_loading"] = False
        return _state["tts"]


def get_stt():
    with _stt_lock:
        if _state["stt"] is None:
            _state["stt_loading"] = True
            try:
                from faster_whisper import WhisperModel
                _state["stt"] = WhisperModel("base", device="cpu", compute_type="int8")
            finally:
                _state["stt_loading"] = False
        return _state["stt"]


def preload_async():
    threading.Thread(target=_preload, daemon=True).start()


def _preload():
    try:
        get_tts()
    except Exception:
        pass
    try:
        get_stt()
    except Exception:
        pass


# ---------------------------------------------------------------- audio utils

def convert_to_wav(raw: bytes, out_path: str, sample_rate: int = 24000) -> float:
    """Convert any browser-recorded audio (webm/opus, mp4/aac, wav...) to mono WAV.

    Returns duration in seconds.
    """
    proc = subprocess.run(
        [FFMPEG, "-y", "-i", "pipe:0", "-ac", "1", "-ar", str(sample_rate),
         "-c:a", "pcm_s16le", out_path],
        input=raw, capture_output=True,
    )
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise ValueError("Could not decode the uploaded audio: "
                         + proc.stderr.decode(errors="replace")[-400:])
    size = os.path.getsize(out_path)
    return max(0.0, (size - 44) / (sample_rate * 2))


_SENT_RE = re.compile(r"(?<=[.!?…])\s+|\n+")

def _chunk_text(text: str, max_len: int = 400):
    """Split long text into sentence-aligned chunks for stable generation."""
    sentences = [s.strip() for s in _SENT_RE.split(text.strip()) if s.strip()]
    if not sentences:
        return []
    chunks, current = [], ""
    for sent in sentences:
        while len(sent) > max_len:  # pathological run-on sentence: hard split
            head, sent = sent[:max_len], sent[max_len:]
            if current:
                chunks.append(current)
                current = ""
            chunks.append(head)
        if current and len(current) + len(sent) + 1 > max_len:
            chunks.append(current)
            current = sent
        else:
            current = f"{current} {sent}".strip()
    if current:
        chunks.append(current)
    return chunks


def generate_speech(text: str, prompt_wav_path: str, ref_text: str,
                    config: dict, out_path: str) -> float:
    """Generate cloned speech for `text`, write WAV to out_path, return duration (s)."""
    import numpy as np
    import soundfile as sf

    model = get_tts()
    kwargs = {
        "temperature": float(config.get("temperature", 0.9)),
        "speed": float(config.get("speed", 1.0)),
    }
    chunks = _chunk_text(text)
    if not chunks:
        raise ValueError("No speakable text provided")

    with _gen_lock:
        pieces = []
        sr = 24000
        for i, chunk in enumerate(chunks):
            results = list(model.generate(
                text=chunk,
                ref_audio=prompt_wav_path,
                ref_text=ref_text,
                lang_code="auto",
                **kwargs,
            ))
            if not results:
                raise RuntimeError("Model produced no audio")
            sr = results[0].sample_rate
            pieces.extend(np.asarray(r.audio) for r in results)
            if i < len(chunks) - 1:
                pieces.append(np.zeros(int(sr * 0.25), dtype=np.float32))
        audio = np.concatenate(pieces)
        sf.write(out_path, audio, sr)
        return len(audio) / sr


def transcribe(raw: bytes) -> str:
    model = get_stt()
    segments, _info = model.transcribe(io.BytesIO(raw), beam_size=5, vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe_file(path: str) -> str:
    with open(path, "rb") as f:
        return transcribe(f.read())
