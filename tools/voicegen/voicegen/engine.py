"""The voice engine: Kokoro with in-model duration and pitch editing.

KModel.forward_with_tokens (kokoro 0.9.4, kokoro/model.py) is re-implemented in _synth_chunk() so the
per-phoneme durations and the F0 (pitch) curve can be edited after the predictor and before the iSTFTNet
decoder. The decoder then voices the edited curves natively, so nothing is stretched after the fact.
Kokoro has no public API for this, hence the version pin.

Voice packs are [510, 1, 256]: [..., :128] = timbre (decoder), [..., 128:] = delivery/prosody (predictor).

Findings behind the design (2026-11-11 experiments, see tools/voice_prototype.py in the bass repo):
- Kokoro is deterministic in timing and pitch (same line -> identical durations and F0). Hence `human`.
- The predicted energy curve (N) is nearly ignored by the decoder: loudness = plain gain.
- F0 = 0 or a sub-20 Hz F0 inside the model = gravel / audible tone.
- Pitch maths must be in semitones on voiced frames only; smoothing F0 itself (not the offset) or widening
  the range lets fade-out frames sag to 20-60 Hz = growl. `floor` ends voicing cleanly instead.
- Loudness comparisons must be ear-weighted (A-weighting): whisper energy sits where hearing is most sensitive.
"""
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import scipy.ndimage as nd

from . import dsp, marks as marks_mod, styles
from .common import STOCK_PREFIXES, make_seed  # noqa: F401  (re-exported)

KOKORO_VERSION = '0.9.4'
REPO_ID = 'hexgrad/Kokoro-82M'
MAX_PHONEMES = 510
VOICED_HZ = 70.0                    # F0 below this = onset/fade-out frames; never reshape them
PUNCT = set(',.;:!?—…')
SENTENCE_END = set('.!?…')
# vowels (misaki's capitals are diphthongs), voiced sonorants, the flap, and stress/length marks
VOWELS = set('aeiouæɑɐɒɔəɚɛɜɪʊʌᵊᵻAIOWY')
SONORANT = VOWELS | set('lɹwjmnŋɫTˈˌː')


@dataclass
class Token:
    text: str
    phonemes: str       # already filtered to the model's vocabulary
    whitespace: bool
    span: tuple = None  # character span in the plain text, if it could be aligned
    unknown: bool = False

    @property
    def is_word(self):
        return any(c.isalpha() for c in self.phonemes)


@dataclass
class Rendered:
    audio: np.ndarray
    unknown_words: list = field(default_factory=list)
    unplaced_marks: list = field(default_factory=list)   # marks that didn't land on any word
    phonemes: str = ''


