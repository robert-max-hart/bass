"""The voice script: a hand-editable text file with one line per clip. Full guide: VOICE_SCRIPT.md.

    # comment
    @voices ../voices                          folder for voice names (relative to this file)
    @cast narrator = base_narrator             speaker = voice (a name in @voices, a .pt path, or a stock voice)
    @cast Doctor Harris = harris | ???         ... | aliases
    @cast Julia (thought) = julia {internal}   ... {a default direction for this speaker}
    @defaults speed 0.95, human 1.0            direction for every line
    @pronounce Julia = /ʤˈulʲə/                 say this word this way wherever it isn't marked otherwise

    == Chapter 1 ==                            a section heading (for reading only)
    c1.e3    Julia (thought): But what if they do find something [wrong](+)?
    c1.e9    !changed Julia {deep, take 2}: yes... yes...

    @ramp trance 0.2 -> 1.0                    deepen across the following lines of one speaker
    c1.e170  Doctor Harris: Let everything else fade away...
    @end

Each line: ID (any text without spaces, unique), optional !flags, speaker, optional {direction}, ':' and text.
`@ramp <trance|setting> a -> b [for Speaker]` applies to the lines of `Speaker` (default: the first line's
speaker) up to `@end`; a line's own direction wins over the ramp.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import marks as marks_mod, styles
from .common import STOCK_PREFIXES, make_seed

LINE_RE = re.compile(r'^(\S+)\s+((?:![\w-]+\s+)*)([^{:]+?)\s*(?:\{([^}]*)\})?\s*:\s?(.*)$')
RAMP_RE = re.compile(r'^@ramp\s+(\w+)\s+(-?[\d.]+)\s*->\s*(-?[\d.]+)(?:\s+for\s+(.+))?$')
SECTION_RE = re.compile(r'^==\s*(.*?)\s*==$')


@dataclass
class Line:
    id: str
    speaker: str
    direction: str          # as written, without braces
    text: str               # with word marks
    flags: list = field(default_factory=list)
    lineno: int = 0
    comments: list = field(default_factory=list)   # comment lines written directly above it

    @property
    def plain(self):
        return marks_mod.parse(self.text).plain


@dataclass
class Ramp:
    what: str
    a: float
    b: float
    speaker: str = None
    lineno: int = 0
    lines: list = field(default_factory=list)

    def header(self):
        num = lambda x: f'{x:.1f}' if x == int(x) else f'{x:g}'
        s = f'@ramp {self.what} {num(self.a)} -> {num(self.b)}'
        return s + (f' for {self.speaker}' if self.speaker else '')


@dataclass
class Cast:
    name: str
    voice: str
    aliases: list = field(default_factory=list)
    direction: str = ''


@dataclass
class Job:
    """one clip to render, fully resolved"""
    id: str
    speaker: str            # canonical cast name
    voice: str              # .pt path or stock name
    direction: styles.Direction
    text: str
    seed: int
    lineno: int


class Script:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.header = []        # raw lines before the first section/line (directives + comments)
        self.items = []         # ('section', title) | ('line', Line) | ('ramp', Ramp) | ('end', None) | ('comment', s)
        self.cast = {}
        self.aliases = {}
        self.defaults = ''
        self.voices = None
        self.pronounce = {}     # word -> '/ipa/'
        self.errors = []        # (lineno, message)

    # ------------------------------------------------------------------ reading
    @classmethod
    def load(cls, path):
        return cls.parse(Path(path).read_text(encoding='utf-8'), path)

    @classmethod
    def parse(cls, text, path=None):
        sc = cls(path)
        pending, ramp, body = [], None, False
        for n, raw in enumerate(text.splitlines(), 1):
            s = raw.strip()
            if not s:
                if body and pending:
                    sc.items += [('comment', c) for c in pending]; pending = []
                continue
            if s.startswith('#'):
                (pending if body else sc.header).append(s); continue
            if s.startswith('@') and not s.startswith(('@ramp', '@end')):
                if body:
                    sc.items += [('comment', c) for c in pending]; pending = []
                    sc.items.append(('directive', s))
                else:
                    sc.header.append(s)
                sc._directive(s, n); continue
            body = True
            if pending and not LINE_RE.match(s):
                sc.items += [('comment', c) for c in pending]; pending = []
            if m := SECTION_RE.match(s):
                sc.items.append(('section', m.group(1)))
            elif m := RAMP_RE.match(s):
                if ramp:
                    sc.errors.append((n, 'a @ramp inside another @ramp (missing @end?)'))
                ramp = Ramp(m.group(1), float(m.group(2)), float(m.group(3)),
                            m.group(4).strip() if m.group(4) else None, n)
                if ramp.what != 'trance' and ramp.what not in styles.SETTING_NAMES:
                    sc.errors.append((n, f'@ramp: "{ramp.what}" is not trance or a setting'))
                sc.items.append(('ramp', ramp))
            elif s == '@end':
                if not ramp:
                    sc.errors.append((n, '@end without @ramp'))
                ramp = None
                sc.items.append(('end', None))
            elif s.startswith('@ramp'):
                sc.errors.append((n, 'bad @ramp; write e.g. "@ramp trance 0.2 -> 1.0"'))
            elif m := LINE_RE.match(s):
                ln = Line(m.group(1), m.group(3).strip(), (m.group(4) or '').strip(), m.group(5),
                          m.group(2).replace('!', ' ').split(), n, pending)
                pending = []
                if ramp:
                    ramp.lines.append(ln)
                sc.items.append(('line', ln))
            else:
                sc.errors.append((n, 'not understood (a line is "ID Speaker {direction}: text")'))
        if ramp:
            sc.errors.append((ramp.lineno, '@ramp without @end'))
        sc.items += [('comment', c) for c in pending]
        return sc

    def _directive(self, s, n):
        word, _, rest = s.partition(' ')
        rest = rest.strip()
        if word == '@voices':
            self.voices = rest
        elif word == '@defaults':
            self.defaults = rest.strip('{} ')
        elif word == '@pronounce':
            w, eq, ipa = rest.partition('=')
            w, ipa = w.strip(), ipa.strip()
            if not eq or not w or not (ipa.startswith('/') and ipa.endswith('/') and len(ipa) > 2):
                self.errors.append((n, '@pronounce needs "word = /ipa/"')); return
            self.pronounce[w] = ipa
        elif word == '@cast':
            name, eq, val = rest.partition('=')
            if not eq or not name.strip() or not val.strip():
                self.errors.append((n, '@cast needs "Name = voice"')); return
            d = re.search(r'\{([^}]*)\}', val)
            direction = d.group(1).strip() if d else ''
            val = re.sub(r'\{[^}]*\}', '', val)
            voice, *aliases = [x.strip() for x in val.split('|')]
            c = Cast(name.strip(), voice, [a for a in aliases if a], direction)
            self.cast[c.name] = c
            for a in [c.name] + c.aliases:
                self.aliases[a] = c.name
        else:
            self.errors.append((n, f'unknown directive {word}'))

    @property
    def lines(self):
        return [x for k, x in self.items if k == 'line']

    @property
    def ramps(self):
        return [x for k, x in self.items if k == 'ramp']

    # ------------------------------------------------------------------ resolving
    def voices_dir(self, override=None):
        if override:
            return Path(override)
        base = self.path.parent if self.path else Path('.')
        return (base / self.voices) if self.voices else base

    def voice_path(self, voice, voices_dir=None):
        p = Path(voice)
        if voice.startswith(STOCK_PREFIXES) and not p.suffix:
            return voice
        if not p.suffix:
            p = p.with_suffix('.pt')
        if not p.is_absolute():
            p = self.voices_dir(voices_dir) / p
        return str(p)

    def jobs(self, voices_dir=None):
        """-> ([Job], [(lineno, error)]); every problem is reported, not just the first"""
        errors = list(self.errors)
        try:
            defaults = styles.parse_direction(self.defaults)
        except ValueError as e:
            errors.append((0, f'@defaults: {e}')); defaults = None
        cast_dirs = {}
        for c in self.cast.values():
            try:
                cast_dirs[c.name] = styles.parse_direction(c.direction)
            except ValueError as e:
                errors.append((0, f'@cast {c.name}: {e}'))
            vp = self.voice_path(c.voice, voices_dir)
            if not vp.startswith(STOCK_PREFIXES) and not Path(vp).exists():
                errors.append((0, f'@cast {c.name}: voice file not found: {vp}'))

        ramp_dirs = {}
        for r in self.ramps:
            who = self.aliases.get(r.speaker, r.speaker) if r.speaker else \
                (self.aliases.get(r.lines[0].speaker) if r.lines else None)
            mine = [ln for ln in r.lines if self.aliases.get(ln.speaker) == who]
            for i, ln in enumerate(mine):
                v = r.a + (r.b - r.a) * (i / (len(mine) - 1) if len(mine) > 1 else 0.0)
                ramp_dirs[id(ln)] = (styles.Direction(style='trance', depth=v) if r.what == 'trance'
                                     else styles.Direction(settings={styles.SETTING_NAMES[r.what]: v}))

        jobs, seen_ids, occurrences = [], set(), {}
        for ln in self.lines:
            if ln.id in seen_ids:
                errors.append((ln.lineno, f'duplicate ID {ln.id}'))
            seen_ids.add(ln.id)
            who = self.aliases.get(ln.speaker)
            if not who:
                errors.append((ln.lineno, f'unknown speaker "{ln.speaker}" (add a @cast line)')); continue
            try:
                mt = marks_mod.parse(ln.text)
                own = styles.parse_direction(ln.direction)
                d = styles.merge(defaults, cast_dirs.get(who), ramp_dirs.get(id(ln)), own)
                styles.resolve(d)
            except ValueError as e:
                errors.append((ln.lineno, str(e))); continue
            key = (who, mt.plain)
            occ = occurrences.get(key, 0); occurrences[key] = occ + 1
            jobs.append(Job(ln.id, who, self.voice_path(self.cast[who].voice, voices_dir), d,
                            marks_mod.apply_pronounce(ln.text, self.pronounce),
                            make_seed(who, mt.plain, occ), ln.lineno))
        return jobs, errors

    # ------------------------------------------------------------------ writing
    @staticmethod
    def format_line(ln, id_width=8):
        flags = ''.join(f'!{f} ' for f in ln.flags)
        d = f' {{{ln.direction}}}' if ln.direction else ''
        return f'{ln.id:<{id_width}} {flags}{ln.speaker}{d}: {ln.text}'

    def dump(self):
        width = max([len(ln.id) for ln in self.lines] + [6]) + 1
        out = list(self.header)
        prev = 'header'
        for kind, x in self.items:
            if kind == 'section':
                out += ['', f'== {x} ==']
            elif kind == 'line':
                if prev in ('section', 'header'):
                    out.append('')
                out += x.comments
                out.append(self.format_line(x, width))
            elif kind == 'ramp':
                out += ['', x.header()]
            elif kind == 'end':
                out += ['@end', '']
            elif kind in ('comment', 'directive'):
                out.append(x)
            prev = kind
        while out and out[-1] == '':
            out.pop()
        return '\n'.join(out) + '\n'

    def save(self, path=None):
        Path(path or self.path).write_text(self.dump(), encoding='utf-8', newline='\n')
