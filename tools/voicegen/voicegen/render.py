"""Render a voice script into clips + manifest.json, reusing every clip whose inputs haven't changed.

A clip's cache key covers everything that shapes its sound: the engine version, the voice file's contents,
the resolved settings, the tuning constants the line actually uses (stress, `internal`, whisper/breath,
loudness), the text with its marks, and the seed. Files are named `<id>_<key10>.<ext>`, so a renumbered line
finds its old clip by key and is renamed instead of re-rendered.

manifest.json:  {"voicegen": 1, "format": "ogg", "clips": {id: {"file", "key", "speaker", "text", "seconds"}}}
"""
import fnmatch
import hashlib
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from . import ENGINE_VERSION, marks as marks_mod, styles
from .common import STOCK_PREFIXES

MANIFEST = 'manifest.json'
CLIP_RE = re.compile(r'^(.+)_([0-9a-f]{10})\.(ogg|wav|flac)$')
CHARS_PER_SECOND = 14.0         # rough speech rate at speed 1, for estimates
RENDER_FACTOR = 0.75            # CPU seconds per second of audio (measured on the dev machine)
MODEL_LOAD = 10.0


def clip_name(line_id, key, ext):
    safe = re.sub(r'[^A-Za-z0-9_-]+', '_', line_id).strip('_') or 'line'
    safe = re.sub(r'\d+', lambda m: m.group(0).zfill(3), safe)
    return f'{safe}_{key[:10]}.{ext}'


_file_hashes = {}


def file_hash(path):
    p = str(path)
    if p.startswith(STOCK_PREFIXES) and not Path(p).suffix:
        return 'stock:' + p
    if p not in _file_hashes:
        _file_hashes[p] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
    return _file_hashes[p]


def tuning(job, settings):
    """the tuning constants this line depends on (so tuning one style only rebuilds the lines using it)"""
    from . import dsp, engine
    t = {'level': [dsp.LEVEL_TARGET, dsp.PEAK_LIMIT], 'voiced': engine.VOICED_HZ,
         'timing': [styles.PAUSES, styles.PAUSE_HUMAN, styles.LENGTH_CAP, styles.PITCH_BRIDGE, styles.QUIET_ENERGY, styles.QUIET_EASE]}
    mt = marks_mod.parse(job.text)
    if mt.marks:
        t['marks'] = [styles.STRESS, styles.STRETCH, styles.STRESS_MAX, styles.STRETCH_CAP, styles.SUSTAIN_ENERGY, styles.SUSTAIN_SMOOTH]
        more = sorted({m.stress for m in mt.marks if m.stress in styles.STRESS_MORE})
        more_s = sorted({m.stretch for m in mt.marks if m.stretch in styles.STRETCH_MORE})
        if more or more_s:                      # only lines using levels 3-5 depend on them
            t['marks_more'] = [{k: styles.STRESS_MORE[k] for k in more}, {k: styles.STRETCH_MORE[k] for k in more_s}]
    if any(styles.question_rises(settings, mt.plain)):
        t['question'] = styles.QUESTION
    if 'internal' in settings['effects']:
        t['internal'] = dsp.INTERNAL
    if settings['style'] == 'whisper' or settings.get('breath'):
        from .whisper_profile import VOWEL, FRIC
        t['whisper'] = [dsp.WHISPER, VOWEL, FRIC]
    return t


def cache_key(job):
    s = styles.resolve(job.direction)
    blob = json.dumps({'engine': ENGINE_VERSION, 'voice': file_hash(job.voice), 'settings': s,
                       'tuning': tuning(job, s), 'text': job.text, 'seed': job.seed},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()


def estimate_seconds(job):
    s = styles.resolve(job.direction)
    plain = marks_mod.parse(job.text).plain
    return len(plain) / CHARS_PER_SECOND / max(0.3, s['speed']) * (1 + 0.15 * (s['pause'] - 1))


def fmt_time(seconds):
    seconds = max(0, seconds)
    return f'{seconds:.0f} s' if seconds < 90 else f'{seconds / 60:.0f} min'


def duration(path):
    try:
        import soundfile as sf
        return round(sf.info(str(path)).duration, 2)
    except Exception:
        return None


def load_manifest(out):
    p = out / MANIFEST
    if p.exists():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except ValueError:
            pass
    return {'voicegen': 1, 'clips': {}}


def save_manifest(out, manifest):
    p = out / MANIFEST
    tmp = p.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)


def select(jobs, only):
    if not only:
        return jobs
    pats = [p.strip() for x in only for p in x.split(',') if p.strip()]
    return [j for j in jobs if any(fnmatch.fnmatchcase(j.id, p) for p in pats)]


