# tools/story_tools.py

Standard library only. Pillow is optional but recommended: it gives exact line-length measuring, image-size checks, and is required for `placeholders`.

```
python3 tools/story_tools.py convert stories/foo/foo_chapter1.odt --out stories/foo.json
python3 tools/story_tools.py check stories/foo.json
python3 tools/story_tools.py placeholders stories/foo.json --list stories/foo/placeholders.md
```

## convert

Turns a manuscript (`.odt`, `.txt`, `.md`) into a draft story file, then prints a report. It refuses to overwrite an existing file unless you pass `--force`; use `--dry-run` to see the report only.

What it does automatically:
- **Splits prose into events** that fit the dialog box (default: at most 3 wrapped lines and 2 sentences per event, measured the way the engine wraps text).
- **Separates speech from narration.** Quoted text becomes an event with `name` set; the rest is narration. Curly, straight and mixed quotes all work, and an unclosed quote is reported.
- **Finds the speaker** from the text around each quote ("Julia recited", "Doctor Harris nodded, “…”"), self-introductions, who is named inside a quote ("Trust me, Julia" is not Julia), and turn-taking. Uncertain choices are listed under *Speaker attributions to review*. On a 2-person, 4,000-word chapter about 96% matched a hand conversion.
- **Drops attribution-only narration** ("Julia murmured softly,") because the name box already shows it; `--keep-attributions` retains it. It also rejoins a quote that the attribution split in two.
- **Reads italics** (ODT italic runs, or `*text*` in .md/.txt). An event becomes `"italics": true` when most of it is italic, and "she thought" tags are removed from thoughts. Italic words inside a sentence can't be shown (the engine styles a whole event), so those are counted as *partial italics ignored*.
- **Handles structure.** `Chapter N – Title` lines or headings start a new chapter (with the chapter-end fade/jump the engine expects). A line of `---` (or `***`) is a scene break: fade out, and a fade in on the next event. Event 0 of every chapter is a fade-in.
- **Lints the source** (double spaces, repeated words, mixed quote styles, stray one-word paragraphs, "Dr." vs "Doctor").

Speakers are auto-detected (names that are used as titles, "Name said", or are repeated mid-sentence). For short texts, or if the detection is wrong, give them explicitly with aliases:

```
--speakers Julia "Doctor Harris=Dr. Harris|the optometrist"
```

### Directives: scene composition from the manuscript

Put a line made only of `[...]` groups in the manuscript. Images are picked up by the next event that is created; fades and waits create their own silent event.

| Directive | Effect |
|---|---|
| `[bg: bg_julia_snaps_out.png]` | Set the background for the next event |
| `[fg: tank.png]` | Set the foreground (character) |
| `[fx: rain.png]` | Set the effects overlay (needs player support, see `docs/effects_proposal.md`) |
| `[bg: clear]`, `[fg: clear]`, `[fx: clear]` | Clear that layer |
| `[sfx: door.wav]` | Sound effect for the next dialog |
| `[fadeout]`, `[fadein]`, `[fadein 800]` | A silent fade event (optional wait in ms) |
| `[wait: 500]` | A silent pause |
| `[label: Some name]` | Label for a fade/wait event |

Combine them with `;` inside one group: `[bg: apartment.png; fadein 700]` creates one fade-in event that also sets the background. Bare file names resolve under `--image-dir` (default `./stories/<name>/`); values starting with `./`, `/`, `http` are used as written.

A `fadeout` with no following `fadein` automatically gets one on the next event, and so does a scene break or chapter break, so the screen never stays black.

Not supported yet: `choice` (add those in the editor or by hand).

## check

Validates a story file (exit code 1 on errors):
- Structure: chapters/events present, event ids contiguous, unknown fields, old pre-chapter format.
- Links: `next`, `chapter`, `choice` targets exist; events with no `next` (the engine would replay them); unreachable events.
- Text: dialog wrapping past the box, `\n` in dialog (the engine does not break on it), empty dialog.
- Images: missing files; backgrounds not 1170×540 (stretched); foregrounds bigger than the canvas (clipped).
- Engine support: warns if the story uses `effects` and the player doesn't read them yet.

