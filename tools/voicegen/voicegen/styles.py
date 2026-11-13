"""Styles, settings and how a direction such as "trance 0.6, internal, pause 2" resolves to engine settings.

Resolution order: DEFAULTS -> the style -> the line's own settings.
- `speed` in a direction sets the speaker's base speed; the style's speed then scales it
  (so `{deep, speed 0.9}` = 0.9 x 0.75).
- `pitch` and `volume` in a direction add to the style's; every other setting replaces the style's value.
"""
import re
from dataclasses import dataclass, field

DEFAULTS = dict(speed=0.95, pitch=0.0, range_=1.0, drift=0.0, pause=1.0, floor=0.0, human=1.0,
                volume=0.0, take=0)

# ---- approved styles (2026-11-11 listening rounds; names in quotes are the round labels)
# pause values were 1.2 / 2.5 / 2.0 (deep) when `pause` stretched the model's own (tiny) punctuation; now it
# scales real inserted silence (PAUSES), so they're gentler.
TRANCE_LIGHT = dict(speed=0.85, pitch=-1.5, range_=0.45, pause=1.15, drift=0.0)  # "t02", depth 0
TRANCE_DEEP = dict(speed=0.75, pitch=-3.5, range_=0.20, pause=1.8, drift=1.0)    # "t04", depth 1


def trance(depth):
    depth = min(1.0, max(0.0, depth))
    return {k: TRANCE_LIGHT[k] + (TRANCE_DEEP[k] - TRANCE_LIGHT[k]) * depth for k in TRANCE_LIGHT}


STYLES = {
    'normal': dict(),
    'deep': dict(speed=0.75, pitch=-3.0, range_=0.0, pause=1.6),               # "m05": dead-flat monotone
    'shout': dict(pitch=4.0, range_=1.4, floor=0.55),                             # "s06/s07"
    'soft': dict(speed=0.9, pitch=-2.0, range_=0.6, floor=0.55, breath=0.15),   # own voice, a trace of whisper
    'whisper': dict(speed=0.88, range_=0.6),                                      # "w65", whisper renderer
}
STYLE_LEVEL_DB = {'soft': -8.0, 'whisper': -12.0}    # ear-weighted, relative to a normal line
# (soft used to borrow af_nicole's delivery, which made e141 sound like another person; 2026-11-12 it became
# the speaker's own voice with breath mixed in. `breath` works on any style except whisper.)
EFFECTS = ('internal',)                               # post effects that combine with any style

# ---- word marks -> per-word edits
# Stress is relative: the word's peak is lifted to `over` semitones above the peaks of the words around it
# (at least `pitch`, at most STRESS_MAX), then eases down `fall` semitones across the word; `gain` dB louder;
# `dur` = length factor. (2026-11-12 round 1: a flat +1.5 st was inaudible on "have you ever WANTED", where
# the model already peaks on "ever"; ++ on "finally" was too high and didn't ease down enough; round 2: ++
# start lower and end below the unstressed "finally" before it.)
STRESS = {1: dict(over=1.5, pitch=1.5, fall=2.0, gain=2.0, dur=1.15),
          2: dict(over=2.0, pitch=2.0, fall=4.0, gain=3.0, dur=1.25),
          -1: dict(pitch=-1.0, dur=0.9, gain=-1.5)}
STRESS_MAX = 6.0
STRETCH = {1: 1.5, 2: 2.0}
SUSTAIN_SMOOTH = 4.0    # F0 samples (12.5 ms): pitch smoothing inside drawn-out words
SUSTAIN_ENERGY = 1.5    # drawn-out words keep their energy at least this far below the line's median (N units)
STRETCH_CAP = 0.35      # seconds at speed 1: a drawn-out word stretches no single sound past this (e209)

# ---- pauses: Kokoro holds punctuation inside a line for only 30-50 ms (it only pauses properly at the end
# of the line), so the engine lengthens the mark to these totals (seconds at `pause 1`, including the spaces
# after it; `pause` scales them) with voicing and energy off over it (see QUIET_*).
PAUSES = dict(comma=0.18, clause=0.28, dash=0.28, sentence=0.42, ellipsis=0.55)
PAUSE_HUMAN = 0.12      # lognormal sigma of each pause, times `human`

# ---- quiet: on punctuation and the line's pads the model keeps humming (pitch) and breathing (energy N);
# both are switched off there. N around -5 is what the model itself predicts for silence.
QUIET_ENERGY = -5.0
QUIET_EASE = 1.5       # F0 samples (12.5 ms each): how softly energy eases into and out of the quiet

# ---- length cap: the model now and then holds a sound far too long (the "r" of "sweater" for 0.95 s).
# Seconds at speed 1 (divided by speed): inner = inside a phrase, phrase_end = before mid-line punctuation,
# gap = a space between words. The last sound of the line isn't capped.
LENGTH_CAP = dict(inner=0.30, phrase_end=0.50, gap=0.15)

# ---- pitch bridge: the predicted F0 sometimes drops out (or by about an octave) over sounds that should be
# voiced, and the decoder goes nearly silent there ("thorough" lost its second syllable). Drops below `ratio`
# x the local median (window `win` F0 samples each side, 80 per second), at most `max_len` samples long,
# voiced on both sides and only over vowels/sonorants, are filled in.
PITCH_BRIDGE = dict(ratio=0.7, win=12, max_len=24)

