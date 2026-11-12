"""Styles, settings and how a direction such as "trance 0.6, internal, pause 2" resolves to engine settings.

Resolution order: DEFAULTS -> the style -> the line's own settings.
- `speed` in a direction sets the speaker's base speed; the style's speed then scales it
  (so `{deep, speed 0.9}` = 0.9 x 0.75).
- `pitch` and `volume` in a direction add to the style's; every other setting replaces the style's value.
"""
from dataclasses import dataclass, field

DEFAULTS = dict(speed=0.95, pitch=0.0, range_=1.0, drift=0.0, pause=1.0, floor=0.0, human=1.0,
                volume=0.0, take=0)

# ---- approved styles (2026-11-11 listening rounds; names in quotes are the round labels)
TRANCE_LIGHT = dict(speed=0.85, pitch=-1.5, range_=0.45, pause=1.2, drift=0.0)   # "t02", depth 0
TRANCE_DEEP = dict(speed=0.75, pitch=-3.5, range_=0.20, pause=2.5, drift=1.0)    # "t04", depth 1


def trance(depth):
    depth = min(1.0, max(0.0, depth))
    return {k: TRANCE_LIGHT[k] + (TRANCE_DEEP[k] - TRANCE_LIGHT[k]) * depth for k in TRANCE_LIGHT}


STYLES = {
    'normal': dict(),
    'deep': dict(speed=0.75, pitch=-3.0, range_=0.0, pause=2.0),                 # "m05": dead-flat monotone
    'shout': dict(pitch=4.0, range_=1.4, floor=0.55),                             # "s06/s07"
    'soft': dict(speed=0.88, pitch=-2.0, range_=0.55, floor=0.55),                # "p01", with SOFT_DELIVERY
    'whisper': dict(speed=0.88, range_=0.6),                                      # "w65", whisper renderer
}
STYLE_LEVEL_DB = {'soft': -10.0, 'whisper': -12.0}   # ear-weighted, relative to a normal line
SOFT_DELIVERY = 'af_nicole'                           # soft = the voice's timbre + this voice's delivery
EFFECTS = ('internal',)                               # post effects that combine with any style

# ---- word marks -> per-word edits
STRESS = {1: dict(pitch=1.5, dur=1.12), 2: dict(pitch=3.0, dur=1.25), -1: dict(pitch=-1.0, dur=0.9)}
STRETCH = {1: 1.5, 2: 2.0}

SETTING_NAMES = {'speed': 'speed', 'pitch': 'pitch', 'range': 'range_', 'drift': 'drift', 'pause': 'pause',
                 'floor': 'floor', 'human': 'human', 'volume': 'volume', 'take': 'take'}
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
    """later directions win: a set style replaces the earlier one; effects add up; settings replace"""
    out = Direction()
    for d in directions:
        if d is None:
            continue
        if d.style:
            out.style, out.depth = d.style, d.depth
        out.effects += [e for e in d.effects if e not in out.effects]
        out.settings.update(d.settings)
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
