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
| `[bg: scene_3.png]` | Set the background for the next event |
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

Creates a stand-in image for every local image the story references that doesn't exist yet (it never touches existing files). Each card is labelled with its file name, what kind of layer it is, where it's first used and a line of dialog from that scene. Backgrounds are opaque; foregrounds and effects are transparent, 1170×540. `--descriptions file.json` (`{"scene_9.png": "Close-up of …"}`) adds your own text to the cards, and `--list out.md` writes a Markdown index. Run it after `convert` once the `[bg: …]` names are in place.
