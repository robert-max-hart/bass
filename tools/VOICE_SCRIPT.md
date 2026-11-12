# Voice scripts

A voice script is a plain text file with one line per voice clip. You direct the delivery by hand: a style
and settings in `{…}` after the speaker, and marks on single words. The format belongs to **voicegen**
(`tools/voicegen/`), so it works the same for any game. The bass-specific part, generating the script from a
story, is at the end of this guide.

```
# Voice script: The Optometrist: An Eye Exam.
@voices ../voices
@cast narrator = base_narrator
@cast Julia = julia
@cast Julia (thought) = julia {internal}
@cast Doctor Harris = harris | ???
@defaults speed 0.95, human 1.0

== Chapter 1: An Eye Exam ==

c1.e3    Julia (thought): But what if they do find something wrong?
c1.e62   Doctor Harris {pause 1.5}: And have you ever [wanted](+) to wear contacts, or glasses?
# she hesitates here
c1.e117  Julia {deep, take 2}: It... it does look clear, but I don’t know if it’s really [more](+) clear…

@ramp trance 0.2 -> 1.0
c1.e170  Doctor Harris: Let everything else fade away…
c1.e174  Doctor Harris {pause 2.2}: Now... Julia... close your [eyes](+~)...
@end
```

## Lines

`ID  [!flags]  Speaker {direction}: text`

- **ID**: any text without spaces, unique in the file. Clips are named after it.
- **!flags**: notes for review, such as `!changed`. They don't change the sound; delete them once you've
  checked the line.
- **Speaker**: a name from a `@cast` line, or one of its aliases.
- **{direction}**: optional; see below.
- **text**: what is spoken, with optional word marks.
- `#` starts a comment. A comment directly above a line belongs to that line. `== Title ==` is a heading
  for reading only.

## Header

| Line | Meaning |
|---|---|
| `@voices ../voices` | the folder that voice names are looked up in, relative to the script |
| `@cast Name = voice` | which voice a speaker uses: a name in `@voices` (`julia` → `julia.pt`), a path to a `.pt`, or a Kokoro stock voice (`af_nicole`) |
| `@cast Name = voice \| alias \| alias` | other names for the same speaker (`???` → Doctor Harris) |
| `@cast Name = voice {direction}` | a default direction for everything this speaker says |
| `@defaults direction` | a default direction for every line |

**To change a voice**, replace its `.pt` file or point the `@cast` line at a new one. A rebuild then
redoes that speaker's lines.

## Directions

A direction is a comma-separated list: at most one **style**, any **effects**, and any **settings**.

| Style | Sound |
|---|---|
| `normal` | the voice as it is |
| `trance 0.6` | slower, lower and flatter, with longer pauses. Depth 0 is light, 1 is deepest |
| `deep` | dead-flat, slow monotone, for very deep trance and mantras |
| `shout` | higher and livelier |
| `soft` | quiet and gentle (placeholder quality) |
| `whisper` | breathy whisper. Not perfect; use sparingly |

| Effect | Sound |
|---|---|
| `internal` | a soft echo, for thoughts |

| Setting | Meaning | Default |
|---|---|---|
| `speed 0.9` | the speaker's base speed; a style scales it | 0.95 |
| `pitch 2` | semitones up (negative = down), added to the style's | 0 |
| `range 0.5` | pitch movement: 0 = flat, 1 = natural, above 1 = livelier | from style |
| `pause 2` | multiplies the length of pauses at punctuation | from style |
| `drift 1` | semitones of slow fall across the line | from style |
| `volume -3` | dB, added to the style's level | 0 |
| `human 0.5` | natural variation in timing and pitch: 0 = none, 1 = default | 1.0 |
| `take 2` | a different variation of the same line; try takes until one sounds right | 0 |
| `floor 0.55` | ends voicing cleanly when pitch sags (cures growl at line ends) | from style |

Directions combine in this order: `@defaults`, then the speaker's `@cast` direction, then a ramp, then the
line's own. A later style replaces an earlier one, settings replace earlier settings, and effects add up.

## Word marks

| Mark | Effect |
|---|---|
| `[word](+)` | stress |
| `[word](++)` | strong stress |
| `[word](-)` | de-stress |
| `[word](~)`, `[word](~~)` | drawn out, very drawn out |
| `[word](+~)` | marks combine |
| `[Harris](/hˈɛɹɪs/)` | pronounce it this way (IPA, Kokoro/misaki symbols) |
| `[Dr.](=Doctor)` | say different words; the text on screen stays the same. `[um](=)` makes a word silent |

