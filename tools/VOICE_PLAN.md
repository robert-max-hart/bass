# Voice lines: plan

Status (2026-11-12): **steps 1–4 done; step 5 (direct + build chapter 1) next**. The script format as built is documented in `tools/VOICE_SCRIPT.md` (it supersedes the sketch in section 4); the standalone/bass split is in section 0. Tone-control research is done and approved. The reference
engine is `tools/voice_prototype.py`. Reference clips are in `stories/voices/reference/`; rebuild them with
`~/venv/kokoro/bin/python -I tools/voice_prototype.py stories/voices/reference`.

Goal: every dialog event in the optometrist stories gets a voice clip: Julia, Doctor Harris, or the
narrator. Tone is directed per line (trance, monotone, shout, whisper, …) in a file you can read and edit by hand.

---

## 0. Two layers: a standalone tool plus a bass adapter (decided 2026-11-12)

The voice generation itself should be reusable in other games and run on **Windows as well as Linux**.
So the code is split:

- **`tools/voicegen/`**: a standalone package that **imports nothing from bass**. It holds the engine,
  styles, effects, word-mark parser, voice-script parser and the `render` command
  (script + cast → audio files + a manifest `id → file, hash`). It has its own `pyproject.toml` with
  pinned versions and a `voicegen` console command, and can later move to its own repo unchanged.
  It is installed into the venv in editable mode (`pip install -e tools/voicegen --no-deps`).
- **`tools/voice_lines.py`**: the bass adapter. It does `sync` (story JSON + `.odt` → voice script,
  matching lines by text, stress from the manuscript), calls voicegen to build, and writes `voice_line`
  into the story. Another game writes its own small adapter.

Rules that come from the split:
- The voice script format is the public contract. Every line has an **explicit, stable ID** (any string);
  voicegen names and caches clips by that ID. Matching lines by text is the adapter's job.
- Voices are just `.pt` files (or Kokoro stock names). `@cast` paths resolve relative to the script file or
  a `--voices` directory; nothing is hardcoded to `stories/voices`. **Replacing a voice = dropping in a new
  `.pt`**; the cache key includes the file's hash, so its lines rebuild.
- Cross-platform: every text file opened with `encoding='utf-8'`; `pathlib` everywhere; output names
  stripped of characters Windows forbids; the model loads lazily, with an option for a local model path
  (offline use). espeak-ng comes with `espeakng-loader`, so there's no separate system install.
  **To check once on Windows:** that the `soundfile` wheel writes Ogg Opus.
- Seeds and cache keys use `hashlib`, never Python's `hash()` (it is salted per process).
- Torch can differ slightly between platforms, so checks against reference clips use a tolerance, and
  cache keys never depend on audio bytes.
- Voice packs are loaded with `torch.load(..., weights_only=True)`.
- Words the phonemizer can't read come out as `❓`. voicegen reports them so they can be respelled
  (`[O'Brien](/oʊbɹˈaɪən/)`).

## 1. Where everything lives

| What | Where | In git? |
|---|---|---|
| Standalone tool (`voicegen/`), bass adapter (`voice_lines.py`), prototype, these docs | `bass/tools/` | yes (commit when the user says) |
| Voice packs (`julia.pt`, `harris.pt`, `base_narrator.pt`) | `bass/stories/voices/` (copies; originals stay in `~/venv/kokoro/voices/`) | no (`stories/` is ignored) |
| Voice script per story (directions) | `bass/stories/<story>/voice_script.txt` | no |
| Generated clips | `bass/stories/<story>/voice/*.ogg` | no |
| Python runtime (kokoro 0.9.4, torch, librosa, soundfile) | `~/venv/kokoro/` (packages only) | n/a |

