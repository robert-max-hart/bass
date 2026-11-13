"""Voice lab: build Kokoro voice packs from recipes (blends, offsets along named axes), and recover recipes.

A voice pack is [510, 1, 256]: one style vector per utterance length (row = phoneme count). The first 128
numbers drive the decoder (timbre: what the voice sounds like), the last 128 the prosody predictor (delivery:
intonation, pacing, energy). A recipe sets each half on its own:

    {"voicegen_recipe": 1,
     "timbre":   {"mix": {"af_aoede": 0.5, "jf_tebukuro": 0.3, "af_jessica": 0.2}, "axes": {"gender": 0.4}},
     "delivery": {"mix": {"af_nicole": 1}, "axes": {"pc2": -0.8}}}

mix  : source -> weight. Weights are divided by their sum, so {"a": 1.3, "b": -0.3} pushes past a, away from b.
       A source is a stock voice name or a .pt path (relative to the recipe file).
axes : axis -> amount in standard deviations of the stock voices along that axis (0 = no change).
       Axes are computed from the stock voices in AXIS_BASIS, so a recipe means the same thing everywhere.
"""
import json
from pathlib import Path

import numpy as np

HALVES = {'timbre': slice(0, 128), 'delivery': slice(128, 256)}
RECIPE_VERSION = 1

# the stock voices the axes are computed from (fixed, so recipes are reproducible); downloaded if missing
AXIS_BASIS = (
    'af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole af_nova af_river af_sarah af_sky '
    'am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa '
    'bf_alice bf_emma bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis '
    'ef_dora em_alex em_santa ff_siwis hf_alpha hf_beta hm_omega hm_psi if_sara im_nicola '
    'jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo').split()
N_PCS = 8
# named axes: (label at -, label at +, voices at the - end, voices at the + end)
NAMED_AXES = {
    'gender': ('female', 'male', lambda n: n[1] == 'f', lambda n: n[1] == 'm'),
    'accent': ('American', 'British', lambda n: n[0] == 'a', lambda n: n[0] == 'b'),
}
# What each axis does, per half: id -> (label, - end, + end, hint). Found by ear, then checked against
# measurements of -2/+2 sweeps (2026-11). The ids are what recipes store, so they never change; only the names do.
# The PC directions depend on AXIS_BASIS, N_PCS and the sign convention in Lab.axes(): change any of those and
# these names no longer apply.
AXIS_NAMES = {
    'timbre': {     # the decoder: voice colour only (pitch and pacing come from the delivery half)
        'pc1': ('brightness', 'muffled', 'bright', 'top end ~9 dB louder at +; the British voices sit at +'),
        'pc2': ('colour', 'Asian', 'American',
                'vowel colour of the Japanese/Hindi voices (-) vs onyx, fable, echo (+); not rhythm'),
        'pc3': ('size', 'feminine', 'masculine',
                'vocal-tract size: - smaller and airier, + larger and darker; the pitch hardly moves'),
        'pc4': ('clarity', 'clear', 'covered',
                'darker, rounder vowels at + (as if through rounded lips); the Japanese female voices sit at +'),
        'pc5': ('grit', 'clean', 'gravelly', 'hoarse and rough at +: the santa voices, bm_lewis, am_michael'),
        'pc6': ('region', 'American', 'European',
                'from af_kore, af_aoede, am_fenrir, am_puck (-) to the Spanish and Italian voices (+)'),
        'pc7': ('softness', 'harsh', 'soft', 'breathy and gentle at +; af_nicole sits at the + end'),
        'pc8': ('unclear', '−', '+', 'no clear effect found by ear or by measurement; slightly less hiss at +'),
    },
    'delivery': {   # the prosody predictor: pitch, melody, pacing
        'gender': ('pitch', 'high', 'low',
                   'pitch only, ~4.7 semitones per step; creaky at the low end. For a gender change, '
                   'pair it with timbre size'),
        'accent': ('endings', 'falling', 'flat',
                   '- starts phrases high and falls far; + flatter, endings may even rise (the British voices)'),
        'pc1': ('pitch 2', 'high', 'low', 'like pitch but colours the voice differently; also ~12% faster at +'),
        'pc2': ('pitch+pace', 'low, slow', 'high, quick', '~5 semitones higher and ~20% faster at +, shorter pauses'),
        'pc3': ('energy', 'subdued', 'energetic', 'livelier at +: a little higher and quicker'),
        'pc4': ('liveliness', 'lively', 'flat, brisk',
                'like endings (high starts and big falls at -) but also ~7% faster at +'),
        'pc5': ('starts (subtle)', 'low starts', 'held up',
                'subtle: at + the start of each phrase stays high for longer before it falls'),
        'pc6': ('expression', 'monotone', 'expressive',
                'how far the pitch swings: flat at -, higher peaks and deeper falls at +'),
        'pc7': ('tempo', 'quicker', 'slower', 'words and pauses ~10-20% longer at +'),
        'pc8': ('tempo (subtle)', 'quicker', 'slower', 'subtle: a few % slower at +, slightly deeper falls'),
    },
}


