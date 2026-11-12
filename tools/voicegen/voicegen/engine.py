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
                if i >= 0 and not marked.align[pos:i].strip():
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
        for i, c in enumerate(ps):
            if c in PUNCT:
                dur[i + 1] *= pause
        if human:
            dur *= float(np.exp(rng.normal(0, 0.03 * human)))                 # line tempo
            for a, b in word_spans:
                dur[a:b] *= float(np.exp(rng.normal(0, 0.07 * human)))       # each word
            for i, c in enumerate(ps):
                if c in PUNCT:
                    dur[i + 1] *= float(np.exp(rng.normal(0, 0.15 * human)))  # each pause
        for (a, b), o in edits.items():
            dur[a:b] *= o.get('dur', 1.0)
        pd = torch.round(dur).clamp(min=1).long()
        idx = torch.repeat_interleave(torch.arange(ids.shape[1]), pd)
        aln = torch.zeros((ids.shape[1], idx.shape[0])); aln[idx, torch.arange(idx.shape[0])] = 1
        aln = aln.unsqueeze(0)
        en = d.transpose(-1, -2) @ aln
        F0, N = m.predictor.F0Ntrain(en, s)

        # ---- pitch, as a semitone offset curve applied to voiced frames
        f = F0[0]
        v = f > VOICED_HZ
        st = torch.zeros_like(f)
        if v.any():
            cur = 12 * torch.log2(f[v] / f[v].median())
            st[v] = cur * range_ - cur
        st += pitch
        if drift:
            st -= torch.linspace(0, drift, f.shape[0])
        ends = torch.cumsum(pd, 0) * 2; starts = ends - pd * 2              # F0 runs at 2x the frame rate
        for (a, b), o in edits.items():
            st[int(starts[a]):int(ends[b - 1])] += o.get('pitch', 0)
        if human:
            pk = 0.35 * human * min(1.0, range_ + 0.25)                       # flat styles stay flat
            st += float(rng.normal(0, 0.4 * pk / 0.35))
            for a, b in word_spans:
                st[int(starts[a]):int(ends[b - 1])] += float(rng.normal(0, pk))
            wander = torch.tensor(nd.gaussian_filter1d(rng.normal(0, 1, f.shape[0]), 25), dtype=torch.float32)
            st += wander / (wander.std() + 1e-9) * 0.3 * pk / 0.35
        if edits or human:   # smooth the OFFSET (edge-replicated), never F0 itself
            k = torch.ones(1, 1, 9) / 9
            st = torch.nn.functional.conv1d(
                torch.nn.functional.pad(st.view(1, 1, -1), (4, 4), mode='replicate'), k).view(-1)
        # question rise: after the smoothing, which would otherwise blur away part of the flattening
        Q = styles.QUESTION
        for (a, b), o in edits.items():
            if not o.get('rise'):
                continue
            lo, hi = int(starts[a]), int(ends[b - 1])
            while hi < len(v) and v[hi]:                                   # on through the "?" to the end of voicing
                hi += 1
            idx = torch.nonzero(v[lo:hi]).view(-1) + lo
            if len(idx) < 3:
                continue
            now = 12 * torch.log2(f[idx] / f[v].median()) + st[idx]       # the word's contour so far
            t = torch.linspace(0, 1, len(idx))
            corr = -(now - now.mean()) * Q['flatten'] + o['rise'] * (Q['gradual'] * t + (1 - Q['gradual']) * t ** Q['power'])
            st[idx] += corr * torch.clamp(torch.arange(len(idx)) / 6.0, max=1.0)   # eased in, no jump at the start
        f = f * 2 ** (st / 12)
        if floor and v.any():
            lim = max(VOICED_HZ, float(f[f > VOICED_HZ].median()) * floor)
            f = torch.where(f < lim, torch.zeros_like(f), f)

        t_en = m.text_encoder(ids, L, mask)
        audio = m.decoder(t_en @ aln, f.view(1, -1), N, ref_s[:, :128]).squeeze().numpy()
        return audio, f.numpy()

    def _speak(self, tokens, word_edits, pack, rng, **kw):
        """tokens -> (audio, per-sample voiced 0..1), chunked as needed"""
        audios, voiced = [], []
        with self.torch.no_grad():
            for chunk in self.chunks(tokens):
                ps, spans = self.layout(chunk)
                if not ps:
                    continue
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
            for t in hit:
                word_edits[id(t)] = e

        rise = styles.question_rise(s, mt.plain)
        if rise:
            for i, t in enumerate(tokens):
                nxt = next((u for u in tokens[i + 1:] if u.phonemes), None)
                if t.is_word and nxt is not None and not nxt.is_word and '?' in nxt.text:
                    word_edits[id(t)] = dict(word_edits.get(id(t), {}), rise=rise)

        pack = self.voice(voice)
        if s['style'] == 'soft':
            pack = self.blend_delivery(voice, styles.SOFT_DELIVERY)
        line_seed = make_seed(seed, s['take'])
        rng = np.random.default_rng(line_seed)
        self.torch.manual_seed(line_seed % 2**63)          # the decoder adds noise; make it repeatable
        kw = {k: s[k] for k in ('speed', 'pitch', 'range_', 'drift', 'pause', 'floor', 'human')}
        audio, voiced = self._speak(tokens, word_edits, pack, rng, **kw)
        if s['style'] == 'whisper' and len(audio):
            audio = dsp.whisper(audio, voiced, seed=make_seed('breath', seed, s['take']) % 2**32)

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
