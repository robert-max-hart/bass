"""Word marks inside a line of text.

    [word](+)      stress            [word](++)   strong stress     [word](-)  de-stress
    [word](~)      drawn out         [word](~~)   very drawn out    marks combine: [word](+~)
    up to five of each stack for more: [word](+++), [word](~~~~), [word](+++++~~~) (3-5 are untuned extremes)
    and up to three -: [word](--), [word](---)
    [word](^1.5)   pitch +1.5 st     [word](^2>-1)  start 2 up, glide to 1 down   [word](^0>3>0)  any number of points
    [word](|)      0.2 s pause after [word](||) ... (|||||) 0.2 s per bar, up to 1 s   [word](|0.8)  exactly 0.8 s
    all combine: [word](++^2>0||)
    [Harris](/hˈɛɹɪs/)               respelling in IPA (misaki's own syntax, passed through to it)
    [Dr.](=Doctor)                   say different words; the screen text stays "Dr." ([um](=) = silent)

The bracketed text may hold several words; the mark applies to all of them.
"""
import re
from dataclasses import dataclass

from .styles import DESTRESS_LEVELS, MARK_LEVELS, PAUSE_MARKS, PAUSE_MAX, SHAPE_MAX

MARK_RE = re.compile(r'\[([^\]]+)\]\(([^)]*)\)')


@dataclass(frozen=True)
class Mark:
    start: int          # character span in the plain text
    end: int
    stress: int = 0     # -DESTRESS_LEVELS .. MARK_LEVELS
    stretch: int = 0    # 0 .. styles.MARK_LEVELS
    shape: tuple = ()   # semitones spread evenly across the word(s): (1.5,) shift, (2, -1) glide, (0, 3, 0) ...
    pause: int = 0      # pause after the word(s): 1-5 bars, 0.2 s each
    pause_s: float = 0  # ... or an exact length in seconds (|0.8)

    def __bool__(self):
        return bool(self.stress or self.stretch or self.shape or self.pause or self.pause_s)


NUM = r'-?(?:\d+(?:\.\d*)?|\.\d+)'
SHAPE_RE = re.compile(rf'\^({NUM}(?:>{NUM})*)')
PAUSE_S_RE = re.compile(r'\|(\d+(?:\.\d*)?|\.\d+)')


def parse_mark_spec(spec):
    """'++~^2>-1|' -> {'stress', 'stretch', 'shape', 'pause', 'pause_s'} (the Mark fields)"""
    s = spec.replace(' ', '')
    shapes = SHAPE_RE.findall(s)
    if len(shapes) > 1:
        raise ValueError(f'word mark "({spec})": one ^ pitch shape per mark')
    s = SHAPE_RE.sub('', s)
    secs = PAUSE_S_RE.findall(s)
    s = PAUSE_S_RE.sub('', s)
    if not spec.strip() or set(s) - set('+-~|'):
        raise ValueError(f'unknown word mark "({spec})": use +, -, ~, ^semitones, | (pause), /ipa/ or =words')
    plus, minus, tilde, bars = s.count('+'), s.count('-'), s.count('~'), s.count('|')
    if plus and minus:
        raise ValueError(f'word mark "({spec})": + and - don\'t combine (use ^ to shape the pitch)')
    if plus > MARK_LEVELS or tilde > MARK_LEVELS or minus > DESTRESS_LEVELS:
        raise ValueError(f'word mark "({spec})": at most {MARK_LEVELS} +, {MARK_LEVELS} ~ and {DESTRESS_LEVELS} -')
    if len(secs) > 1 or (secs and bars) or bars > len(PAUSE_MARKS):
        raise ValueError(f'word mark "({spec})": one pause per mark: | to {"|" * len(PAUSE_MARKS)} (0.2 s each) or |seconds')
    shape = tuple(float(x) for x in shapes[0].split('>')) if shapes else ()
    if any(abs(x) > SHAPE_MAX for x in shape):
        raise ValueError(f'word mark "({spec})": ^ values go from -{SHAPE_MAX:g} to {SHAPE_MAX:g} semitones')
    pause_s = float(secs[0]) if secs else 0.0
    if pause_s > PAUSE_MAX:
        raise ValueError(f'word mark "({spec})": a pause is at most {PAUSE_MAX:g} seconds')
    return dict(stress=plus or -minus, stretch=tilde, shape=shape, pause=bars, pause_s=pause_s)