# ---- question rise: Kokoro says "Julia?" like a statement (a peak on the stressed syllable, then a fall), so the
# word before a "?" gets its own contour flattened and a rise laid on top: gradual at first, then sharp.
# auto: semitones used on one-word questions when the line doesn't set `rise`; flatten: 0..1, how much of the
# model's own movement on that word is removed; gradual: the share of the rise that is linear (the rest
# comes as t**power, i.e. at the end).
# auto_multi: semitones on longer questions without a wh-word at the start of a clause (round 1: "Maybe... back
# when I was a kid?" was said as a statement).
QUESTION = dict(auto=5.0, auto_multi=3.0, flatten=0.9, gradual=0.3, power=4.0)

SETTING_NAMES = {'speed': 'speed', 'pitch': 'pitch', 'range': 'range_', 'drift': 'drift', 'pause': 'pause',
                 'floor': 'floor', 'human': 'human', 'volume': 'volume', 'take': 'take', 'rise': 'rise',
                 'breath': 'breath'}
ADDITIVE = ('pitch', 'volume')


@dataclass
class Direction:
    style: str = None                    # None = not set here (inherits; 'normal' in the end)
    depth: float = None                  # trance depth
    effects: list = field(default_factory=list)
    settings: dict = field(default_factory=dict)    # engine names (range_, ...)

    def __str__(self):
        parts = []
        if self.style:
            parts.append(self.style if self.depth is None else f'{self.style} {self.depth:g}')
        parts += self.effects
        inv = {v: k for k, v in SETTING_NAMES.items()}
        parts += [f'{inv[k]} {v:g}' for k, v in self.settings.items()]
        return ', '.join(parts)


def parse_direction(text):
    """'trance 0.6, internal, pause 2' -> Direction. An empty string is 'normal'."""
    d = Direction()
    styled = False
    for part in filter(None, (p.strip() for p in (text or '').split(','))):
        words = part.split()
        name = words[0].lower()
        if name in STYLES or name == 'trance':
            if styled:
                raise ValueError(f'two styles in "{text}"')
            styled = True
            d.style = name
            if name == 'trance':
                d.depth = float(words[1]) if len(words) > 1 else 0.5
            elif len(words) > 1:
                raise ValueError(f'"{name}" takes no value')
        elif name in EFFECTS:
            d.effects.append(name)
        elif name in SETTING_NAMES:
            if len(words) != 2:
                raise ValueError(f'"{name}" needs one number, e.g. "{name} 1.2"')
            v = float(words[1])
            d.settings[SETTING_NAMES[name]] = int(v) if name == 'take' else v
        else:
            raise ValueError(f'unknown direction "{part}"')
    return d


def merge(*directions):
    """later directions win: a set style replaces the earlier one; effects add up; settings replace, except
    pitch and volume, which add up (so a speaker's `@cast ... {pitch 1}` survives a line's own `pitch -1`)"""
    out = Direction()
    for d in directions:
        if d is None:
            continue
        if d.style:
            out.style, out.depth = d.style, d.depth
        out.effects += [e for e in d.effects if e not in out.effects]
        for k, v in d.settings.items():
            out.settings[k] = out.settings.get(k, 0) + v if k in ADDITIVE else v
    return out


def resolve(direction):
    """Direction -> flat engine settings (plus 'style', 'effects', 'level_db')."""
    s = dict(DEFAULTS)
    base_speed = direction.settings.get('speed', s['speed'])
    name = direction.style or 'normal'
    style = trance(direction.depth) if name == 'trance' else STYLES[name]
    s.update(style)
    s['speed'] = base_speed * style.get('speed', 1.0)
    for k, v in direction.settings.items():
        if k == 'speed':
            continue
        s[k] = s[k] + v if k in ADDITIVE else v
    s['style'] = name
    s['effects'] = list(direction.effects)
    s['level_db'] = STYLE_LEVEL_DB.get(name, 0.0) + s.pop('volume')
    return s


WH_WORDS = {'who', 'whom', 'whose', 'what', 'when', 'where', 'why', 'how', 'which'}
WORD_RE = re.compile(r"[^\W\d_][\w'’-]*")


def question_rises(settings, plain):
    """semitones of rise for each question ("?" run) in the line, in order. The line's `rise` applies to all.
    Otherwise: a one-word question gets QUESTION['auto']; a longer one QUESTION['auto_multi'], unless one of its
    clauses starts with a wh-word ("So Julia, when was...?"), since those fall. (`rise` is not in DEFAULTS,
    so lines without it keep their cache keys.)"""
    out, start = [], 0
    for m in re.finditer(r'\?+', plain):
        if 'rise' in settings:
            out.append(settings['rise'])
        else:
            sentence = re.split(r'[.!?…]+(?=\s|$)', plain[start:m.start()])[-1]
            words = WORD_RE.findall(sentence)
            clauses = re.split(r'[,;:—–…]|\.\.\.|\s-+\s', sentence)
            firsts = {w[0].lower() for w in (WORD_RE.findall(c) for c in clauses) if w}
            out.append(0.0 if not words else QUESTION['auto'] if len(words) == 1 else
                       0.0 if firsts & WH_WORDS else QUESTION['auto_multi'])
        start = m.end()
    return out
