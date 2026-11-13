"""Word marks inside a line of text.

    [word](+)      stress            [word](++)   strong stress     [word](-)  de-stress
    [word](~)      drawn out         [word](~~)   very drawn out    marks combine: [word](+~)
    [Harris](/hˈɛɹɪs/)               respelling in IPA (misaki's own syntax, passed through to it)
    [Dr.](=Doctor)                   say different words; the screen text stays "Dr." ([um](=) = silent)

The bracketed text may hold several words; the mark applies to all of them.
"""
import re
from dataclasses import dataclass

MARK_RE = re.compile(r'\[([^\]]+)\]\(([^)]*)\)')


@dataclass(frozen=True)
class Mark:
    start: int          # character span in the plain text
    end: int
    stress: int = 0     # -1, 0, 1, 2
    stretch: int = 0    # 0, 1, 2

    def __bool__(self):
        return bool(self.stress or self.stretch)


def parse_mark_spec(spec):
    s = spec.replace(' ', '')
    if not s or set(s) - set('+-~'):
        raise ValueError(f'unknown word mark "({spec})": use +, ++, -, ~, ~~, /ipa/ or =words')
    plus, minus, tilde = s.count('+'), s.count('-'), s.count('~')
    if (plus and minus) or plus > 2 or minus > 1 or tilde > 2:
        raise ValueError(f'unknown word mark "({spec})"')
    return (plus or -minus), tilde


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
            stress, stretch = parse_mark_spec(spec)
            g2p.append(word)
            marks.append(Mark(n, n + len(word), stress, stretch))
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
