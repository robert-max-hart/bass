# voicegen

Directed voice lines from [Kokoro](https://github.com/hexgrad/kokoro) TTS. You write a plain-text voice
script with one line per clip, giving a style (trance, deep monotone, shout, soft, whisper), effects (an echo
for thoughts), settings and word stress, and voicegen renders clips plus a manifest. It only re-renders what changed.

```
pip install -e .                 # Python 3.10+, CPU is fine; pins kokoro==0.9.4
voicegen preview "Close your [eyes](+~)..." --voice voices/harris.pt --style "trance 0.6" -o x.ogg
voicegen check  voice_script.txt
voicegen render voice_script.txt --out voice/ --dry-run
voicegen render voice_script.txt --out voice/
```

- The script format, directions and marks are documented in `VOICE_SCRIPT.md` (in the bass repo:
  `tools/VOICE_SCRIPT.md`).
- Voices are Kokoro voice packs (`.pt`, shape [510, 1, 256]) or stock names such as `af_nicole`.
- Output is Ogg Opus by default, with an automatic fallback to `.wav`; `--format wav|flac` is also available.
- Works on Linux. Windows is intended to work but not yet tested: first check that `voicegen preview` writes
  an `.ogg`. On first use the model downloads from Hugging Face; for offline use, pass `--model-dir`
  (a folder with `config.json` and the `.pth`).

Layout: `engine.py` (the model with duration/pitch editing), `styles.py` (styles and how directions
resolve), `marks.py` (word marks), `dsp.py` (loudness, whisper, the `internal` echo), `script.py`
(the voice script), `render.py` (caching and the manifest), `cli.py`.

Bump `ENGINE_VERSION` in `__init__.py` when a change alters the sound in a way the cache key can't see
(for example a change inside `engine.py`). Every clip is then rebuilt on the next render.