class Engine:
    def __init__(self, model_dir=None, lang='a'):
        """model_dir: a folder with config.json and the .pth (offline use); default downloads from HF."""
        import kokoro
        if kokoro.__version__ != KOKORO_VERSION:
            raise RuntimeError(f'voicegen needs kokoro=={KOKORO_VERSION} (it re-implements part of the model); '
                               f'found {kokoro.__version__}')
        import torch
        from kokoro import KModel, KPipeline
        warnings.filterwarnings('ignore', module='torch')
        self.torch = torch
        model = None
        if model_dir:
            d = Path(model_dir)
            pth = next(d.glob('*.pth'))
            model = KModel(repo_id=REPO_ID, config=str(d / 'config.json'), model=str(pth)).eval()
        self.pipe = KPipeline(lang_code=lang, repo_id=REPO_ID, model=model if model is not None else True)
        self.m = self.pipe.model
        self.vocab = self.m.vocab
        self._packs = {}

    # ------------------------------------------------------------------ voices
    def voice(self, spec):
        """a .pt path, or a Kokoro stock voice name such as 'af_nicole'; tensors pass through"""
        if self.torch.is_tensor(spec):
            return spec
        key = str(spec)
        if key not in self._packs:
            if Path(key).suffix == '.pt' or Path(key).exists():
                self._packs[key] = self.torch.load(key, map_location='cpu', weights_only=True)
            elif key.startswith(STOCK_PREFIXES):
                self._packs[key] = self.pipe.load_voice(key)
            else:
                raise FileNotFoundError(f'voice "{key}" is neither a .pt file nor a Kokoro stock voice')
        return self._packs[key]

    def blend_delivery(self, timbre_voice, delivery_voice):
        """timbre of one voice, delivery (prosody) of another"""
        return self.torch.cat([self.voice(timbre_voice)[..., :128], self.voice(delivery_voice)[..., 128:]], dim=-1)

    # ------------------------------------------------------------------ text -> tokens
    def tokenize(self, marked):
        """MarkedText -> [Token], with character spans aligned to marked.plain"""
        _, mtoks = self.pipe.g2p(marked.g2p)
        out, pos = [], 0
        for t in mtoks:
            raw = t.phonemes or ''
            ps = ''.join(c for c in raw if c in self.vocab)
            tok = Token(t.text, ps, bool(t.whitespace), unknown='❓' in raw)
            if t.text:
                i = marked.align.find(t.text, pos)
                if i >= 0 and not marked.align[pos:i].strip(' \t\n-–—'):    # a dash the phonemizer re-spelt
                    tok.span = (i, i + len(t.text)); pos = i + len(t.text)
            out.append(tok)
        return out

    @staticmethod
    def chunks(tokens):
        """split a token list so each piece fits Kokoro's 510 phonemes, preferring sentence ends"""
        def length(ts):
            return len(Engine.phoneme_string(ts))
        if length(tokens) <= MAX_PHONEMES:
            return [tokens]
        out, cur = [], []
        for t in tokens:
            cur.append(t)
            if length(cur) > MAX_PHONEMES:
                cut = max((i + 1 for i, x in enumerate(cur[:-1]) if x.phonemes and x.phonemes[-1] in SENTENCE_END),
                          default=None)
                if cut is None:
                    cut = max((i + 1 for i, x in enumerate(cur[:-1]) if x.phonemes and x.phonemes[-1] in PUNCT),
                              default=len(cur) - 1)
                out.append(cur[:cut]); cur = cur[cut:]
        if cur:
            out.append(cur)
        return out

    @staticmethod
    def phoneme_string(tokens):
        return ''.join(t.phonemes + (' ' if t.whitespace else '') for t in tokens if t.phonemes).strip()

    @staticmethod
    def layout(tokens):
        """phoneme string + each token's (start, end) position in the id tensor (ids carry a leading pad)"""
        ps, spans = '', []
        for t in tokens:
            if not t.phonemes:
                spans.append(None); continue
            if ps and not ps.endswith(' ') and prev_ws:
                ps += ' '
            a = len(ps) + 1
            ps += t.phonemes
            spans.append((a, len(ps) + 1))
            prev_ws = t.whitespace
        return ps, spans

    # ------------------------------------------------------------------ the model, with edits
    def _synth_chunk(self, ps, word_spans, edits, pack, speed=1.0, pitch=0.0, range_=1.0, drift=0.0,
                     pause=1.0, floor=0.0, human=0.0, rng=None):
        """
        ps         : phoneme string (<= 510)
        word_spans : id spans of the words (for `human` and per-word edits)
        edits      : {word_span: {'pitch': st, 'dur': x}}
        speed      : divides all durations       pitch : semitones, whole line
        range_     : pitch movement around the median (0 = dead flat, >1 = livelier)
        drift      : semitones of slow fall across the line
        pause      : multiplier on punctuation durations
        floor      : end voicing when F0 sags below floor x median (0 = off)
        human      : variation strength (1.0 = approved default), drawn from rng
        returns (audio, f0)
        """
        torch, m = self.torch, self.m
        ids = torch.LongTensor([[0] + [self.vocab[c] for c in ps] + [0]])
        ref_s = pack[len(ps) - 1]
        L = torch.full((1,), ids.shape[-1], dtype=torch.long)
        mask = torch.gt(torch.arange(L.max()).unsqueeze(0) + 1, L.unsqueeze(1))
        d_en = m.bert_encoder(m.bert(ids, attention_mask=(~mask).int())).transpose(-1, -2)
        s = ref_s[:, 128:]
        d = m.predictor.text_encoder(d_en, s, L, mask)
        x, _ = m.predictor.lstm(d)
        dur = torch.sigmoid(m.predictor.duration_proj(x)).sum(axis=-1).squeeze(0) / speed

        # ---- durations
        dur = torch.minimum(dur, self.length_caps(ps, speed))
        if human:
            dur *= float(np.exp(rng.normal(0, 0.03 * human)))                 # line tempo
            for a, b in word_spans:
                dur[a:b] *= float(np.exp(rng.normal(0, 0.07 * human)))       # each word
        natural = dur.clone()                   # before marks lengthen words: what pitch/energy are predicted on
        for (a, b), o in edits.items():
            if o.get('dur', 1.0) != 1.0:
                dur[a:b] = self.stretch_word(ps, a, b, dur[a:b], o['dur'], speed)
        # pauses: Kokoro holds mid-line punctuation for only 30-50 ms. The mark is lengthened to the pause, and
        # voicing and energy are switched off over it below, so the decoder itself renders the fade into the
        # silence and the next word's start. (Cutting silence into the audio afterwards chopped tails and left
        # fragments of the next word: "s-sorry", a hiccup on "yes… yes…".)
        for i0, j, kind in self.pause_groups(ps):
            target = styles.PAUSES[kind] * pause
            if human:
                target *= float(np.exp(rng.normal(0, styles.PAUSE_HUMAN * human)))
            have = float(dur[i0 + 1:j + 2].sum()) / 40.0                     # 40 duration frames per second
            last = i0 + len(ps[i0:j + 1].rstrip()) - 1                       # the run's last mark
            if target > have:
                extra = (target - have) * 40.0
                dur[last + 1] += extra; natural[last + 1] += extra
        pd = self.frames(dur)
        self.last_timing = (ps, pd.numpy())                                 # for diagnostics
        idx, aln = self.alignment(pd)
        ids_at = idx.repeat_interleave(2)                                   # the id under each F0 sample
        pd_n = self.frames(natural)
        if torch.equal(pd_n, pd):
            F0, N = m.predictor.F0Ntrain(d.transpose(-1, -2) @ aln, s)
        else:
            # Drawn-out/stressed words: pitch and energy are predicted at the natural length, then stretched.
            # Asked for a long vowel, the model fades it out: "deeper" lost its "-per", "Heavy" distorted.
            F0, N = m.predictor.F0Ntrain(d.transpose(-1, -2) @ self.alignment(pd_n)[1], s)
            F0, N = self.retime(F0, pd_n, pd), self.retime(N, pd_n, pd)

        # ---- pitch, as a semitone offset curve applied to voiced frames
        f, gaps = self.bridge_dips(F0[0], ps, ids_at.numpy())
        for a_, b_ in gaps:                     # the energy curve drops out with the pitch: fill it the same way
            N[0, a_ + 1:b_] = torch.linspace(float(N[0, a_]), float(N[0, b_]), b_ - a_ + 1)[1:-1]
        self.sustain(f, N, ps, ids_at, edits, starts=None, pd=pd)
        v = f > VOICED_HZ
        st = torch.zeros_like(f)
        if v.any():
            cur = 12 * torch.log2(f[v] / f[v].median())
            st[v] = cur * range_ - cur
        st += pitch
        if drift:
            st -= torch.linspace(0, drift, f.shape[0])
        ends = torch.cumsum(pd, 0) * 2; starts = ends - pd * 2              # F0 runs at 2x the frame rate
        if human:
            pk = 0.35 * human * min(1.0, range_ + 0.25)                       # flat styles stay flat
            st += float(rng.normal(0, 0.4 * pk / 0.35))
            for a, b in word_spans:
                st[int(starts[a]):int(ends[b - 1])] += float(rng.normal(0, pk))
            wander = torch.tensor(nd.gaussian_filter1d(rng.normal(0, 1, f.shape[0]), 25), dtype=torch.float32)
            st += wander / (wander.std() + 1e-9) * 0.3 * pk / 0.35
        self.stress(f, v, st, edits, word_spans, starts, ends)
        if edits or human:   # smooth the OFFSET (edge-replicated), never F0 itself
            k = torch.ones(1, 1, 9) / 9
            st = torch.nn.functional.conv1d(
                torch.nn.functional.pad(st.view(1, 1, -1), (4, 4), mode='replicate'), k).view(-1)
        # question rise: after the smoothing, which would otherwise blur away part of the flattening
        Q = styles.QUESTION
        for (a, b), o in edits.items():
            if not o.get('rise'):
                continue
            lo, hi = int(starts[a]), int(ends[b - 1])                       # within the word: the "?" is silent
            vi = torch.nonzero(v[lo:hi]).view(-1) + lo
            if len(vi) < 3:
                continue
            now = 12 * torch.log2(f[vi] / f[v].median()) + st[vi]       # the word's contour so far
            t = torch.linspace(0, 1, len(vi))
            corr = -(now - now.mean()) * Q['flatten'] + o['rise'] * (Q['gradual'] * t + (1 - Q['gradual']) * t ** Q['power'])
            st[vi] += corr * torch.clamp(torch.arange(len(vi)) / 6.0, max=1.0)   # eased in, no jump at the start
        f = f * 2 ** (st / 12)
        if floor and v.any():
            lim = max(VOICED_HZ, float(f[f > VOICED_HZ].median()) * floor)
            f = torch.where(f < lim, torch.zeros_like(f), f)
        # no voicing or breath on punctuation (and the spaces after it) or on the pads around the line: the model
        # hums through them ("yes…oo", a buzz before the first word), worst in flat styles, which lift that hum.
        # Energy is eased in and out of the quiet (no clicks); pitch is simply off.
        quiet = torch.zeros(len(ps) + 2, dtype=torch.bool)
        quiet[0] = quiet[-1] = True
        for a, b in self.punct_runs(ps):
            quiet[a + 1:b + 2] = True
        qf = quiet[ids_at]
        f = torch.where(qf, torch.zeros_like(f), f)
        w = torch.tensor(nd.gaussian_filter1d(qf.numpy().astype(float), styles.QUIET_EASE), dtype=N.dtype)
        w = torch.maximum(w, qf.to(N.dtype) * 0.5)
        N[0] = N[0] * (1 - w) + torch.minimum(N[0], torch.full_like(N[0], styles.QUIET_ENERGY)) * w

        t_en = m.text_encoder(ids, L, mask)
        audio = m.decoder(t_en @ aln, f.view(1, -1), N, ref_s[:, :128]).squeeze().numpy()
        f = f.numpy()
        spf = len(audio) // len(f)                                          # audio samples per F0 sample (300)

        # ---- word gain (stress)
        gains = [(int(starts[a]) * spf, int(ends[b - 1]) * spf, o['gain']) for (a, b), o in edits.items()
                 if o.get('gain')]
        if gains:
            g = np.zeros(len(audio))
            for lo, hi, db in gains:
                g[lo:hi] = db
            audio = audio * 10 ** (nd.gaussian_filter1d(g, dsp.SR * 0.015) / 20)

        return audio, f

    @staticmethod
    def sustain(f, N, ps, ids_at, edits, starts=None, pd=None):
        """Drawn-out words are held: the model trails a long or phrase-final word off (the "ee" of the second
        "deeper" lost its pitch and energy, and the "-per" went with it). Over the word's vowels and sonorants,
        missing pitch is filled from the word's own voiced pitch (held at the ends) and energy is kept up to
        the line's speaking level (styles.SUSTAIN_ENERGY below its median). In place."""
        import torch
        v = f > VOICED_HZ
        if not v.any():
            return
        ref = float(N[0][v].median()) - styles.SUSTAIN_ENERGY
        ends = np.cumsum(pd.numpy()) * 2; begins = ends - pd.numpy() * 2
        for (a, b), o in edits.items():
            if not o.get('sustain'):
                continue
            lo, hi = int(begins[a]), int(ends[b - 1])
            k = np.arange(lo, hi)
            son = np.array([0 < i <= len(ps) and ps[i - 1] in SONORANT for i in ids_at[lo:hi].numpy()])
            if not son.any():
                continue
            fv = f[lo:hi].numpy(); vv = fv > VOICED_HZ
            if vv.any():
                fill = np.exp(np.interp(k, k[vv], np.log(fv[vv])))            # held flat beyond the ends
            else:
                fill = np.full(len(k), float(f[v].median()))
            fv = np.where(son & ~vv, fill, fv)
            on = fv > VOICED_HZ                                             # smooth jumps (a crack in "Heavy")
            if on.sum() > 3:
                lf = np.log(np.where(on, fv, 1.0))
                sm = nd.gaussian_filter1d(np.interp(k, k[on], lf[on]), styles.SUSTAIN_SMOOTH)
                fv = np.where(on, np.exp(sm), fv)
            f[lo:hi] = torch.tensor(fv, dtype=f.dtype)
            n = N[0, lo:hi]
            N[0, lo:hi] = torch.where(torch.tensor(son), torch.clamp(n, min=ref), n)

    @staticmethod
    def frames(dur):
        """durations -> whole frames, rounding the running total (so a x1.15 stress isn't rounded away)"""
        import torch
        return torch.diff(torch.round(torch.cumsum(dur, 0)), prepend=torch.zeros(1)).clamp(min=1).long()

    @staticmethod
    def alignment(pd):
        import torch
        idx = torch.repeat_interleave(torch.arange(len(pd)), pd)
        aln = torch.zeros((len(pd), idx.shape[0])); aln[idx, torch.arange(idx.shape[0])] = 1
        return idx, aln.unsqueeze(0)

    @staticmethod
    def retime(curve, pd_from, pd_to):
        """a [1, T] curve at 2 samples per frame, timed by pd_from -> timed by pd_to, each sound stretched evenly"""
        import torch
        a = np.concatenate([[0], np.cumsum(pd_from.numpy()) * 2]); b = np.concatenate([[0], np.cumsum(pd_to.numpy()) * 2])
        t = np.arange(b[-1]) + 0.5
        k = np.searchsorted(b, t, side='right') - 1                         # which sound each new sample is in
        src = a[k] + (t - b[k]) * (a[k + 1] - a[k]) / (b[k + 1] - b[k]) - 0.5
        c = curve[0].numpy()
        return torch.tensor(np.interp(src, np.arange(len(c)), c), dtype=curve.dtype).unsqueeze(0)

    @staticmethod
    def stretch_word(ps, a, b, seg, factor, speed):
        """lengthen (or shorten) one word's sounds by `factor`, but no sound past styles.STRETCH_CAP (even one the
        model already held longer); what doesn't fit goes to the word's vowels. A sound the model already holds long
        (an "r" before a comma) stretched again distorts ("deeper"). The line's last sound is exempt."""
        import torch
        new = seg * factor
        if factor <= 1.0:
            return new
        cap = styles.STRETCH_CAP * 40.0 / speed
        last = max((i for i, c in enumerate(ps) if c not in PUNCT and c != ' '), default=-1) + 1
        lim = torch.full_like(seg, cap)
        free = torch.tensor([i + a == last for i in range(len(seg))])
        lim = torch.where(free, new, lim)
        excess = float((new - torch.minimum(new, lim)).sum())
        new = torch.minimum(new, lim)
        vowel = torch.tensor([ps[i + a - 1] in VOWELS if 0 < i + a <= len(ps) else False for i in range(len(seg))])
        room = torch.where(vowel, lim - new, torch.zeros_like(new)).clamp(min=0)
        if excess > 0 and float(room.sum()) > 0:
            new += room * min(1.0, excess / float(room.sum()))
        return new

    @staticmethod
    def stress(f, v, st, edits, word_spans, starts, ends):
        """Stressed words (edits with 'over'): the word's peak is lifted to `over` semitones above the peaks of
        the words around it (at least `pitch`, at most styles.STRESS_MAX), then eases down `fall` semitones
        across the word. Relative, because the model often peaks on a neighbour
        ("have you EVER wanted"), which swamps a fixed lift. Changes st in place."""
        import torch
        med = f[v].median() if v.any() else None
        def peak(a, b):
            lo, hi = int(starts[a]), int(ends[b - 1])
            m = v[lo:hi]
            if med is None or not m.any():
                return None
            return float(torch.quantile(12 * torch.log2(f[lo:hi][m] / med) + st[lo:hi][m], 0.9))
        words = sorted(word_spans)
        for (a, b), o in edits.items():
            lo, hi = int(starts[a]), int(ends[b - 1])
            if hi <= lo or not (o.get('over') or o.get('pitch')):
                continue
            if not o.get('over'):                                        # de-stress: a plain shift
                st[lo:hi] += o['pitch']
                continue
            i = next((k for k, w in enumerate(words) if w[0] >= a), len(words))
            near = [w for w in words[max(0, i - 2):i] + words[i + 1:i + 2] if w not in edits]
            mine = peak(a, b)
            others = [p for p in (peak(*w) for w in near) if p is not None]
            lift = o.get('pitch', 0.0)
            if mine is not None and others:
                lift = max(lift, max(others) + o['over'] - mine)
            lift = min(lift, styles.STRESS_MAX)
            t = torch.linspace(0, 1, hi - lo)
            st[lo:hi] += lift - o.get('fall', 0.0) * t

    @staticmethod
    def punct_runs(ps):
        """[(first, last)] char indices of each run of punctuation, with the spaces after it"""
        out, i, n = [], 0, len(ps)
        while i < n:
            if ps[i] not in PUNCT:
                i += 1; continue
            j = i
            while j + 1 < n and (ps[j + 1] in PUNCT or ps[j + 1] == ' '):
                j += 1
            out.append((i, j))
            i = j + 1
        return out

    @classmethod
    def pause_groups(cls, ps):
        """mid-line runs of punctuation in a phoneme string -> [(first, last, kind)]: char indices of the run's
        first mark and its last character (spaces included). Runs at the start or end of the string are left out."""
        out, n = [], len(ps)
        for i, j in cls.punct_runs(ps):
            run = ps[i:j + 1]
            if i > 0 and j + 1 < n and ps[:i].strip():
                if '…' in run or run.count('.') >= 2:
                    kind = 'ellipsis'
                elif any(c in run for c in '.!?'):
                    kind = 'sentence'
                elif '—' in run:
                    kind = 'dash'
                elif any(c in run for c in ';:'):
                    kind = 'clause'
                else:
                    kind = 'comma'
                out.append((i, j, kind))
        return out

    def length_caps(self, ps, speed):
        """per id (with the leading/trailing pad) the longest a sound may last, in model frames"""
        C, fps = styles.LENGTH_CAP, 40.0                                    # 40 duration frames per second
        caps = self.torch.full((len(ps) + 2,), float('inf'))
        last = max((i for i, c in enumerate(ps) if c not in PUNCT and c != ' '), default=-1)
        for i, c in enumerate(ps):
            if c in PUNCT or i >= last:
                continue
            if c == ' ':
                caps[i + 1] = C['gap'] * fps / speed
                continue
            nxt = ps[i + 1:].lstrip(' ')
            final = nxt[:1] in PUNCT and nxt[:1] != ''
            caps[i + 1] = (C['phrase_end'] if final else C['inner']) * fps / speed
        return caps

    @staticmethod
    def bridge_dips(f, ps, ids_at):
        """Fill in short gaps and octave-ish drops in the predicted F0 where the sounds should be voiced.
        The model sometimes drops voicing for a syllable ("thorough" -> "thor- -ough"). A gap is filled
        (log-interpolated) only when it is short, voiced on both sides, and every sound under it is a vowel
        or a voiced sonorant; gaps on s, t, k, ... are real. ids_at: the id index of each F0 sample.
        Returns (F0, [(a, b)]): the voiced samples on either side of each filled gap."""
        B = styles.PITCH_BRIDGE
        x = f.numpy().copy()
        v = x > VOICED_HZ
        if v.sum() < 3:
            return f, []
        idx = np.nonzero(v)[0]
        ref = np.array([np.median(x[idx[(idx >= k - B['win']) & (idx <= k + B['win'])]])
                        if ((idx >= k - B['win']) & (idx <= k + B['win'])).any() else 0.0 for k in range(len(x))])
        dip = (x < B['ratio'] * ref) | ~v
        def ch(i):
            return ps[i - 1] if 0 < i <= len(ps) else None
        k, n, gaps = 0, len(x), []
        while k < n:
            if not dip[k]:
                k += 1; continue
            e = k
            while e + 1 < n and dip[e + 1]:
                e += 1
            a, b = k - 1, e + 1
            under = {ch(i) for i in ids_at[k:e + 1]} - {' '}                # word gaps don't count...
            edges = {ch(ids_at[a]), ch(ids_at[b])} if a >= 0 and b < n else {None}   # ...but the edges must be voiced
            if (e - k + 1 <= B['max_len'] and a >= 0 and b < n and v[a] and v[b] and not dip[a] and not dip[b]
                    and under and under <= SONORANT and edges <= SONORANT | {' '}):
                t = (np.arange(k, e + 1) - a) / (b - a)
                x[k:e + 1] = np.exp(np.log(x[a]) * (1 - t) + np.log(x[b]) * t)
                gaps.append((a, b))
            k = e + 1
        return (f.new_tensor(x) if gaps else f), gaps

    def _speak(self, tokens, word_edits, pack, rng, **kw):
        """tokens -> (audio, per-sample voiced 0..1), chunked as needed"""
        audios, voiced = [], []
        with self.torch.no_grad():
            first = next((i for i, t in enumerate(tokens) if t.is_word), len(tokens))
            tokens = tokens[first:]                     # a leading "..." is voiced as a little hit; drop it
            for chunk in self.chunks(tokens):
                ps, spans = self.layout(chunk)
                if not ps:
                    continue
                if audios:                              # chunks break at sentence ends: pause between them
                    gap = np.zeros(int(styles.PAUSES['sentence'] * kw.get('pause', 1.0) * dsp.SR), np.float32)
                    audios.append(gap); voiced.append(np.zeros(len(gap)))
                words = [sp for t, sp in zip(chunk, spans) if sp and t.is_word]
                edits = {sp: word_edits[id(t)] for t, sp in zip(chunk, spans) if sp and id(t) in word_edits}
                a, f = self._synth_chunk(ps, words, edits, pack, rng=rng, **kw)
                audios.append(a)
                voiced.append(dsp.frames_to_samples(f > VOICED_HZ, len(a)))
        if not audios:
            return np.zeros(0, np.float32), np.zeros(0)
        return np.concatenate(audios), np.concatenate(voiced)

    # ------------------------------------------------------------------ public
    def render(self, text, voice, direction='', seed=0, finish=True):
        """
        text      : the line, with optional word marks (see marks.py)
        voice     : .pt path or stock name
        direction : 'trance 0.6, internal, pause 2' or a styles.Direction
        seed      : variation seed (use make_seed(speaker, text, occurrence, take))
        finish    : trim, apply effects and level (False = the raw model output, for checks)
        """
        d = styles.parse_direction(direction) if isinstance(direction, str) else direction
        s = styles.resolve(d)
        mt = marks_mod.parse(text)
        tokens = self.tokenize(mt)

        word_edits, unplaced = {}, []
        for mk in mt.marks:
            hit = [t for t in tokens if t.span and t.is_word and t.span[0] < mk.end and t.span[1] > mk.start]
            if not hit:
                unplaced.append(mt.plain[mk.start:mk.end]); continue
            e = dict(styles.STRESS.get(mk.stress, {}))
            e['dur'] = e.get('dur', 1.0) * styles.STRETCH.get(mk.stretch, 1.0)
            if mk.stretch:
                e['sustain'] = True
            for t in hit:
                word_edits[id(t)] = e

        rises = styles.question_rises(s, mt.plain)
        if any(rises):
            q = 0                                       # which question we're at
            for i, t in enumerate(tokens):
                if '?' in t.text and not t.is_word:
                    q += 1; continue
                nxt = next((u for u in tokens[i + 1:] if u.phonemes), None)
                if (t.is_word and nxt is not None and not nxt.is_word and '?' in nxt.text and q < len(rises)
                        and rises[q]):
                    word_edits[id(t)] = dict(word_edits.get(id(t), {}), rise=rises[q])

        pack = self.voice(voice)
        line_seed = make_seed(seed, s['take'])
        rng = np.random.default_rng(line_seed)
        self.torch.manual_seed(line_seed % 2**63)          # the decoder adds noise; make it repeatable
        kw = {k: s[k] for k in ('speed', 'pitch', 'range_', 'drift', 'pause', 'floor', 'human')}
        audio, voiced = self._speak(tokens, word_edits, pack, rng, **kw)
        if s['style'] == 'whisper' and len(audio):
            audio = dsp.whisper(audio, voiced, seed=make_seed('breath', seed, s['take']) % 2**32)
        elif s.get('breath') and len(audio):
            audio = dsp.breathy(audio, voiced, min(1.0, max(0.0, s['breath'])),
                                seed=make_seed('breath', seed, s['take']) % 2**32)

        if finish and len(audio):
            audio = dsp.trim(audio)
            for fx in s['effects']:
                if fx == 'internal':
                    audio = dsp.internal(audio)
            audio = dsp.level(audio, s['level_db'])
        return Rendered(audio.astype(np.float32),
                        unknown_words=[t.text for t in tokens if t.unknown],
                        unplaced_marks=unplaced,
                        phonemes=self.phoneme_string(tokens))