@dataclass
class MarkedText:
    plain: str          # what is shown on screen (markup removed)
    g2p: str            # what is spoken (respellings kept in misaki syntax, =substitutions applied)
    marks: list         # [Mark]
    align: str = ''     # plain text with =substituted words blanked out (for matching tokens to words)


def parse(text):
    plain, g2p, align, marks, pos = [], [], [], [], 0
    n = 0                                  # length of plain so far
    for m in MARK_RE.finditer(text):
        before = text[pos:m.start()]
        plain.append(before); g2p.append(before); align.append(before); n += len(before)
        word, spec = m.group(1), m.group(2).strip()
        if spec.startswith('/'):
            g2p.append(m.group(0))
        elif spec.startswith('='):
            g2p.append(spec[1:].strip())
        else:
            mk = Mark(n, n + len(word), **parse_mark_spec(spec))
            g2p.append(word)
            # a pause needs a mark the model can hold: a comma, spoken only (unless punctuation follows anyway)
            if (mk.pause or mk.pause_s) and not re.match(r'\s*[,.;:!?…—–]', text[m.end():]):
                g2p.append(',')
            marks.append(mk)
        plain.append(word); n += len(word)
        align.append(' ' * len(word) if spec.startswith('=') else word)
        pos = m.end()
    plain.append(text[pos:]); g2p.append(text[pos:]); align.append(text[pos:])
    # misaki drops a spaced hyphen ("eyes - easy") without a pause; an en/em dash it reads as one
    spoken = re.sub(r'(?<=\s)-+(?=\s)', '—', ''.join(g2p))
    return MarkedText(''.join(plain), spoken, marks, ''.join(align))


def apply_pronounce(text, table):
    """respell every unmarked occurrence of each word in table ({word: '/ipa/'}) as [word](/ipa/). Words that
    already carry a mark keep it as written (a mark holds either a stress or a respelling)."""
    if not table:
        return text
    pat = re.compile(r"(?<![\w'’])(" + '|'.join(re.escape(w) for w in sorted(table, key=len, reverse=True)) +
                     r")(?![\w'’])")
    out, pos = [], 0
    for m in list(MARK_RE.finditer(text)) + [None]:
        end = m.start() if m else len(text)
        out.append(pat.sub(lambda w: f'[{w.group(1)}]({table[w.group(1)]})', text[pos:end]))
        if m:
            out.append(m.group(0)); pos = m.end()
    return ''.join(out)


def has_marks(text):
    return bool(MARK_RE.search(text))


def marked_words(text):
    """[(word, spec, occurrence of that word in the plain text before it)] - used to carry marks over to an
    edited version of the same line"""
    out, plain_before = [], ''
    pos = 0
    for m in MARK_RE.finditer(text):
        plain_before += MARK_RE.sub(lambda x: x.group(1), text[pos:m.start()])
        word = m.group(1)
        out.append((word, m.group(2), _count_word(plain_before, word)))
        plain_before += word
        pos = m.end()
    return out


def _count_word(text, word):
    return len(re.findall(r'(?<!\w)' + re.escape(word) + r'(?!\w)', text))


def apply_marks(plain, wanted):
    """re-apply [(word, spec, occurrence)] to an unmarked text; returns (marked text, [words not found])"""
    spans, missing = [], []
    for word, spec, occ in wanted:
        hits = [m for m in re.finditer(r'(?<!\w)' + re.escape(word) + r'(?!\w)', plain)]
        if not hits:
            missing.append(word); continue
        h = hits[min(occ, len(hits) - 1)]
        if any(a < h.end() and h.start() < b for a, b, _ in spans):
            continue
        spans.append((h.start(), h.end(), spec))
    out, pos = [], 0
    for a, b, spec in sorted(spans):
        out += [plain[pos:a], f'[{plain[a:b]}]({spec})']
        pos = b
    out.append(plain[pos:])
    return ''.join(out), missing
