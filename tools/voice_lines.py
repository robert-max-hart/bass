#!/usr/bin/env python3
"""voice_lines.py - the bass side of voice generation (the generic part is tools/voicegen).

  sync    Write or update stories/<story>/voice_script.txt from the story .json (+ manuscript stress)
  build   Render the script's clips into stories/<story>/voice/ and link them into the story as `voice_line`

    ~/venv/kokoro/bin/python -I tools/voice_lines.py sync  stories/optometrist_p1.json
    ~/venv/kokoro/bin/python -I tools/voice_lines.py build stories/optometrist_p1.json --dry-run

The story is the source of truth for the text, and the script is the source of truth for directions.
Lines are matched by their text, so renumbering events is harmless. See tools/VOICE_SCRIPT.md.
"""
import argparse
import difflib
import json
import re
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / 'voicegen'))
import story_tools  # noqa: E402  (standard library only)
from voicegen import marks  # noqa: E402
from voicegen.script import Script, Line, Ramp  # noqa: E402

REPO_ROOT = TOOLS.parent

NARRATOR = 'narrator'
FUZZY = 0.85                 # text similarity above which directions carry over to an edited line
MAX_STRESS_WORDS = 3         # longer italic runs inside a line are passages, not stress
ORPHANED = 'Orphaned'
ORPHAN_NOTE = '# Directions whose line is no longer in the story. Copy them back or delete them.'

DEFAULT_CAST = {             # speaker -> (voice, aliases, direction)
    NARRATOR: ('base_narrator', [], ''),
    'Julia': ('julia', [], ''),
    'Julia (thought)': ('julia', [], 'internal'),
    'Doctor Harris': ('harris', ['???'], ''),
}


def header_for(story_name, speakers, thinker):
    h = [f'# Voice script: {story_name}. Edit {{directions}} and [word](marks); guide: tools/VOICE_SCRIPT.md',
         '# Lines are matched to the story by text; IDs and section titles are refreshed on every sync.',
         '@voices ../voices']
    cast = dict(DEFAULT_CAST)
    if thinker != 'Julia':
        cast[f'{thinker} (thought)'] = (thinker.lower(), [], 'internal')
    used = set(speakers)
    for name, (voice, aliases, d) in cast.items():
        if name in used or any(a in used for a in aliases) or name == NARRATOR:
            h.append(f'@cast {name} = {voice}' + ''.join(f' | {a}' for a in aliases) + (f' {{{d}}}' if d else ''))
    h.append('@defaults speed 0.95, human 1.0')
    return h


# =====================================================================
# Story -> lines
# =====================================================================
def story_lines(story, thinker):
    """[(section title, [Line])] in story order"""
    out = []
    for ci, (ck, ch) in enumerate(story['chapters'].items(), 1):
        lines = []
        for ek, ev in ch['events'].items():
            text = (ev.get('dialog') or '').strip()
            if not text:
                continue
            name = ev.get('name')
            if name:
                speaker = name
            elif ev.get('italics'):
                speaker = f'{thinker} (thought)'
            else:
                speaker = NARRATOR
            lines.append(Line(f'c{ci}.e{ek}', speaker, '', text))
        out.append((f'Chapter {ci}: {ch.get("label", "")}'.rstrip(': '), lines))
    return out


# =====================================================================
# Manuscript stress: italic words inside an otherwise upright line
# =====================================================================
def manuscript_text(path):
    """whole manuscript as one string (whitespace collapsed) + a style mask of the same length
    ('0' plain, '1' italic, '2' bold, '3' bold italic)"""
    s, m = [], []
    for _, t in story_tools.read_manuscript(path, bold=True):
        for ch, it in zip(t.s, t.m):
            if ch.isspace():
                if s and s[-1] == ' ':
                    continue
                ch = ' '
            s.append(ch); m.append(it)
        s.append(' '); m.append('0')
    return ''.join(s), ''.join(m)


def collapse(text):
    return re.sub(r'\s+', ' ', text).strip()


def locate(t, ms, mm, cursor):
    """the italic mask for line t, aligned character by character to the manuscript (the story text may
    have been edited since). Returns (mask or None, new cursor)."""
    i = ms.find(t, cursor)
    if i < 0:
        i = ms.find(t)
    if i >= 0:
        return mm[i:i + len(t)], i + len(t)
    for w0, w1 in ((max(0, cursor - 300), cursor + 2 * len(t) + 600), (0, len(ms))):
        window = ms[w0:w1]
        sm = difflib.SequenceMatcher(None, t, window, autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size >= 4]
        matched = sum(b.size for b in blocks)
        if blocks and matched >= 0.75 * len(t):
            mask = ['0'] * len(t)
            for b in blocks:
                mask[b.a:b.a + b.size] = mm[w0 + b.b:w0 + b.b + b.size]
            return ''.join(mask), w0 + blocks[-1].b + blocks[-1].size
        if len(ms) <= w1 - w0:
            break
    return None, cursor