FORMATS = {'ogg': ('OGG', 'OPUS'), 'wav': ('WAV', 'PCM_16'), 'flac': ('FLAC', 'PCM_16')}


def opus_available():
    import soundfile as sf
    try:
        return 'OPUS' in sf.available_subtypes('OGG')
    except Exception:
        return False


def resolve_format(fmt='ogg', warn=print):
    """'ogg' (Opus, small), 'wav' (uncompressed) or 'flac' (lossless). 'ogg' falls back to 'wav' when this
    machine's libsndfile can't write Opus."""
    fmt = fmt.lower().lstrip('.')
    if fmt not in FORMATS:
        raise ValueError(f'unknown audio format "{fmt}": use ogg, wav or flac')
    if fmt == 'ogg' and not opus_available():
        if warn:
            warn('voicegen: this soundfile/libsndfile cannot write Ogg Opus; writing .wav instead')
        return 'wav'
    return fmt


def write(path, audio, fmt=None, warn=print):
    """Write a clip; returns the path actually written (its extension may change).
    fmt: 'ogg' | 'wav' | 'flac'; default = the path's extension. If Ogg Opus fails, the clip is written as
    .wav next to it instead."""
    import soundfile as sf
    path = Path(path)
    fmt = resolve_format(fmt or path.suffix or 'ogg', warn)
    path = path.with_suffix('.' + fmt)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sf.write(str(path), audio, dsp.SR, format=FORMATS[fmt][0], subtype=FORMATS[fmt][1])
    except Exception as e:
        if fmt != 'ogg':
            raise
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        if warn:
            warn(f'voicegen: writing Ogg Opus failed ({e}); writing .wav instead')
        path = path.with_suffix('.wav')
        sf.write(str(path), audio, dsp.SR, format='WAV', subtype='PCM_16')
    return path
