"""voice_prototype.py — the approved prosody engine from the 2026-11-11 tone-control experiments.

NOT the generator. This is the reference implementation that tools/voice_lines.py should be built from
(see tools/VOICE_PLAN.md). Running it rebuilds the approved reference clips so the real generator can be
A/B-checked against them:

    ~/venv/kokoro/bin/python -I tools/voice_prototype.py stories/voices/reference

How it works: KModel.forward_with_tokens (kokoro 0.9.4, kokoro/model.py) is re-implemented in synth() so the
per-phoneme durations and the F0 (pitch) curve can be edited after the predictor and before the iSTFTNet
decoder. The decoder then voices the edited curves natively, so nothing is stretched after the fact.
Voice packs are [510, 1, 256]: [..., :128] = timbre (decoder), [..., 128:] = delivery/prosody (predictor).

Findings that shaped this file:
- Kokoro is deterministic in timing and pitch (same line -> identical durations and F0). Hence `human`.
- The predicted energy curve (N) is nearly ignored by the decoder: loudness = plain gain.
- F0 = 0 or a sub-20 Hz F0 inside the model = gravel / audible tone. Rejected for whisper.
- Pitch maths must be in semitones on voiced frames only; smoothing F0 itself (not the offset) or widening
  the range lets fade-out frames sag to 20-60 Hz = growl. `floor` ends voicing cleanly instead.
- Loudness comparisons must be ear-weighted (A-weighting): whisper energy sits where hearing is most sensitive.
"""
import sys, warnings
from pathlib import Path
import numpy as np, torch, soundfile as sf, librosa
import scipy.ndimage as nd
from scipy.signal import butter, sosfilt

warnings.filterwarnings('ignore')
from kokoro import KPipeline

SR = 24000
VOICED_HZ = 70.0                    # F0 below this = onset/fade-out frames; never reshape them
PUNCT = set(',.;:!?—…')
ROOT = Path(__file__).resolve().parents[1]
VOICES = ROOT / 'stories' / 'voices'

pipe = KPipeline(repo_id='hexgrad/Kokoro-82M', lang_code='a')
m = pipe.model


def load_pack(voice):
    """'julia' -> stories/voices/julia.pt; 'af_nicole' -> Kokoro stock voice; tensors pass through."""
    if torch.is_tensor(voice):
        return voice
    if voice[:3] in ('af_', 'am_', 'bf_', 'bm_'):
        return pipe.load_voice(voice)
    return torch.load(VOICES / f'{voice}.pt', map_location='cpu')


def soft_pack(voice):
    """the voice's timbre with af_nicole's soft delivery"""
    return torch.cat([load_pack(voice)[..., :128], pipe.load_voice('af_nicole')[..., 128:]], dim=-1)


def word_spans(ps):
    """(start, end) token spans of each space-separated phoneme word; ids carry a leading pad, hence +1.
    NOTE for the real generator: map marked words via the g2p tokens instead (see VOICE_PLAN.md)."""
    spans, start = [], None
    for i, c in enumerate(ps + ' '):
        if c != ' ' and start is None:
            start = i
        elif c == ' ' and start is not None:
            spans.append((start + 1, i + 1)); start = None
    return spans


@torch.no_grad()
def synth(text, voice, speed=1.0, pitch=0.0, range_=1.0, drift=0.0, pause=1.0, words=None, floor=0.0,
          human=0.0, seed=0, return_f0=False):
    """
    speed   : divides all durations (as Kokoro's own speed)
    pitch   : semitones, whole line
    range_  : scale of pitch movement around the median (0 = dead flat, >1 = livelier)
    drift   : semitones of slow fall across the line
    pause   : multiplier on punctuation durations
    words   : {word_index: {'pitch': st, 'dur': x}} per-word stress / stretch
    floor   : end voicing when F0 sags below floor * median (0 = off; 0.55 used for shout/soft)
    human   : variation strength (1.0 = approved default); seeded by `seed`, so reproducible
    """
    pack = load_pack(voice)
    tokens = pipe.g2p(text)[1]
    ps = ''.join(t.phonemes + (' ' if t.whitespace else '') for t in tokens if t.phonemes).strip()
    ps = ''.join(c for c in ps if c in m.vocab)
    if len(ps) > 510:
        raise ValueError(f'{len(ps)} phonemes; split the line (Kokoro max 510)')
    ids = torch.LongTensor([[0] + [m.vocab[c] for c in ps] + [0]])
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
    spans = word_spans(ps)
    words = words or {}
    rng = np.random.default_rng(seed)
    if human:
        dur *= float(np.exp(rng.normal(0, 0.03 * human)))                 # line tempo
        for a, b in spans:
            dur[a:b] *= float(np.exp(rng.normal(0, 0.07 * human)))       # each word
        for i, c in enumerate(ps):
            if c in PUNCT:
                dur[i + 1] *= float(np.exp(rng.normal(0, 0.15 * human)))  # each pause
    for wi, o in words.items():
        a, b = spans[wi]; dur[a:b] *= o.get('dur', 1.0)
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
    for wi, o in words.items():
        a, b = spans[wi]; st[int(starts[a]):int(ends[b - 1])] += o.get('pitch', 0)
    if human:
        pk = 0.35 * human * min(1.0, range_ + 0.25)                       # flat styles stay flat
        st += float(rng.normal(0, 0.4 * pk / 0.35))
        for a, b in spans:
            st[int(starts[a]):int(ends[b - 1])] += float(rng.normal(0, pk))
        wander = torch.tensor(nd.gaussian_filter1d(rng.normal(0, 1, f.shape[0]), 25), dtype=torch.float32)
        st += wander / (wander.std() + 1e-9) * 0.3 * pk / 0.35
    if words or human:   # smooth the OFFSET (edge-replicated), never F0 itself
        k = torch.ones(1, 1, 9) / 9
        st = torch.nn.functional.conv1d(torch.nn.functional.pad(st.view(1, 1, -1), (4, 4), mode='replicate'), k).view(-1)
    f = f * 2 ** (st / 12)
    if floor and v.any():
        lim = max(VOICED_HZ, float(f[f > VOICED_HZ].median()) * floor)
        f = torch.where(f < lim, torch.zeros_like(f), f)

    t_en = m.text_encoder(ids, L, mask)
    audio = m.decoder(t_en @ aln, f.view(1, -1), N, ref_s[:, :128]).squeeze().numpy()
    return (audio, f.numpy()) if return_f0 else audio