## placeholders

Creates a stand-in image for every local image the story references that doesn't exist yet (it never touches existing files). Each card is labelled with its file name, what kind of layer it is, where it's first used and a line of dialog from that scene. Backgrounds are opaque; foregrounds and effects are transparent, 1170×540. `--descriptions file.json` (`{"bg_penlight_blink.png": "Close-up of …"}`) adds your own text to the cards, and `--list out.md` writes a Markdown index. Run it after `convert` once the `[bg: …]` names are in place.

## finalize

Turns raw image-generator output into game-ready art. Put generations in `stories/<name>/raw/` named after the image they're for (`bg_exam_room_intake.jpeg`, `fg_black_glasses.png`, …); any size, any format. Rejected attempts go in `raw/_rejects/` (anything starting with `_` is ignored).

```
python3 tools/story_tools.py finalize stories/optometrist_p1.json            # only what changed
python3 tools/story_tools.py finalize stories/optometrist_p1.json --only bg_exam_room_intake --show-regions
```

For each raw image it:
- **Crops to 1170×540** (cover-crop to the 2.17:1 aspect, then a high-quality downscale) and reports any crop that removes more than 5 %.
- **Picks the format from how the story uses it:** backgrounds become `.jpg` (quality 90, full-colour chroma, about 5× smaller than PNG); foregrounds and effects become `.png` with alpha. Images the story doesn't use yet go by prefix (`fg_`, `fx_`, else background).
- **Removes the backdrop** of foregrounds and effects. It samples the real backdrop colour from the border, because generators never give exact #00FF00 and JPEG adds noise. Green or magenta backdrops are unmixed, so glass and soft edges keep partial alpha, with no green fringe. Effects on **black** become glow-style alpha: brightness turns into opacity. Generate light effects on black; glows on green key poorly.
- Writes `art/<name>.jpg|png` and remembers what it built in `art/.finalize.json`. It rebuilds only when the raw file or its settings change; use `--force` to rebuild everything.

Optional per-image settings go in `raw/finalize.json`; coordinates are canvas pixels (1170×540):

```json
{
  "bg_waiting_room_julia": {"anchor": [0.5, 0.0]},
  "bg_exam_room_intake": {"skin": [{"ellipse": [503, 270, 55, 60], "lightness": -0.15, "saturation": 0.15}]},
  "fx_display_shimmer": {"opacity": 0.6},
  "fg_julia_display_case": {"key": "green"}
}
```

| Setting | Effect |
|---|---|
| `anchor: [x, y]` | Which part survives the crop: 0 keeps the left/top edge, 1 the right/bottom (default centre) |
| `zoom` | Magnify by this factor (e.g. 1.3): crops a window 1/zoom the size of the normal crop, then scales it up. Use with `anchor` to choose which part stays in view. Warns if it has to upscale. `skin` coordinates are in the zoomed picture. |
| `kind` | Force `background` / `foreground` / `effects` |
| `key` | Force `green` / `magenta` / `black` / `none` instead of auto-detecting |
| `opacity` | Multiply an overlay's alpha (e.g. 0.35 for a subtle effect) |
| `skin` | List of skin-tone fixes. Inside a feathered `ellipse` `[cx, cy, rx, ry]`, only skin-coloured pixels (warm hue, medium saturation) are changed by `lightness`, `saturation` (fractions, e.g. -0.15) and `hue` (degrees). Hoodies, eye whites, hair and upholstery are left alone. `--show-regions` writes `art/_preview/` copies with the ellipses drawn, to check placement. |

## relink

Points every image in a story at the best version available: `art/`, then `originals/` (old art kept until it's replaced), then `placeholders/`. Images are matched by file name without extension, so `bg_exam_room_intake.png` in the story picks up `art/bg_exam_room_intake.jpg`. It prints how many images come from each folder; `-v` lists them and `--dry-run` changes nothing. The previous story file is saved as `<story>.json.bak`.

```
python3 tools/story_tools.py relink stories/optometrist_p1.json -v
```

Typical loop: generate into `raw/`, then `finalize`, then `relink`, then reload the game.
