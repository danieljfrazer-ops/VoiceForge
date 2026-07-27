"""Filesystem-backed storage for voices and generated clips.

Layout:
  data/voices/<voice_id>/
      meta.json      -- voice metadata incl. clip index
      sample.wav     -- reference sample used for cloning
      clips/<clip_id>.wav
"""
import json
import os
import shutil
import threading
import time
import uuid

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
VOICES_DIR = os.path.join(DATA_DIR, "voices")

_lock = threading.Lock()

DEFAULT_CONFIG = {
    "temperature": 0.9,    # sampling variability   (0.1 - 1.5)
    "speed": 1.0,          # speaking speed         (0.5 - 1.5)
}


def _voice_dir(voice_id: str) -> str:
    return os.path.join(VOICES_DIR, voice_id)


def _meta_path(voice_id: str) -> str:
    return os.path.join(_voice_dir(voice_id), "meta.json")


def _read_meta(voice_id: str):
    try:
        with open(_meta_path(voice_id), "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_meta(voice_id: str, meta: dict):
    path = _meta_path(voice_id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, path)


def list_voices():
    os.makedirs(VOICES_DIR, exist_ok=True)
    voices = []
    for vid in os.listdir(VOICES_DIR):
        meta = _read_meta(vid)
        if meta:
            voices.append(meta)
    voices.sort(key=lambda v: v.get("created", 0), reverse=True)
    return voices


def get_voice(voice_id: str):
    return _read_meta(voice_id)


def create_voice(name: str, sample_duration: float) -> dict:
    voice_id = uuid.uuid4().hex[:12]
    vdir = _voice_dir(voice_id)
    os.makedirs(os.path.join(vdir, "clips"), exist_ok=True)
    meta = {
        "id": voice_id,
        "name": name,
        "created": time.time(),
        "config": dict(DEFAULT_CONFIG),
        "sample_duration": sample_duration,
        "ref_text": "",
        "finalized": False,
        "clips": [],
    }
    _write_meta(voice_id, meta)
    return meta


def update_voice(voice_id: str, *, name=None, config=None, finalized=None, ref_text=None):
    with _lock:
        meta = _read_meta(voice_id)
        if not meta:
            return None
        if name is not None:
            meta["name"] = name
        if config is not None:
            cfg = meta.get("config", dict(DEFAULT_CONFIG))
            for key in DEFAULT_CONFIG:
                if key in config:
                    cfg[key] = float(config[key])
            meta["config"] = cfg
        if finalized is not None:
            meta["finalized"] = bool(finalized)
        if ref_text is not None:
            meta["ref_text"] = str(ref_text)
        _write_meta(voice_id, meta)
        return meta


def delete_voice(voice_id: str) -> bool:
    vdir = _voice_dir(voice_id)
    if not os.path.isdir(vdir):
        return False
    shutil.rmtree(vdir, ignore_errors=True)
    return True


def sample_path(voice_id: str) -> str:
    return os.path.join(_voice_dir(voice_id), "sample.wav")


def clip_path(voice_id: str, clip_id: str) -> str:
    return os.path.join(_voice_dir(voice_id), "clips", f"{clip_id}.wav")


def add_clip(voice_id: str, text: str, duration: float, config: dict, kind: str = "clip") -> dict:
    with _lock:
        meta = _read_meta(voice_id)
        if not meta:
            return None
        clip = {
            "id": uuid.uuid4().hex[:12],
            "voice_id": voice_id,
            "text": text,
            "duration": duration,
            "created": time.time(),
            "config": config,
            "kind": kind,  # "clip" | "preview"
        }
        meta.setdefault("clips", []).insert(0, clip)
        _write_meta(voice_id, meta)
        return clip


def delete_clip(voice_id: str, clip_id: str) -> bool:
    with _lock:
        meta = _read_meta(voice_id)
        if not meta:
            return False
        before = len(meta.get("clips", []))
        meta["clips"] = [c for c in meta.get("clips", []) if c["id"] != clip_id]
        if len(meta["clips"]) == before:
            return False
        _write_meta(voice_id, meta)
    try:
        os.remove(clip_path(voice_id, clip_id))
    except OSError:
        pass
    return True


def find_clip(voice_id: str, clip_id: str):
    meta = _read_meta(voice_id)
    if not meta:
        return None
    for clip in meta.get("clips", []):
        if clip["id"] == clip_id:
            return clip
    return None