# =====================================================================
# Loudness
# =====================================================================
def hp(y, hz):
    return sosfilt(butter(4, hz, btype='highpass', fs=SR, output='sos'), y)


def aw(y):
    """ear-weighted (A-weighted) level in dB, arbitrary reference"""
    S = np.abs(librosa.stft(y, n_fft=2048)) ** 2
    w = 10 ** (librosa.A_weighting(librosa.fft_frequencies(sr=SR, n_fft=2048)) / 10)
    return 10 * np.log10((S * w[:, None]).sum(0).mean() + 1e-12)


def level(y, target_aw):
    return y * 10 ** ((target_aw - aw(y)) / 20)


def env_db(y, ms=10):
    r = np.sqrt(nd.uniform_filter1d(y ** 2, int(SR * ms / 1000)) + 1e-12)
    return 20 * np.log10(r)


# =====================================================================
# Whisper (approved: "w65") — model consonants + breath-coloured noise on vowels
# =====================================================================
H_TEXT = 'Hush. Hat. Hope. Who. Hum. Hush. Hard. Hold. Hush. Heat. Hush. Ahh. Hush. Huff. Hush. Hoop. Shh. Hush.'
_breath_cache = {}


def harvest_breath(voice):
    """the voice's own unvoiced 'h' sounds, cut from a sentence of h-words (only ~0.1-0.2 s per voice).
    Used only for its average spectrum (colour): looping it would flutter."""
    key = voice if isinstance(voice, str) else id(voice)
    if key in _breath_cache:
        return _breath_cache[key]
    a, f = synth(H_TEXT, voice, speed=0.8, return_f0=True)
    uvf = np.interp(np.arange(len(a)) * len(f) / len(a), np.arange(len(f)), (f <= VOICED_HZ).astype(float))
    n, hop = 1024, 256
    S = np.abs(librosa.stft(a, n_fft=n, hop_length=hop)) ** 2
    fr = librosa.fft_frequencies(sr=SR, n_fft=n)
    cen = (S * fr[:, None]).sum(0) / (S.sum(0) + 1e-12)
    lvl = 10 * np.log10(S.sum(0) + 1e-12)
    keep = np.zeros(len(a), bool)
    for i in range(S.shape[1]):
        s0, s1 = i * hop, min(len(a), i * hop + hop)
        if uvf[s0:s1].mean() > 0.8 and 300 < cen[i] < 2600 and lvl[i] > lvl.max() - 40:
            keep[s0:s1] = True
    pieces, i = [], 0
    while i < len(a):
        if keep[i]:
            j = i
            while j < len(a) and keep[j]:
                j += 1
            if j - i > 0.02 * SR:
                r = a[i:j].copy(); fade = int(0.008 * SR)
                r[:fade] *= np.linspace(0, 1, fade); r[-fade:] *= np.linspace(1, 0, fade)
                pieces.append(r / (np.sqrt(np.mean(r ** 2)) + 1e-9))
            i = j
        else:
            i += 1
    if not pieces:
        raise RuntimeError('no breath harvested for this voice')
    _breath_cache[key] = np.concatenate(pieces).astype(np.float32)
    return _breath_cache[key]