So the code lives in bass, and the kokoro folder only provides the Python environment: run the tool with
`~/venv/kokoro/bin/python -I tools/voice_lines.py …`. The old scripts in `~/venv/kokoro`
(`reader.py`, `weaver.py`, `scripts/`, `output/`) are retired. New voices are designed with `voicegen mixer`
(replaces `voice_lab.py`; see `tools/voicegen/README.md`). The existing voices' recipes were recovered exactly
on 2026-11-12 and saved as `stories/voices/<name>.recipe.json` (same blend for timbre and delivery):
julia = af_aoede 0.5 + jf_tebukuro 0.3 + af_jessica 0.2; harris = jf_gongitsune 0.4 + bm_lewis 0.3 +
af_nicole 0.3; base_narrator = af_nicole 0.65 + bm_fable 0.3 + bf_lily 0.05. Keep the `.pt` files backed up.

## 2. Audio format: Ogg Opus

- Written directly by `soundfile` (libsndfile 1.2.2 in the venv supports OGG/OPUS). **No ffmpeg, no mp3
  encoder.**
- Mono, 24 kHz. Opus at its default/VBR setting is roughly 30–50 kB per 5 s line, a few MB per chapter.
- Plays in Chrome, Firefox and Edge. Safari's Ogg support came late; check there if it matters (the stories
  are played locally, so probably not). Fallback: the format is one constant, so switching to `.m4a` via
  ffmpeg later is a one-line change plus a rebuild.
- **WAV fallback (2026-11-12):** `write()` takes a format (`ogg` / `wav` / `flac`; `--format` on the
  command line). If this machine's libsndfile can't write Opus, or the write fails, the clip is written as
  16-bit `.wav` instead and the real path is returned, so the manifest always names the actual file.

## 3. Decisions already made (2026-11-11)

- **Tone control is inside the model.** `synth()` re-implements `KModel.forward_with_tokens` and edits the
  durations and F0 before the decoder. See the prototype's docstring. Kokoro has no public API for this, so
  **pin kokoro == 0.9.4** and refuse to run on another version.
- **Approved styles** (settings in `voice_prototype.py`, `STYLES` / `trance()`):
  - `normal`
  - `trance <depth 0–1>`: depth 0 = "t02" (light), 1 = "t04" (deepest). It should deepen across a run of lines.
  - `deep`: "m05", dead-flat monotone for very deeply hypnotized lines and mantras. The user's favourite.
  - `shout`: pitch +4, range ×1.4, voicing floor 0.55 (fixes the growl at the tail).
  - `soft`: the voice's timbre with af_nicole's delivery, low, −10 dB by ear. Placeholder-quality.
  - `whisper`: calibrated against real whisper recordings (2026-11-12, eight rounds; replaced "w65"). Vowels
    become noise through the line's own vowel shapes with a real whisper's tone (`whisper_profile.py`),
    consonants are EQ'd toward real hiss and given short tails, pitchless voice-like bits are turned down,
    then a de-esser and two low-mid cuts found by ear (623 Hz −17 dB, 546 Hz −10 dB). −12 dB by ear.
    Tested on Harris; constants in `dsp.WHISPER`.
- **Question rise** (2026-11-12): Kokoro says one-word questions like statements (peak, fall, sag). The word
  before a `?` gets its contour flattened (90 %) and a rise laid on, level then sharp, through the end of
  voicing; applied after the offset smoothing. One-word questions get `rise 5` automatically (5 and 7 both
  approved by ear on "Julia?"); `{rise N}` on any line. Constants in `styles.QUESTION`.
- **Loudness is plain gain.** The model's energy curve is inert. Every clip is levelled by A-weighted
  loudness to one target, then the style's offset and the line's `volume` are applied.
- **Variation (`human`)**: default **1.0** ("medium"), per-line override. It's seeded, so builds are
  reproducible; `take N` re-rolls it. Seed = sha256(speaker, plain text, occurrence of that same text, take),
  so a repeated mantra gets different takes and renumbering events doesn't change any of them.
- **Speakers**: named events → that character. `???` is an alias for Doctor Harris. **Unnamed italic events
  are Julia's thoughts** (e.g. "But what if they do find something wrong?") → Julia's voice with an
  **`internal` effect** (slight reverb; still to be designed, see step 1). Named italic events are trance
  speech (e.g. "Yes… my eyes do feel a little more relaxed."), not thoughts: normal voice unless directed
  (good `deep` candidates).