Brackets can hold several words: `[you must](+)`. **Change how a line sounds with marks only**, and leave
the words themselves alone. If the plain text differs from the game's text, the line no longer matches
and is treated as changed.

Each line is checked for words the phonemizer couldn't read; respell those with `(/…/)`.

## Ramps

```
@ramp trance 0.2 -> 1.0
…lines…
@end
```

A ramp gradually changes a style or setting across the lines of one speaker: by default the first line's
speaker, or name one with `@ramp trance 0.2 -> 1.0 for Doctor Harris`. Other speakers' lines in between
are untouched. Ramps work on `trance` or any setting, for example `@ramp speed 1.0 -> 0.8`. A line's own
style wins over the ramp, but a line with only settings (such as `{pause 2.2}`) keeps the ramp's style.

## Commands

```
voicegen check  stories/optometrist_p1/voice_script.txt
voicegen preview --script stories/optometrist_p1/voice_script.txt --line c1.e174 -o x.ogg
voicegen preview --script … --line c1.e174 --style "take 3"       # try a direction on top of the line's
voicegen preview "Close your [eyes](+~)..." --voice stories/voices/harris.pt --style "trance 0.6"
voicegen render stories/optometrist_p1/voice_script.txt --out clips/ [--dry-run] [--only "c1.e1*"] [--prune]
```

`render` renders every line into the clip folder and writes `manifest.json` (ID → file, duration). It only
renders what changed. Each clip's file name carries a key built from everything that affects its sound:
the voice file's contents, the resolved direction, the tuning constants that line uses, the text and marks,
and the variation seed. Renumbered lines reuse their old clips, and replacing a `.pt` rebuilds only that
voice's lines. `--dry-run` shows what would be rendered and roughly how long it would take. Ctrl-C is safe:
finished clips are kept, and the next run continues. `--prune` deletes clips no line uses any more.

`--format wav` (or `flac`) writes uncompressed audio. Ogg Opus is the default and falls back to `.wav` on
machines that can't write it.

---

## bass: syncing a script with a story

```
~/venv/kokoro/bin/python -I tools/voice_lines.py sync stories/optometrist_p1.json
```

This writes `stories/optometrist_p1/voice_script.txt`, keeping a `.bak` of the previous version. Run it
again whenever the story changes.

- **Speakers.** Named events go to that character. Unnamed italic events are Julia's thoughts, as the speaker
  `Julia (thought)` with `{internal}` (`--thinker` changes whose thoughts they are). Named italic events are
  normal speech, often trance; direct them yourself (`deep` is a good candidate). Every other unnamed event
  is the narrator.
- **IDs** (`c1.e62` = chapter 1, event 62) and section titles are rewritten on every sync. Lines are matched
  by their **text**, so renumbering events doesn't lose anything.
- **When the story text changes slightly** (≥ 85 % similar), the line keeps its direction and marks. It is
  flagged `!changed`, with the old version as a `#   was:` comment above it. Check it, then delete the flag
  and the comment.
- **When a directed line disappears from the story**, it moves to the `== Orphaned ==` section at the end,
  commented out. Copy it back to where it belongs, or delete it. A ramp whose lines are all gone goes there
  too.
- **Stress marks.** New lines get marks from the manuscript (`stories/<story>/*.odt`, or `.md`/`.txt` with
  `*italic*` and `**bold**`): words in **italic** within a sentence get `(+)`, and **bold** or bold italic
  words get `(++)`. A mostly-italic line is a thought, so its italics are ignored, but bold still counts.
  Matching is fuzzy, because the story text may have been edited since. Runs longer than three words are
  reported rather than marked. `--no-stress` turns this off.
- `--dry-run -v` prints the result without writing anything.

```
~/venv/kokoro/bin/python -I tools/voice_lines.py build stories/optometrist_p1.json [--dry-run] [--only …] [--prune]
```

`build` renders the script into `stories/<story>/voice/` (using `voicegen render`), then writes each clip's
path into its event as `voice_line` (the previous story is kept as `.json.bak`). It refuses to run when the
story has changed since the last sync. Chapter 2's `build_story.py` rewrites the story file and drops those
links. Run `build` again afterwards; nothing is re-rendered.