def add_stress(lines, ms, mm, report):
    """new (unmarked) lines get [word](+) for italic and [word](++) for bold words inside a sentence.
    A line that is mostly italic is a thought, not stress, so its italics are ignored (bold still counts)."""
    cursor = 0
    for ln in lines:
        t = collapse(ln.text)
        mask, cursor = locate(t, ms, mm, cursor)
        if mask is None:
            report['not_in_manuscript'] += 1
            continue
        letters = sum(1 for c in t if c.isalpha())
        ital = sum(1 for c, k in zip(t, mask) if c.isalpha() and k in '13')
        italic_line = ital >= story_tools.ITALIC_THRESHOLD * max(1, letters)
        out, pos = [], 0
        for r in re.finditer(r'1+|2+|3+', mask):
            code = r.group(0)[0]
            if code == '1' and italic_line:
                continue
            a, b = r.start(), r.end()
            while 0 < a < len(t) and t[a].isalnum() and t[a - 1].isalnum():   # run starts mid-word
                a -= 1
            while 0 < b < len(t) and t[b - 1].isalnum() and t[b].isalnum():   # run ends mid-word
                b += 1
            while a < b and not t[a].isalnum():
                a += 1
            while b > a and not t[b - 1].isalnum():
                b -= 1
            if a >= b or a < pos:
                continue
            run = t[a:b]
            if len(run.split()) > MAX_STRESS_WORDS:
                report['long_italic'].append(f'{ln.id}: "{run}"' + (' (bold)' if code != '1' else ''))
                continue
            out += [t[pos:a], f'[{run}]({"+" if code == "1" else "++"})']
            pos = b
            report['stress'] += 1
        if out:
            ln.text = ''.join(out) + t[pos:]


# =====================================================================
# Merge with the existing script
# =====================================================================
def carry_comments(old):
    """standalone comments travel with the next line"""
    pending = []
    for kind, x in old.items:
        if kind == 'section' and x == ORPHANED:
            break
        if kind == 'comment':
            pending.append(x)
        elif kind == 'line':
            x.comments = pending + x.comments; pending = []
    return pending


def old_orphans(old):
    """comment lines already in the Orphaned section"""
    out, inside = [], False
    for kind, x in old.items:
        if kind == 'section':
            inside = x == ORPHANED
        elif inside and kind in ('comment', 'line') and x != ORPHAN_NOTE:
            out.append(x if kind == 'comment' else '# ' + Script.format_line(x))
    return out


def worth_keeping(ln):
    return bool(ln.direction or marks.has_marks(ln.text) or ln.flags or ln.comments)


def merge(new_sections, old, report):
    old_lines = [ln for ln in old.lines] if old else []
    if old:
        orphan_section_start = next((i for i, (k, x) in enumerate(old.items) if k == 'section' and x == ORPHANED),
                                    len(old.items))
        live = {id(x) for k, x in old.items[:orphan_section_start] if k == 'line'}
        old_lines = [ln for ln in old_lines if id(ln) in live]
    taken = {}                                     # id(old line) -> new line
    new_all = [ln for _, lines in new_sections for ln in lines]

    by_text = {}
    for ln in old_lines:
        by_text.setdefault(ln.plain, []).append(ln)
    unmatched = []
    for ln in new_all:
        cands = by_text.get(ln.text)               # new lines are unmarked here, so text == plain
        if cands:
            o = cands.pop(0)
            taken[id(o)] = ln
            ln.direction, ln.flags, ln.comments, ln.text = o.direction, o.flags, o.comments, o.text
            report['kept'] += 1
        else:
            unmatched.append(ln)

    rest = [o for o in old_lines if id(o) not in taken]
    for ln in unmatched:
        best, score = None, FUZZY
        for o in rest:
            sm = difflib.SequenceMatcher(None, o.plain, ln.text)
            if sm.real_quick_ratio() < score or sm.quick_ratio() < score:
                continue
            r = sm.ratio()
            if r >= score:
                best, score = o, r
        if best is None:
            report['new'] += 1
            ln.fresh = True
            continue
        rest.remove(best)
        taken[id(best)] = ln
        text, missing = marks.apply_marks(ln.text, marks.marked_words(best.text))
        ln.text = text
        ln.direction, ln.comments = best.direction, best.comments + [f'#   was: {best.text}']
        ln.flags = best.flags + ([] if 'changed' in best.flags else ['changed'])
        if missing:
            ln.comments.append('#   marks lost: ' + ', '.join(missing))
        report['changed'] += 1

    orphans = []
    for o in rest:
        if worth_keeping(o):
            orphans += o.comments + ['# ' + Script.format_line(o)]
            report['orphaned'] += 1
    return taken, orphans