- **Narrator**: `base_narrator.pt` as is. **Default speed 0.95** for everyone, as in the old reader.
- **Stress** comes from the manuscript's italic/bold words inside a sentence (italic → `(+)`, bold or bold
  italic → `(++)`). Neither current manuscript uses bold; it's there for future ones (`read_manuscript(path,
  bold=True)` in story_tools; the default output is unchanged, so `convert` and `build_story.py` aren't affected). Quoted words like `"need"` are *not* stressed automatically; that was a workaround in
  the old Kokoro scripts.
- **No auto-advance** in the player.
- **Directions live outside the story JSON.** `build_story.py` (chapter 2) overwrites
  `optometrist_p2.json` from the manuscript, and the editor used to drop unknown fields. The generator only
  writes `voice_line` paths into the story, like `relink` does for images, and is cheap to rerun after a
  rebuild because clips are cached.

## 4. The voice script (hand-editable)

`voice_lines.py sync` turns the story into `stories/<story>/voice_script.txt`, one line per dialog event.
You add directions by hand. A guide, `tools/VOICE_SCRIPT.md`, explains the format with examples. Sketch:

```
# Voice script: optometrist_p1. Edit {directions} and [word](marks). Guide: tools/VOICE_SCRIPT.md
@cast narrator = base_narrator
@cast Julia = julia
@cast Doctor Harris = harris | ???
@cast Julia (thought) = julia {internal}     # (as built: thoughts are a cast entry)
@defaults speed 0.95, human 1.0

== Chapter 1: An Eye Exam ==
c1.e1    narrator: Julia sat in the waiting room of the optometrist’s office, finding herself …
c1.e3    Julia (thought) {internal}: But what if they do find something wrong?
c1.e114  Julia {deep}: Yes… my eyes do feel a little more relaxed.
c1.e125  Julia {deep, take 2}: yes… yes… yes… yes…