def render(jobs, out, fmt='ogg', dry_run=False, prune=False, only=None, model_dir=None, log=print):
    """Render `jobs` (from Script.jobs) into folder `out`. Returns a summary dict.
    Safe to interrupt: the manifest is saved after every clip."""
    from .engine import resolve_format
    out = Path(out)
    fmt = resolve_format(fmt, warn=log)
    manifest = load_manifest(out) if out.exists() else {'voicegen': 1, 'clips': {}}
    old = manifest['clips']
    chosen = select(jobs, only)
    chosen_ids = {j.id for j in chosen}

    # what's on disk, by key
    on_disk = {}
    if out.exists():
        for f in out.iterdir():
            m = CLIP_RE.match(f.name)
            if m and m.group(3) == fmt:
                on_disk.setdefault(m.group(2), f)

    plan = []                                   # (job, key, action, source file)
    for j in chosen:
        key = cache_key(j)
        want = clip_name(j.id, key, fmt)
        if (out / want).exists():
            plan.append((j, key, 'cached', out / want))
        elif key[:10] in on_disk:
            plan.append((j, key, 'reuse', on_disk[key[:10]]))
        else:
            plan.append((j, key, 'render', None))

    todo = [p for p in plan if p[2] == 'render']
    est = sum(estimate_seconds(p[0]) for p in todo)
    summary = {'lines': len(chosen), 'cached': sum(p[2] == 'cached' for p in plan),
               'reused': sum(p[2] == 'reuse' for p in plan), 'render': len(todo),
               'estimate_audio_s': est, 'estimate_time_s': (MODEL_LOAD + est * RENDER_FACTOR) if todo else 0,
               'by_speaker': Counter(p[0].speaker for p in todo), 'warnings': [], 'format': fmt}
    summary['substituted'] = [j.id for j in chosen if any(m.group(2).strip().startswith('=')
                                                          for m in marks_mod.MARK_RE.finditer(j.text))]
    if dry_run:
        return summary

    out.mkdir(parents=True, exist_ok=True)
    manifest['format'] = fmt
    all_ids = {j.id for j in jobs}
    # lines outside --only keep their clips; IDs that left the script are dropped (their files stay until --prune)
    clips = {k: v for k, v in old.items() if k in all_ids and (k in chosen_ids or (out / v.get('file', '')).exists())}
    manifest['clips'] = clips

    # cheap work first: an old clip with the same key is moved to its new name, or copied if anything
    # else (another line, or a line outside --only) still needs the old file
    uses = Counter(src for _, _, action, src in plan if action == 'reuse')
    kept_files = {v.get('file') for k, v in clips.items() if k not in chosen_ids}
    kept_files |= {src.name for _, _, action, src in plan if action == 'cached'}
    for j, key, action, src in plan:
        name = clip_name(j.id, key, fmt)
        if action == 'reuse':
            dst = out / name
            uses[src] -= 1
            if uses[src] > 0 or src.name in kept_files:
                dst.write_bytes(src.read_bytes())
            else:
                try:
                    src.rename(dst)
                except OSError:
                    dst.write_bytes(src.read_bytes())
        if action in ('cached', 'reuse'):
            prev = clips.get(j.id, {})
            secs = prev.get('seconds') if prev.get('key') == key else None
            if secs is None:
                secs = duration(out / name)
            clips[j.id] = {'file': name, 'key': key, 'speaker': j.speaker, 'text': j.text, 'seconds': secs}
    save_manifest(out, manifest)

    if todo:
        from .engine import Engine, write
        t0 = time.time()
        eng = Engine(model_dir=model_dir)
        log(f'model loaded in {time.time() - t0:.0f} s; rendering {len(todo)} clips (~{est / 60:.1f} min of audio)')
        done_audio = 0.0
        try:
            for n, (j, key, _, _) in enumerate(todo, 1):
                t1 = time.time()
                r = eng.render(j.text, j.voice, j.direction, seed=j.seed)
                path = write(out / clip_name(j.id, key, fmt), r.audio, fmt, warn=log)
                secs = len(r.audio) / 24000
                done_audio += secs
                clips[j.id] = {'file': path.name, 'key': key, 'speaker': j.speaker, 'text': j.text,
                               'seconds': round(secs, 2)}
                for w in r.unknown_words:
                    summary['warnings'].append(f'{j.id}: the phonemizer could not read "{w}"')
                for w in r.unplaced_marks:
                    summary['warnings'].append(f'{j.id}: the mark on "{w}" did not land on a word')
                save_manifest(out, manifest)
                left = (est - done_audio) * RENDER_FACTOR
                log(f'[{n}/{len(todo)}] {j.id:<10} {secs:5.1f} s audio in {time.time() - t1:4.1f} s'
                    + (f'   ~{fmt_time(left)} left' if n < len(todo) else ''))
        except KeyboardInterrupt:
            save_manifest(out, manifest)
            summary['interrupted'] = True
            log('interrupted: finished clips are saved; run again to continue')

    if prune:
        keep = {v['file'] for k, v in clips.items()}
        removed = 0
        for f in out.iterdir():
            if CLIP_RE.match(f.name) and f.name not in keep:
                f.unlink(); removed += 1
        summary['pruned'] = removed
        save_manifest(out, manifest)
    return summary


def print_summary(s, log=print, dry_run=False):
    head = '(dry run) ' if dry_run else ''
    log(f'{head}{s["lines"]} lines: {s["cached"]} up to date, {s["reused"]} reused from renamed clips, '
        f'{s["render"]} to render' + ('' if dry_run else ' (done)' if not s.get('interrupted') else ' (interrupted)'))
    if s['render'] and dry_run:
        log(f'  about {s["estimate_audio_s"] / 60:.1f} min of audio, roughly {fmt_time(s["estimate_time_s"])} '
            f'to render on CPU')
        log('  ' + ', '.join(f'{who} {n}' for who, n in s['by_speaker'].most_common()))
    if s['substituted']:
        log(f'  lines that say different words from the screen ({len(s["substituted"])}): '
            + ', '.join(s['substituted']))
    if s.get('pruned') is not None:
        log(f'  pruned {s["pruned"]} unused clip files')
    for w in s['warnings']:
        log('  ! ' + w)