def rebuild_ramps(old, taken, report):
    """old ramps re-anchored on the new lines they covered: {id(first new line): Ramp, id(last): 'end'}"""
    starts, ends, orphans = {}, {}, []
    for r in (old.ramps if old else []):
        mapped = [taken[id(ln)] for ln in r.lines if id(ln) in taken]
        if not mapped:
            orphans.append('# ' + r.header() + '   (its lines are gone)')
            report['orphaned'] += 1
            continue
        starts[id(mapped[0])] = Ramp(r.what, r.a, r.b, r.speaker)
        ends[id(mapped[-1])] = True
    return starts, ends, orphans


def sync(a):
    story_path = Path(a.story)
    story = json.loads(story_path.read_text(encoding='utf-8'))
    folder = story_path.with_suffix('')
    script_path = Path(a.script) if a.script else folder / 'voice_script.txt'
    report = dict(kept=0, changed=0, new=0, orphaned=0, stress=0, not_in_manuscript=0, long_italic=[])

    sections = story_lines(story, a.thinker)
    old = Script.load(script_path) if script_path.exists() else None
    if old and old.errors:
        for n, msg in old.errors:
            print(f'{script_path}:{n}: {msg}')
        sys.exit('fix the script before syncing (nothing was changed)')

    leftover_comments = carry_comments(old) if old else []
    taken, orphans = merge(sections, old, report)
    starts, ends, ramp_orphans = rebuild_ramps(old, taken, report)

    manuscript = Path(a.manuscript) if a.manuscript else next(iter(sorted(folder.glob('*.odt'))), None)
    fresh = [ln for _, lines in sections for ln in lines if getattr(ln, 'fresh', False)]
    if fresh and manuscript and not a.no_stress:
        ms, mm = manuscript_text(manuscript)
        add_stress(fresh, ms, mm, report)

    sc = Script(script_path)
    speakers = {ln.speaker for _, lines in sections for ln in lines}
    if old:
        sc.header = list(old.header) + [x for k, x in old.items if k == 'directive']
        known = set(old.aliases)
        for who in sorted(speakers - known):
            sc.header.append(f'@cast {who} = {who.lower().replace(" ", "_")}   # added by sync: check the voice')
    else:
        sc.header = header_for(story.get('story_name', story_path.stem), speakers, a.thinker)
        known = {n for h in sc.header if h.startswith('@cast')
                 for n in [h[5:].split('=')[0].strip()] + [x.split('{')[0].strip() for x in h.split('|')[1:]]}
        for who in sorted(speakers - known):
            sc.header.append(f'@cast {who} = {who.lower().replace(" ", "_")}   # added by sync: check the voice')

    for title, lines in sections:
        sc.items.append(('section', title))
        for ln in lines:
            if id(ln) in starts:
                sc.items.append(('ramp', starts[id(ln)]))
            sc.items.append(('line', ln))
            if id(ln) in ends:
                sc.items.append(('end', None))
    orphan_lines = old_orphans(old) if old else []
    orphan_lines += leftover_comments + orphans + ramp_orphans
    if orphan_lines:
        sc.items.append(('section', ORPHANED))
        sc.items.append(('comment', ORPHAN_NOTE))
        sc.items += [('comment', c) for c in orphan_lines]

    text = sc.dump()
    if a.dry_run:
        print(text if a.verbose else f'(dry run) would write {script_path}')
    else:
        if script_path.exists():
            shutil.copyfile(script_path, script_path.with_suffix('.txt.bak'))
        script_path.write_text(text, encoding='utf-8', newline='\n')
    n = sum(len(lines) for _, lines in sections)
    print(f'{script_path}: {n} lines - {report["kept"]} kept, {report["changed"]} changed (flagged !changed), '
          f'{report["new"]} new, {report["orphaned"]} orphaned')
    if fresh:
        print(f'  stress marks from the manuscript: {report["stress"]}'
              + (f' ({report["not_in_manuscript"]} new lines not found in {manuscript.name})'
                 if manuscript and report['not_in_manuscript'] else '')
              + ('' if manuscript else ' (no manuscript found)'))
        for x in report['long_italic']:
            print(f'  italic/bold passage left unmarked: {x}')


