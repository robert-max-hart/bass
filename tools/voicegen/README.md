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
voicegen mixer  --voices-dir voices/ --script voice_script.txt     # design voices in the browser
```

## Designing voices: `voicegen mixer`

A local page (127.0.0.1 only) for blending and tuning voice packs by ear. A pack's first 128 numbers set the
**timbre** (what the voice sounds like) and the last 128 the **delivery** (intonation, pacing, energy), and
the mixer sets each half on its own:

- **Blend**: any stock or own voices with weights. Weights are divided by their sum, so a negative weight
  pushes away from a voice (`julia 1.2, af_nicole -0.2`).
- **Adjust**: sliders along `gender`, `accent` and the eight principal axes of the 43 stock voices, in
  standard deviations. A mark on each slider shows where the current voice sits.
- **Variations**: six random nudges along the principal axes; keep the best and repeat with a smaller step.
- **Preview** through the full engine, with any direction (`trance 0.6`, `whisper`, ...) or a line picked from
  `--script`; every take is kept so you can replay or restore it, and *Compare with* plays another voice.
- **Save** writes `<name>.pt` plus `<name>.recipe.json` (the blend and adjustments) to `--save-dir` (default:
  the first `--voices-dir`). Replacing a file keeps the old one as `.bak-<time>`.

Loading a voice uses its `.recipe.json` if there is one; otherwise the mixer works out the stock blend it was
made from (exact for plain blends), or uses the file as it is.

```
voicegen voice recipe voices/julia.pt --write        # recover a .pt's stock blend into julia.recipe.json
voicegen voice build voices/julia.recipe.json -o j.pt  # rebuild a .pt from a recipe
```

## Notes


- The script format, directions and marks are documented in `VOICE_SCRIPT.md` (in the bass repo:
  `tools/VOICE_SCRIPT.md`).
- Voices are Kokoro voice packs (`.pt`, shape [510, 1, 256]) or stock names such as `af_nicole`.
- Output is Ogg Opus by default, with an automatic fallback to `.wav`; `--format wav|flac` is also available.
- Works on Linux. Windows is intended to work but not yet tested: first check that `voicegen preview` writes
  an `.ogg`. On first use the model downloads from Hugging Face; for offline use, pass `--model-dir`
  (a folder with `config.json` and the `.pth`).

Layout: `engine.py` (the model with duration/pitch editing), `styles.py` (styles and how directions
resolve), `marks.py` (word marks), `dsp.py` (loudness, whisper, the `internal` echo), `script.py`
(the voice script), `render.py` (caching and the manifest), `voicelab.py` (recipes and axes), `mixer.py` +
`mixer.html` (the voice mixer), `cli.py`.

Bump `ENGINE_VERSION` in `__init__.py` when a change alters the sound in a way the cache key can't see
(for example a change inside `engine.py`). Every clip is then rebuilt on the next render.