def empty_recipe(source=None):
    return {'voicegen_recipe': RECIPE_VERSION,
            **{h: {'mix': {source: 1.0} if source else {}, 'axes': {}} for h in HALVES}}


class Lab:
    def __init__(self, engine, voice_dirs=()):
        """engine: a voicegen Engine (loads packs); voice_dirs: folders of your own .pt voices"""
        self.eng = engine
        self.torch = engine.torch
        self.user = {}                              # display name -> path
        for d in voice_dirs:
            for p in sorted(Path(d).expanduser().glob('*.pt')):
                name = p.stem if p.stem not in self.user else f'{Path(d).name}/{p.stem}'
                self.user[name] = str(p.resolve())
        self.stock = list(AXIS_BASIS)
        self._axes = None
        self._mean = {}

    # ------------------------------------------------------------------ packs
    def pack(self, source, base=None):
        """[510, 1, 256] float32 tensor for a stock name, a user voice name, or a .pt path"""
        if source in self.user:
            source = self.user[source]
        elif base and source.endswith('.pt') and not Path(source).is_absolute():
            source = str(Path(base) / source)
        return self.eng.voice(source).float()

    def sources(self):
        return {'stock': self.stock, 'user': self.user}

    # ------------------------------------------------------------------ axes
    def axes(self):
        """{half: {axis_id: {'lo', 'hi', 'label', 'hint', 'vec': [510, 1, 128] tensor (one std)}}}"""
        if self._axes is None:
            X = np.stack([self.pack(n).numpy() for n in AXIS_BASIS])        # n, 510, 1, 256
            out = {}
            for half, sl in HALVES.items():
                H = X[..., sl].reshape(len(AXIS_BASIS), -1).astype(np.float64)
                mu = H.mean(0)
                ax = {}
                for aid, (lo, hi, neg, pos) in NAMED_AXES.items():
                    a = np.array([pos(n) for n in AXIS_BASIS]); b = np.array([neg(n) for n in AXIS_BASIS])
                    d = H[a].mean(0) - H[b].mean(0); d /= np.linalg.norm(d)
                    # one unit = one standard deviation of the stock voices along this direction
                    ax[aid] = dict(lo=lo, hi=hi, label=aid, hint=f'stock {lo} vs {hi} voices',
                                   vec=d * ((H - mu) @ d).std())
                _, s, Vt = np.linalg.svd(H - mu, full_matrices=False)
                for k in range(N_PCS):
                    v = Vt[k] * (1 if Vt[k].sum() >= 0 else -1)     # fixed sign convention
                    share = s[k] ** 2 / (s ** 2).sum()
                    ax[f'pc{k + 1}'] = dict(lo='−', hi='+', label=f'PC{k + 1}', hint=f'{share:.0%} of the stock variation',
                                            vec=v * s[k] / np.sqrt(len(AXIS_BASIS) - 1))
                for aid, a in ax.items():
                    if aid in AXIS_NAMES.get(half, {}):
                        a['label'], a['lo'], a['hi'], hint = AXIS_NAMES[half][aid]
                        a['hint'] = f'{aid}: {hint}' + (f' ({a["hint"]})' if aid.startswith('pc') else '')
                for a in ax.values():
                    a['unit'] = a['vec'] / (a['vec'] @ a['vec'])          # projection -> amount
                    a['vec'] = self.torch.tensor(a['vec'].reshape(510, 1, 128), dtype=self.torch.float32)
                out[half] = ax
                self._mean[half] = mu
            self._axes = out
        return self._axes

    def axes_info(self):
        return {h: [{'id': k, 'lo': a['lo'], 'hi': a['hi'], 'label': a['label'], 'hint': a['hint']}
                    for k, a in ax.items()]
                for h, ax in self.axes().items()}

    def position(self, pack):
        """where a pack sits on every axis, in stock-voice standard deviations from the stock average"""
        ax = self.axes()
        v = pack.numpy().reshape(510, 256).astype(np.float64)
        return {h: {k: round(float((v[:, sl].reshape(-1) - self._mean[h]) @ a['unit']), 2) for k, a in ax[h].items()}
                for h, sl in HALVES.items()}

    # ------------------------------------------------------------------ recipes
    def build(self, recipe, base=None):
        """recipe -> [510, 1, 256] tensor. base: folder that relative .pt paths in the recipe start from"""
        out = self.torch.zeros(510, 1, 256)
        for half, sl in HALVES.items():
            part = recipe.get(half) or {}
            mix = {k: float(w) for k, w in (part.get('mix') or {}).items() if float(w) != 0}
            tot = sum(mix.values())
            if not mix or abs(tot) < 1e-6:
                raise ValueError(f'{half}: the mix weights must not add up to 0')
            v = sum(w / tot * self.pack(src, base)[..., sl] for src, w in mix.items())
            for aid, amt in (part.get('axes') or {}).items():
                if float(amt):
                    if aid not in self.axes()[half]:
                        raise ValueError(f'{half}: unknown axis "{aid}"')
                    v = v + float(amt) * self.axes()[half][aid]['vec']
            out[..., sl] = v
        return out

    def decompose(self, source, tol=1e-4):
        """the stock mix a voice was blended from, per half, if it is one (else the voice itself)"""
        from scipy.optimize import nnls
        T = self.pack(source).numpy().reshape(510, 256).astype(np.float64)
        S = np.stack([self.pack(n).numpy().reshape(510, 256) for n in AXIS_BASIS]).astype(np.float64)
        r = empty_recipe(source); fits = {}
        for half, sl in HALVES.items():
            t = T[:, sl].reshape(-1); X = S[:, :, sl].reshape(len(AXIS_BASIS), -1)
            if any(np.allclose(x, t, atol=1e-6) for x in X):
                fits[half] = 0.0
                r[half]['mix'] = {AXIS_BASIS[int(np.argmin([np.abs(x - t).max() for x in X]))]: 1.0}
                continue
            M = np.vstack([X.T, 1e3 * np.ones(len(X))])                 # weights sum to 1
            w, _ = nnls(M, np.concatenate([t, [1e3]]))
            w = np.round(w, 2)
            w[w < 0.005] = 0
            if w.sum() <= 0:
                continue
            w /= w.sum()
            resid = np.linalg.norm(X.T @ w - t) / np.linalg.norm(t - X.mean(0))
            fits[half] = float(resid)
            if resid < tol:
                r[half]['mix'] = {AXIS_BASIS[i]: round(float(x), 4) for i, x in
                                  sorted(enumerate(w), key=lambda e: -e[1]) if x > 0}
        return r, fits

    def variations(self, recipe, n=6, step=0.5, halves=('timbre', 'delivery'), seed=None):
        """n copies of recipe, each moved a random distance along the principal axes"""
        rng = np.random.default_rng(seed)
        out = []
        for _ in range(n):
            r = json.loads(json.dumps(recipe))
            for h in halves:
                ax = r[h].setdefault('axes', {})
                for k in range(1, N_PCS + 1):
                    a = f'pc{k}'
                    ax[a] = round(float(ax.get(a, 0)) + float(rng.normal(0, step)), 2)
            out.append(r)
        return out

    # ------------------------------------------------------------------ files
    def portable(self, recipe, base):
        """your voices by name -> .pt paths (relative inside base, the recipe's folder; else absolute)"""
        def where(path):
            p, b = Path(path).resolve(), Path(base).resolve()
            return p.relative_to(b).as_posix() if p.is_relative_to(b) else p.as_posix()
        r = clean(recipe)
        for h in HALVES:
            r[h]['mix'] = {(where(self.user[k]) if k in self.user else k): w for k, w in r[h]['mix'].items()}
        return r

    def local(self, recipe, base):
        """.pt paths in a recipe file -> names of your voices where they match one, else absolute paths"""
        by_path = {str(Path(p).resolve()): n for n, p in self.user.items()}
        r = json.loads(json.dumps(recipe))
        for h in HALVES:
            mix = {}
            for k, w in (r.get(h, {}).get('mix') or {}).items():
                if k.endswith('.pt'):
                    p = str((Path(base) / k).resolve())
                    k = by_path.get(p, p)
                mix[k] = w
            r.setdefault(h, {})['mix'] = mix
            r[h].setdefault('axes', {})
        return r

    def save(self, recipe, pt_path):
        """write the pack and its recipe (<name>.recipe.json) side by side"""
        pt_path = Path(pt_path)
        pack = self.build(recipe, base=pt_path.parent)
        pt_path.parent.mkdir(parents=True, exist_ok=True)
        self.torch.save(pack, str(pt_path))
        recipe_path(pt_path).write_text(json.dumps(self.portable(recipe, pt_path.parent), indent=2) + '\n',
                                        encoding='utf-8')
        self.eng._packs.pop(str(pt_path), None)
        self.eng._packs.pop(str(pt_path.resolve()), None)
        self.user.setdefault(pt_path.stem, str(pt_path.resolve()))
        return pt_path


def recipe_path(pt_path):
    p = Path(pt_path)
    return p.with_name(p.stem + '.recipe.json')


def clean(recipe):
    """drop zero weights and zero axis amounts"""
    out = {'voicegen_recipe': RECIPE_VERSION}
    for h in HALVES:
        part = recipe.get(h) or {}
        out[h] = {'mix': {k: float(w) for k, w in (part.get('mix') or {}).items() if float(w) != 0},
                  'axes': {k: float(v) for k, v in (part.get('axes') or {}).items() if float(v) != 0}}
    return out


def load_recipe(path):
    r = json.loads(Path(path).read_text(encoding='utf-8'))
    if r.get('voicegen_recipe') != RECIPE_VERSION:
        raise ValueError(f'{path}: not a voicegen recipe (version {RECIPE_VERSION})')
    return r