def build(a):
    from voicegen.render import render, print_summary
    story_path = Path(a.story).resolve()
    text = story_path.read_text(encoding='utf-8')
    story = json.loads(text)
    folder = story_path.with_suffix('')
    script_path = Path(a.script) if a.script else folder / 'voice_script.txt'
    out = Path(a.out).resolve() if a.out else folder / 'voice'
    if not script_path.exists():
        sys.exit(f'{script_path} not found: run "voice_lines.py sync {a.story}" first')
    try:
        web_root = './' + out.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        sys.exit(f'the clip folder must be inside the repo ({REPO_ROOT}) so the game can load it')

    # the script must describe the story as it is now
    sc = Script.load(script_path)
    expected = {ln.id: ln for _, lines in story_lines(story, a.thinker) for ln in lines}
    have = {ln.id: ln for ln in sc.lines}
    stale = [i for i in expected if i not in have or have[i].plain != expected[i].text]
    stale += [i for i in have if i not in expected]
    if stale:
        print(f'{len(stale)} lines differ between the story and {script_path.name}, e.g. ' + ', '.join(stale[:6]))
        sys.exit(f'run "voice_lines.py sync {a.story}" first (nothing was built)')

    jobs, errors = sc.jobs()
    if errors:
        for n, msg in errors:
            print(f'  {script_path}:{n}: {msg}' if n else f'  {script_path}: {msg}')
        if not a.keep_going:
            sys.exit('fix the script first (or use --keep-going to build the good lines)')
    s = render(jobs, out, a.format, a.dry_run, a.prune, a.only)
    print_summary(s, dry_run=a.dry_run)

    # link clips into the story
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8')) if (out / 'manifest.json').exists() \
        else {'clips': {}}
    changes = 0
    for ci, (ck, ch) in enumerate(story['chapters'].items(), 1):
        for ek, ev in ch['events'].items():
            clip = manifest['clips'].get(f'c{ci}.e{ek}')
            want = f'{web_root}/{clip["file"]}' if clip and (out / clip['file']).exists() else None
            if ev.get('voice_line') != want:
                changes += 1
                if not a.dry_run:
                    if want:
                        ev['voice_line'] = want
                    else:
                        ev.pop('voice_line', None)
    if a.dry_run:
        print('(dry run) story not changed')
        return
    if not changes:
        print('story links already up to date')
        return
    story_path.with_name(story_path.name + '.bak').write_text(text, encoding='utf-8')
    one_line = text.count('\n') <= 2
    story_path.write_text(json.dumps(story, ensure_ascii=False, separators=(',', ':')) if one_line
                          else story_tools.dump_story(story), encoding='utf-8')
    print(f'updated {changes} voice_line links in {story_path.name} (previous version: {story_path.name}.bak)')


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('sync', help='write/update the voice script from the story')
    s.add_argument('story', help='story .json')
    s.add_argument('--script', help='default: stories/<story>/voice_script.txt')
    s.add_argument('--manuscript', help='default: the .odt in stories/<story>/')
    s.add_argument('--no-stress', action='store_true', help="don't add stress marks from the manuscript")
    s.add_argument('--thinker', default='Julia', help='whose thoughts the unnamed italic lines are')
    s.add_argument('--dry-run', action='store_true')
    s.add_argument('-v', '--verbose', action='store_true', help='with --dry-run: print the script')
    s.set_defaults(func=sync)
    b = sub.add_parser('build', help='render clips and link them into the story')
    b.add_argument('story', help='story .json')
    b.add_argument('--script', help='default: stories/<story>/voice_script.txt')
    b.add_argument('--out', help='clip folder (default: stories/<story>/voice/)')
    b.add_argument('--format', default='ogg', choices=('ogg', 'wav', 'flac'))
    b.add_argument('--only', action='append', help='IDs or patterns, e.g. "c1.e1*" (repeatable, comma-separated)')
    b.add_argument('--dry-run', action='store_true', help='what would be rendered, how long it would take')
    b.add_argument('--prune', action='store_true', help='delete clips no line uses any more')
    b.add_argument('--keep-going', action='store_true', help='build the good lines even if some have errors')
    b.add_argument('--thinker', default='Julia')
    b.set_defaults(func=build)
    a = p.parse_args(argv)
    a.func(a)


if __name__ == '__main__':
    main()
