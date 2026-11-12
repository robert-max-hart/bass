"""voicegen command line.

    voicegen preview "Close your [eyes](+)..." --voice voices/harris.pt --style "trance 0.6" -o x.ogg
    voicegen preview --script voice_script.txt --line c1.e174 -o x.ogg
    voicegen check voice_script.txt
    voicegen render voice_script.txt --out voice/ [--dry-run] [--only "c1.e1*"] [--prune]
"""
import argparse
import sys
import time
from pathlib import Path


def report(errors, label):
    for n, msg in errors:
        print(f'  {label}:{n}: {msg}' if n else f'  {label}: {msg}')


def cmd_preview(a):
    from .engine import Engine, write
    if a.script:
        from .script import Script
        jobs, errors = Script.load(a.script).jobs(a.voices_dir)
        job = next((j for j in jobs if j.id == a.line), None)
        if not job:
            report(errors, a.script)
            sys.exit(f'voicegen: no usable line "{a.line}" in {a.script}')
        if a.style:
            from . import styles
            job.direction = styles.merge(job.direction, styles.parse_direction(a.style))
        text, voice, direction, seed = job.text, job.voice, job.direction, job.seed
        print(f'{job.id}  {job.speaker} {{{job.direction}}}: {job.text}')
    else:
        if not a.text or not a.voice:
            sys.exit('voicegen preview: give TEXT and --voice, or --script and --line')
        text, voice, direction, seed = a.text, a.voice, a.style, a.seed
        if a.voices_dir and not Path(voice).suffix and not Path(voice).exists():
            voice = str(Path(a.voices_dir) / f'{voice}.pt')
    t0 = time.time()
    eng = Engine(model_dir=a.model_dir)
    t1 = time.time()
    r = eng.render(text, voice, direction, seed=seed)
    out = write(a.output, r.audio, a.format)
    print(f'{out}: {len(r.audio) / 24000:.1f} s  (model load {t1 - t0:.0f} s, render {time.time() - t1:.1f} s)')
    if a.verbose:
        print('phonemes:', r.phonemes)
    for w in r.unknown_words:
        print(f'  ! the phonemizer could not read "{w}": respell it, e.g. [{w}](/.../)')
    for w in r.unplaced_marks:
        print(f'  ! the mark on "{w}" did not land on a word')


def cmd_check(a):
    from collections import Counter
    from .script import Script
    sc = Script.load(a.script)
    jobs, errors = sc.jobs(a.voices_dir)
    flagged = [ln for ln in sc.lines if ln.flags]
    print(f'{a.script}: {len(sc.lines)} lines, {len(jobs)} ready')
    for who, n in Counter(j.speaker for j in jobs).most_common():
        print(f'  {n:5}  {who}')
    styled = Counter(j.direction.style or 'normal' for j in jobs)
    print('  styles: ' + ', '.join(f'{k} {v}' for k, v in styled.most_common()))
    if flagged:
        print(f'{len(flagged)} flagged for review:')
        for ln in flagged:
            print(f'  {a.script}:{ln.lineno}: {ln.id} !{" !".join(ln.flags)}')
    if errors:
        print(f'{len(errors)} problems:')
        report(errors, a.script)
        sys.exit(1)


def cmd_render(a):
    from .script import Script
    from .render import render, print_summary
    sc = Script.load(a.script)
    jobs, errors = sc.jobs(a.voices_dir)
    if errors:
        print(f'{len(errors)} problems in the script (fix them, or they are skipped):')
        report(errors, a.script)
        if not a.keep_going:
            sys.exit('voicegen: nothing rendered (use --keep-going to render the good lines anyway)')
    out = Path(a.out) if a.out else Path(a.script).parent / 'voice'
    s = render(jobs, out, a.format, a.dry_run, a.prune, a.only, a.model_dir)
    print_summary(s, dry_run=a.dry_run)
    print(f'clips: {out}')


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')     # Windows consoles default to a legacy code page
    p = argparse.ArgumentParser(prog='voicegen', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--model-dir', help='local Kokoro model folder (config.json + .pth) for offline use')
    sub = p.add_subparsers(dest='cmd', required=True)
    pv = sub.add_parser('preview', help='render one line')
    pv.add_argument('text', nargs='?')
    pv.add_argument('--voice', help='.pt file, a name in --voices-dir, or a stock voice (af_nicole)')
    pv.add_argument('--voices-dir')
    pv.add_argument('--style', default='', help='direction, e.g. "trance 0.6, internal, pause 2" '
                                                '(with --script: added on top of the line\'s own)')
    pv.add_argument('--script', help='voice script to take the line from')
    pv.add_argument('--line', help='ID of the line in --script')
    pv.add_argument('--seed', type=int, default=0)
    pv.add_argument('-o', '--output', default='preview.ogg')
    pv.add_argument('--format', choices=('ogg', 'wav', 'flac'),
                    help='audio format (default: from -o; ogg falls back to wav if Opus is unavailable)')
    pv.add_argument('-v', '--verbose', action='store_true')
    pv.set_defaults(func=cmd_preview)
    ck = sub.add_parser('check', help='parse a voice script and report problems')
    ck.add_argument('script')
    ck.add_argument('--voices-dir', help='overrides the script\'s @voices')
    ck.set_defaults(func=cmd_check)
    rd = sub.add_parser('render', help='render every line of a voice script (cached)')
    rd.add_argument('script')
    rd.add_argument('--out', help='clip folder (default: voice/ next to the script)')
    rd.add_argument('--voices-dir', help='overrides the script\'s @voices')
    rd.add_argument('--format', default='ogg', choices=('ogg', 'wav', 'flac'))
    rd.add_argument('--only', action='append', help='IDs or patterns, e.g. "c1.e1*" (repeatable, comma-separated)')
    rd.add_argument('--dry-run', action='store_true', help='show what would be rendered and how long it would take')
    rd.add_argument('--prune', action='store_true', help='delete clips no line uses any more')
    rd.add_argument('--keep-going', action='store_true', help='render the good lines even if some have errors')
    rd.set_defaults(func=cmd_render)
    a = p.parse_args(argv)
    try:
        a.func(a)
    except (ValueError, FileNotFoundError) as e:
        sys.exit(f'voicegen: {e}')


if __name__ == '__main__':
    main()
