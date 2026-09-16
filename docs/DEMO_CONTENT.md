# Making demo content

The hosted demo at [voiceforge.pages.dev](https://voiceforge.pages.dev) is a static site with no
backend — it plays back clips you generate locally and export with
`scripts/export_demo.py`. This is a step-by-step guide to producing that content.

## 1. Start the app

```bash
./run.sh
```

Open http://localhost:7860.

## 2. Create a voice

- **New voice** → give it a name → record 10–30 seconds of natural speech (or upload a clean
  audio file). Use a quiet room and a natural, relaxed pace — the reference sample is what
  Qwen3-TTS clones from, so quality in is quality out.
- Only do this for voices you have consent to record and publish. See **Responsible use** in the
  main README.

## 3. Generate 3–5 clips that show range

Aim for clips that show off different aspects of the voice, not five similar lines. Some example
prompts to adapt:

- **A short intro:** "Hi, I'm [name] — welcome to my voice clone."
- **A question with intonation:** "Wait, you actually finished it already?"
- **An expressive / emotional line:** "I can't believe we actually pulled this off!"
- **A longer narration paragraph:** two or three sentences of a story or description, to show
  pacing and stamina over a longer passage.
- **Something numbers/names-heavy:** a sentence with dates, prices, or proper nouns, since these
  are often where TTS models slip up.

Regenerate and delete bad takes directly in the app until you're happy with the set — only the
clips left in the voice's clip list get exported.

## 4. Check what's available to export

```bash
python3 scripts/export_demo.py --list
```

Prints a table of every voice (id, name, finalized, sample length, clip count) so you can confirm
names/ids before exporting.

## 5. Export

```bash
python3 scripts/export_demo.py "Dan" "Other voice"
```

Voices can be given by name (case-insensitive) or id. This converts the reference sample and
clips to MP3 and writes `static/demo/voices/` and `static/demo/voices.json`.

Useful flags:
- `--no-sample` — skip exporting the reference recording, just clips
- `--include-previews` — include the auto-generated "this is how I sound" preview clip (excluded
  by default)
- `--bitrate 96k` — smaller files, lower quality
- `--out <dir>` — export somewhere other than `static/demo` (e.g. to preview before committing)

**Re-running the export replaces the whole demo set** — anything previously in `static/demo/voices/`
and `static/demo/voices.json` is removed first, and only the voices you pass on the command line
are re-exported.

## 6. Preview the static site

```bash
python3 -m http.server 8788 --directory static
```

Open http://localhost:8788. With no backend running, it auto-detects there's nothing at
`/api/status` and falls back to demo mode using `static/demo/voices.json`. (You can also force
demo mode by setting `mode: "demo"` in `static/config.js`.)

## 7. Publish

```bash
git add static/demo
git commit -m "Update demo content"
git push
```

Pushing triggers a Cloudflare Pages redeploy (if you've set up the Git integration — see the main
README's deploy section).
