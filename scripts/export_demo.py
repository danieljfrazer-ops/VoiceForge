#!/usr/bin/env python3
"""Export VoiceForge voices into static demo content for the hosted (no-backend) UI.

Converts a voice's reference sample and clips from WAV to MP3 (via the
`ffmpeg` binary — no Python audio deps needed) and writes a `voices.json`
manifest that the hosted frontend reads in place of the `/api/voices*`
endpoints. Each export REPLACES the existing demo set under `--out`.

Examples:
    # See what's available to export (id, name, finalized, sample length, clips)
    python3 scripts/export_demo.py --list

    # Export two voices, by name, into static/demo (the default)
    python3 scripts/export_demo.py "Dan" "Yas"

    # Export by id, at a lower bitrate, without the reference sample audio
    python3 scripts/export_demo.py 1336578d3fd5 --bitrate 96k --no-sample

    # Include preview clips too, and export to a custom directory
    python3 scripts/export_demo.py "Dan" --include-previews --out /tmp/demo-preview
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA_DIR = os.path.join(ROOT, "data", "voices")
DEFAULT_OUT_DIR = os.path.join(ROOT, "static", "demo")
ENGINE_LABEL = "Qwen3-TTS 1.7B Base (MLX)"
MAX_FILE_BYTES = 25 * 1024 * 1024  # Cloudflare Pages per-file limit


# ------------------------------------------------------------------- reading

def _load_meta(voice_dir: str):
    try:
        with open(os.path.join(voice_dir, "meta.json")) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_all_voices(data_dir: str) -> dict:
    voices = {}
    if not os.path.isdir(data_dir):
        return voices
    for vid in sorted(os.listdir(data_dir)):
        vdir = os.path.join(data_dir, vid)
        if not os.path.isdir(vdir):
            continue
        meta = _load_meta(vdir)
        if meta:
            voices[vid] = meta
    return voices


def resolve_voices(requested: list, all_voices: dict) -> list:
    """Resolve CLI tokens (id, or case-insensitive exact name) to voice ids.

    Preserves the order given on the command line; raises SystemExit with a
    clear message for unknown or ambiguous (duplicate-name) tokens.
    """
    by_name = {}
    for vid, meta in all_voices.items():
        by_name.setdefault(meta.get("name", "").strip().lower(), []).append(vid)

    resolved = []
    for token in requested:
        if token in all_voices:
            resolved.append(token)
            continue
        matches = by_name.get(token.strip().lower(), [])
        if len(matches) == 1:
            resolved.append(matches[0])
        elif len(matches) > 1:
            raise SystemExit(
                f"Voice name '{token}' is ambiguous — matches ids: {', '.join(matches)}. "
                "Specify one of those ids instead.")
        else:
            raise SystemExit(f"Unknown voice: '{token}' (not a known voice id or name)")
    return resolved


def print_list(all_voices: dict):
    if not all_voices:
        print("No voices found.")
        return
    rows = [("id", "name", "finalized", "sample (s)", "clips")]
    ordered = sorted(all_voices.items(), key=lambda kv: kv[1].get("created", 0), reverse=True)
    for vid, meta in ordered:
        clips = [c for c in meta.get("clips", []) if c.get("kind") != "preview"]
        rows.append((
            vid,
            meta.get("name", ""),
            str(bool(meta.get("finalized", False))),
            f"{meta.get('sample_duration', 0):.1f}",
            str(len(clips)),
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for i, row in enumerate(rows):
        print("  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)))
        if i == 0:
            print("  ".join("-" * w for w in widths))


# --------------------------------------------------------------- output safety

def _is_under_system_temp(path: str) -> bool:
    """True if `path` is under a system temp directory (allows testing exports
    outside the repo without risking an accidental delete elsewhere)."""
    candidates = {
        tempfile.gettempdir(),
        os.path.realpath(tempfile.gettempdir()),
        "/tmp", "/private/tmp",
        "/var/folders", "/private/var/folders",
    }
    real = os.path.realpath(path)
    for candidate in candidates:
        candidate_real = os.path.realpath(candidate)
        if real == candidate_real or real.startswith(candidate_real + os.sep):
            return True
    return False


def resolve_out_dir(out_dir: str, data_dir: str) -> str:
    resolved = os.path.realpath(out_dir)
    root = os.path.realpath(ROOT)
    inside_repo = resolved == root or resolved.startswith(root + os.sep)
    is_repo_root = resolved == root
    if (not inside_repo or is_repo_root) and not _is_under_system_temp(resolved):
        raise SystemExit(
            f"Refusing to export to {resolved} — it must be inside the repo "
            "(and not the repo root itself), or under a system temp directory.")
    # clean_out_dir wipes <out>/voices — never let that overlap the source voices.
    data = os.path.realpath(data_dir)
    wiped = os.path.join(resolved, "voices")
    if (data == wiped or data.startswith(wiped + os.sep)
            or wiped.startswith(data + os.sep) or resolved == data):
        raise SystemExit(
            f"Refusing to export to {resolved} — it overlaps the source voices in {data}.")
    return resolved


def clean_out_dir(out_dir: str):
    """Replace the existing demo set: only touches <out>/voices/ and <out>/voices.json."""
    voices_dir = os.path.join(out_dir, "voices")
    if os.path.isdir(voices_dir):
        shutil.rmtree(voices_dir)
    voices_json = os.path.join(out_dir, "voices.json")
    if os.path.exists(voices_json):
        os.remove(voices_json)


# --------------------------------------------------------------------- export

def run_ffmpeg(src_wav: str, dst_mp3: str, bitrate: str):
    os.makedirs(os.path.dirname(dst_mp3), exist_ok=True)
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", src_wav,
         "-ac", "1", "-codec:a", "libmp3lame", "-b:a", bitrate, dst_mp3],
        capture_output=True,
    )
    if proc.returncode != 0 or not os.path.exists(dst_mp3):
        raise RuntimeError(
            f"ffmpeg failed converting {src_wav}: "
            f"{proc.stderr.decode(errors='replace')[-400:]}")


def export_voice(vid: str, meta: dict, data_dir: str, out_dir: str, *,
                  include_previews: bool, export_sample: bool, bitrate: str,
                  sizes: list, warnings: list) -> dict:
    src_vdir = os.path.join(data_dir, vid)
    dst_vdir = os.path.join(out_dir, "voices", vid)

    clips = meta.get("clips", [])
    if not include_previews:
        clips = [c for c in clips if c.get("kind") != "preview"]
    clips = sorted(clips, key=lambda c: c.get("created", 0), reverse=True)  # like the API

    sample_url = None
    if export_sample:
        src_sample = os.path.join(src_vdir, "sample.wav")
        if os.path.exists(src_sample):
            dst_sample = os.path.join(dst_vdir, "sample.mp3")
            run_ffmpeg(src_sample, dst_sample, bitrate)
            _track_size(dst_sample, out_dir, sizes, warnings)
            sample_url = f"demo/voices/{vid}/sample.mp3"
        else:
            warnings.append(f"{vid}: sample.wav missing, skipped")

    out_clips = []
    for clip in clips:
        cid = clip["id"]
        src_clip = os.path.join(src_vdir, "clips", f"{cid}.wav")
        if not os.path.exists(src_clip):
            warnings.append(f"{vid}/{cid}: clip audio missing, skipped")
            continue
        dst_clip = os.path.join(dst_vdir, "clips", f"{cid}.mp3")
        run_ffmpeg(src_clip, dst_clip, bitrate)
        _track_size(dst_clip, out_dir, sizes, warnings)
        clip_out = dict(clip)
        clip_out["audio_url"] = f"demo/voices/{vid}/clips/{cid}.mp3"
        out_clips.append(clip_out)

    voice_out = dict(meta)
    voice_out["clips"] = out_clips
    voice_out["sample_url"] = sample_url
    return voice_out


def _track_size(path: str, out_dir: str, sizes: list, warnings: list):
    size = os.path.getsize(path)
    sizes.append(size)
    if size > MAX_FILE_BYTES:
        rel = os.path.relpath(path, out_dir)
        warnings.append(f"{rel} is {size / 1024 / 1024:.1f} MiB "
                        "(> 25 MiB Cloudflare Pages per-file limit)")


# ------------------------------------------------------------------------ cli

def main():
    parser = argparse.ArgumentParser(
        description="Export VoiceForge voices to static/demo (MP3 + voices.json) "
                     "for the hosted, backend-free demo UI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("voices", nargs="*",
                        help="Voice ids or names (case-insensitive exact match) to export")
    parser.add_argument("--data", default=DEFAULT_DATA_DIR,
                        help="Voices data directory (default: data/voices)")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR,
                        help="Output directory (default: static/demo)")
    parser.add_argument("--list", action="store_true",
                        help="List available voices and exit")
    parser.add_argument("--include-previews", action="store_true",
                        help="Include preview clips (excluded by default)")
    parser.add_argument("--no-sample", action="store_true",
                        help="Skip exporting the reference sample recording")
    parser.add_argument("--bitrate", default="128k",
                        help="MP3 bitrate passed to ffmpeg (default: 128k)")
    args = parser.parse_args()

    all_voices = load_all_voices(args.data)

    if args.list:
        print_list(all_voices)
        return

    if not args.voices:
        parser.error("specify one or more voice ids/names to export, or pass --list")

    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required but was not found on PATH (brew install ffmpeg)")

    ids = resolve_voices(args.voices, all_voices)
    out_dir = resolve_out_dir(args.out, args.data)

    os.makedirs(out_dir, exist_ok=True)
    clean_out_dir(out_dir)

    sizes, warnings, voice_objs = [], [], []
    for vid in ids:
        voice_objs.append(export_voice(
            vid, all_voices[vid], args.data, out_dir,
            include_previews=args.include_previews,
            export_sample=not args.no_sample,
            bitrate=args.bitrate,
            sizes=sizes, warnings=warnings,
        ))

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "engine": ENGINE_LABEL,
        "voices": voice_objs,
    }
    with open(os.path.join(out_dir, "voices.json"), "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")

    clip_count = sum(len(v["clips"]) for v in voice_objs)
    total_mib = sum(sizes) / 1024 / 1024
    print(f"\nExported {len(voice_objs)} voice(s), {clip_count} clip(s), "
          f"{len(sizes)} audio file(s), {total_mib:.1f} MiB total -> {out_dir}")
    for w in warnings:
        print(f"  warning: {w}")
    print("\nOnly publish voices you have consent to share.")


if __name__ == "__main__":
    main()