@ramp trance 0.2 -> 1.0
c1.e170  Doctor Harris: Let everything else fade away…
c1.e174  Doctor Harris {pause 2.2}: Now... Julia... close your [eyes](+)...
@end
```

- `{…}` after the speaker holds a style and/or settings: `deep`, `trance 0.6`, `shout`, `soft`, `whisper`,
  `internal`, `normal`, plus `speed`, `pitch` (semitones), `range`, `pause`, `drift`, `volume` (dB),
  `human`, `take`.
- Word marks in the text: `[word](+)` stress, `(++)` strong stress, `(-)` de-stress, `(~)` drawn out,
  `(~~)` very drawn out; marks combine, e.g. `(+~)`. `[Harris](/hˈɛɹɪs/)` respells pronunciation (misaki's
  own syntax, passed through).
- **As built:** words are never edited directly; `[Dr.](=Doctor)` changes what is spoken and keeps the screen
  text, so lines always match the story exactly. (Original idea: editing the *words* of a line changes only what is spoken (a respelling, or "Dr." → "Doctor"). The
  dry-run lists every line whose spoken text differs from the screen text.
- `@ramp style a -> b … @end` deepens a style across the lines between, for the speaker of the first line;
  the narration in between is untouched. A per-line `{trance 0.8}` overrides the ramp.
- The `c1.e12` prefixes are for reading only. Lines are matched by text, so renumbering is harmless.

**Sync rules.** The story is the source of truth for text; the script is the source of truth for
directions.
- Directions and marks carry over to the line with the same plain text, matched in order.
- If the text changed a little (fuzzy match ≥ ~0.85), they carry over and the line is flagged `!changed`
  for review.
- Directions that no longer match anything move to an `== Orphaned ==` section at the end, commented
  out, so nothing is lost.
- The sync reads the **manuscript** (`.odt`) for in-sentence italic/bold words and adds `(+)`/`(++)` marks
  when a line is first created. It reuses `story_tools.py`'s ODT italic reader (that module is
  standard-library only, so it imports cleanly). This avoids touching the story JSON or
  `convert`/`build_story.py`.

## 5. Build steps (do them in this order, each one testable)

1. **Engine** (`tools/voicegen/`, standalone; see section 0)
   - Port `voice_prototype.py`. Replace `word_spans` with **token-based mapping**: build the phoneme string
     from misaki's g2p tokens while recording each token's span, then find a marked word by
     (normalized text, occurrence).
   - Split lines over 510 phonemes at sentence boundaries, splitting the *marked* text so marks stay
     with their words.
   - Level, apply the style offset, trim silence, write Ogg.
   - `preview` command: `voicegen preview --voice stories/voices/julia.pt --style "trance 0.7" "text" -o x.ogg`.
   - **Design `internal`** (short synthetic room reverb, roughly 0.25–0.4 s decay, 15–25 % wet, slightly
     darker and closer). Do a listening round with 3–4 variants before fixing it.
   - Check: reference clips rebuilt by the module match `stories/voices/reference/`.
2. **Done 2026-11-12.** **Voice script**: parser (voicegen), `sync` in the adapter (with merge, fuzzy carry-over, orphans,
   manuscript stress), and the `tools/VOICE_SCRIPT.md` guide. Check: sync chapter 1, add a few directions, edit the story text, re-sync,
   and confirm the directions survived.
3. **Done 2026-11-12.** **Build**: `voicegen render` (script → clips + manifest), wrapped by the adapter's `build` command.
   - Cache key = engine version + voice-file hash + resolved settings + spoken text + seed.
   - Clips are named `c1_e012_<hash10>.ogg`; an existing clip with a matching hash is reused or renamed.
   - Write `voice_line` into the story (save `.bak` first).
   - `--dry-run` (counts per speaker, lines with spoken-text overrides, unmatched directions, time
     estimate of roughly 0.7× real time on CPU plus 16 s model load). Also `--prune` and `--chapter`/`--events`.
   - Save progress on Ctrl-C.
4. **Done 2026-11-12.** **Player, editor, check** (tested in headless Chrome with playback stubbed; real playback still to be heard)
   - `dialog.js`:
     - a voice `Audio` separate from the sound effect;
     - stop the voice when a new event loads, including non-dialog events;
     - a "Mute voice" checkbox in `index.html`;
     - separate replay buttons: Play Audio (sound effect) and Play Voice (voice line).
   - `event_manager.js`: set/clear the voice in `ParseNextEvent`.
   - `edit.js`:
     - a voice field with Preview;
     - **fix `SaveEvent`** so it starts from the existing event and keeps fields it doesn't manage (today
       it rebuilds the event from the form and silently drops them).
   - `story_tools.py check`: add `voice_line` to `KNOWN_EVENT_KEYS` and report missing clip files.
   - `tools/README.md`: a section for voice_lines.
5. **Chapter 1**: sync, direct it by hand, build, listen, tune the style constants.
6. **Suggest pass** (only after 1–5): scan each line and the narration around it for cues
   ("whispered", "droned", "recited", "screamed", "mantra", Harris's hypnotic phrasing; named italic
   trance lines). Write suggestions into the script as comments (`#? {deep}  ← "Julia droned"`) for the
   user to accept by uncommenting.
7. **Chapter 2**: same as chapter 5's steps. Separately, a story-text fix: chapter 2 event 5 still contains
   "Oh shit, she thought, …" (add it to `TEXT_FIXES` in `build_story.py`).

## 6. Open items to settle along the way

- ~~The sound of `internal`~~ — settled 2026-11-12: soft 220 ms echo + a 5 % trace of room (`dsp.INTERNAL`).
- Whether `soft` or `whisper` should also apply to some thoughts (decide per line in the script).
- Trance ramp shape: linear is the default; maybe ease-in if the deepening sounds too abrupt.
- `human` on one-word lines can swing about 2.7 semitones (the line and word offsets add up on the same
  word). Cap it if it sounds off.
- Safari / Ogg, if the stories are ever played there.