def coloured_noise(breath, n_samples, seed):
    n = 2048
    P = nd.gaussian_filter1d(np.mean(np.abs(librosa.stft(breath, n_fft=n, hop_length=n // 4)) ** 2, axis=1), 3)
    N = librosa.stft(np.random.default_rng(seed).standard_normal(n_samples), n_fft=n, hop_length=n // 4)
    return librosa.istft(N * np.sqrt(P)[:, None], hop_length=n // 4, length=n_samples).astype(np.float32)


def shaped_breath(y, excitation, lifter=22, smooth_ms=25):
    """the spoken line's vowel shape (cepstrally smoothed, time-smoothed) laid over the excitation"""
    n_fft, hop = 1024, 120
    Y = librosa.stft(y, n_fft=n_fft, hop_length=hop)
    c = np.fft.irfft(np.log(np.abs(Y) + 1e-7), axis=0); c[lifter:-lifter] = 0
    env = np.fft.rfft(c, axis=0).real[:Y.shape[0]]
    env = nd.gaussian_filter1d(env, (smooth_ms / 1000) * SR / hop / 2.355, axis=1, mode='nearest')
    E = librosa.stft(excitation, n_fft=n_fft, hop_length=hop)[:, :env.shape[1]]
    E /= np.sqrt(np.mean(np.abs(E) ** 2))
    out = librosa.istft(E * np.exp(env), hop_length=hop, length=len(y))
    return hp(out, 150).astype(np.float32)


def whisper(text, voice, seed=0, vowel_db=-10.0, expand=1.6, **kw):
    """consonants straight from the model; voiced stretches replaced by breath that follows the line's loudness"""
    a, f = synth(text, voice, seed=seed, return_f0=True, **kw)
    vm = np.interp(np.arange(len(a)) * len(f) / len(a), np.arange(len(f)), (f > VOICED_HZ).astype(float))
    vm = nd.gaussian_filter1d(vm, SR * 0.008)
    br = shaped_breath(a, coloured_noise(harvest_breath(voice), len(a) + 1024, seed))
    tgt = env_db(a); top = tgt.max()
    gain = 10 ** (nd.gaussian_filter1d(top + (tgt - top) * expand - env_db(br), SR * 0.004) / 20)
    return ((1 - vm) * a + vm * br * gain * 10 ** (vowel_db / 20)).astype(np.float32)


# =====================================================================
# Approved styles (settings relative to the cast member's own speed)
# =====================================================================
TRANCE_LIGHT = dict(speed=0.85, pitch=-1.5, range_=0.45, pause=1.2, drift=0.0)   # "t02", depth 0
TRANCE_DEEP = dict(speed=0.75, pitch=-3.5, range_=0.20, pause=2.5, drift=1.0)    # "t04", depth 1


def trance(depth):
    return {k: TRANCE_LIGHT[k] + (TRANCE_DEEP[k] - TRANCE_LIGHT[k]) * depth for k in TRANCE_LIGHT}


STYLES = {
    'normal': dict(),
    'deep': dict(speed=0.75, pitch=-3.0, range_=0.0, pause=2.0),                 # "m05": user's favourite
    'shout': dict(pitch=4.0, range_=1.4, floor=0.55),                             # "s06/s07"
    'soft': dict(speed=0.88, pitch=-2.0, range_=0.55, floor=0.55),                # "p01", soft_pack(), -10 dB
    'whisper': dict(speed=0.88, range_=0.6),                                      # "w65", whisper(), -12 dB
}
STYLE_LEVEL_DB = {'soft': -10.0, 'whisper': -12.0}   # ear-weighted, relative to a normal line


if __name__ == '__main__':
    out = Path(sys.argv[1] if len(sys.argv) > 1 else 'stories/voices/reference')
    out.mkdir(parents=True, exist_ok=True)
    plain_j = synth('I need to wear my glasses.', 'julia')
    ref = aw(plain_j)

    def save(name, a, db=0.0):
        a = level(a, ref + db)
        sf.write(out / f'{name}.wav', a.astype(np.float32), SR)
        print(f'{name:28} {len(a) / SR:4.1f}s')

    t = 'Let everything else fade away. Nothing else matters but my eyes, and my voice.'
    for d in (0.0, 0.5, 1.0):
        save(f'trance_depth_{d:.1f}', synth(t, 'harris', **trance(d)))
    save('deep_m05', synth('I need to wear my glasses. I need to wear my glasses.', 'julia', **STYLES['deep']))
    save('shout_s07', synth('Yours!', 'julia', **STYLES['shout']))
    w = 'You may stop talking now, Julia.'
    save('soft_p01', synth(w, soft_pack('harris'), **STYLES['soft']), STYLE_LEVEL_DB['soft'])
    save('whisper_w65', whisper(w, 'harris', **STYLES['whisper']), STYLE_LEVEL_DB['whisper'])
    gap = np.zeros(int(0.6 * SR), np.float32)
    mantra = [synth('I need to wear my glasses!', 'julia', human=1.0, seed=i) for i in range(4)]
    save('human_medium_x4', np.concatenate([x for c in mantra for x in (c, gap)]))
