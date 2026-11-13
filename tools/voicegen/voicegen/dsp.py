"""Signal processing that doesn't need the model: loudness, trimming, whisper breath, the `internal` room."""
import numpy as np
import librosa
import scipy.ndimage as nd
from scipy.signal import butter, sosfilt, fftconvolve

SR = 24000
# Ear-weighted level every clip is set to (before its style offset): aw() of a plain Julia line from the
# 2026-11-11 reference set. Peaks of a levelled line sit around -8 dBFS, so shouts have headroom.
LEVEL_TARGET = 32.1
PEAK_LIMIT = 0.95


def hp(y, hz):
    return sosfilt(butter(4, hz, btype='highpass', fs=SR, output='sos'), y)


def lp(y, hz):
    return sosfilt(butter(2, hz, btype='lowpass', fs=SR, output='sos'), y)


def aw(y):
    """ear-weighted (A-weighted) level in dB"""
    S = np.abs(librosa.stft(np.asarray(y, np.float32), n_fft=2048)) ** 2
    w = 10 ** (librosa.A_weighting(librosa.fft_frequencies(sr=SR, n_fft=2048)) / 10)
    return float(10 * np.log10((S * w[:, None]).sum(0).mean() + 1e-12))


def level(y, offset_db=0.0):
    """set the clip to LEVEL_TARGET + offset_db; scale down only if a peak would clip"""
    y = y * 10 ** ((LEVEL_TARGET + offset_db - aw(y)) / 20)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > PEAK_LIMIT:
        y = y * (PEAK_LIMIT / peak)
    return y.astype(np.float32)


def trim(y, top_db=45, lead=0.05, tail=0.15):
    """cut leading/trailing silence, keeping a short margin"""
    if not len(y):
        return y
    _, (a, b) = librosa.effects.trim(y, top_db=top_db, frame_length=512, hop_length=128)
    a = max(0, a - int(lead * SR)); b = min(len(y), b + int(tail * SR))
    return y[a:b]


def frames_to_samples(mask_frames, n_samples):
    """a per-F0-frame curve stretched to sample rate"""
    return np.interp(np.arange(n_samples) * len(mask_frames) / n_samples, np.arange(len(mask_frames)),
                     mask_frames.astype(float))


# =====================================================================
# Whisper: the model's own speech turned to breath, calibrated against a real whisper
# =====================================================================
# Chosen 2026-11-12 over eight listening rounds against reference recordings (round 8 "e_smoothed");
# replaces "w65". Voiced stretches become noise through the line's own per-frame vowel shape, with the
# voice's long-term tone swapped for a real whisper's (whisper_profile.VOWEL). Unvoiced stretches keep the
# model's consonants, EQ'd toward real whisper hiss (whisper_profile.FRIC) and given a hiss tail where they
# stop dead. Bits that sound voiced but carry no pitch label are kept, turned down and eased in and out.
# Then a de-esser and the listener's two low-mid cuts.
# lifter: cepstral cut-off of the vowel shape (lower = smoother); smooth_ms: how slowly the shape changes;
# tilt: dB per octave above 1 kHz on the breath; steady: 0..1, evens out frame-to-frame breath level;
# colour: [(Hz, dB)] laid on the noise (the 2026-11-12 "guideline": +2 dB at 2-5 kHz, -1.5 dB at 5-8 kHz);
# vowel_db / cons_db: breath and consonant gain; eq_max: limit of the consonant EQ (dB);
# tail_ms: consonant hiss falls 40 dB over this; xfade_ms: breath/consonant crossfade;
# duck_db: gain on voice-like bits without pitch; deess: threshold over the line's median level (dB),
# ratio, lowest frequency; cuts: [(Hz, dB, Q)] peaking filters on the result (found by ear with a sweep).
WHISPER = dict(lifter=16, smooth_ms=35, tilt=-2.0, steady=0.4,
               colour=[(80, -6), (150, -1), (500, 0), (1000, 0), (2000, 1.5), (3000, 2), (5000, 2),
                       (6500, -1.5), (8000, -1.5), (10000, 0), (12000, 0)],
               vowel_db=-10.0, cons_db=0.0, eq_max=10.0, tail_ms=90, xfade_ms=25, duck_db=-12.0,
               deess=dict(thresh_db=9.0, ratio=4.0, lo=3500), low_cut=90,
               cuts=[(623, -17.0, 0.4), (546, -10.0, 0.45)])

_LN = np.log(10) / 20          # dB -> natural log of amplitude


def _curve(points, fr):
    f, d = zip(*points)
    return np.interp(np.log(np.maximum(fr, 20)), np.log(f), d)


def _cepstral_env(Y, lifter):
    """log-magnitude spectral envelope of each STFT frame"""
    c = np.fft.irfft(np.log(np.abs(Y) + 1e-7), axis=0); c[lifter:-lifter] = 0
    return np.fft.rfft(c, axis=0).real[:Y.shape[0]]


def _smooth_frames(x, ms, hop):
    return nd.gaussian_filter1d(x, (ms / 1000) * SR / hop / 2.355, axis=-1, mode='nearest')


def peaking(y, hz, gain_db, q):
    """RBJ peaking biquad (the same filter as Web Audio's BiquadFilterNode 'peaking')"""
    from scipy.signal import lfilter
    A = 10 ** (gain_db / 40); w = 2 * np.pi * hz / SR; al = np.sin(w) / (2 * q)
    b = np.array([1 + al * A, -2 * np.cos(w), 1 - al * A]); a = np.array([1 + al / A, -2 * np.cos(w), 1 - al / A])
    return lfilter(b / a[0], a / a[0], y)


def deess(y, thresh_db=9.0, ratio=4.0, lo=3500):
    """turn down lo Hz and up (soft knee from lo - 800 Hz) only where that band rises more than thresh_db
    above the line's median level; 2 ms attack, 60 ms release"""
    n, hop = 512, 60
    Y = librosa.stft(y, n_fft=n, hop_length=hop); fr = librosa.fft_frequencies(sr=SR, n_fft=n)
    hi = 10 * np.log10((np.abs(Y[fr >= lo]) ** 2).sum(0) + 1e-12)
    tot = 10 * np.log10((np.abs(Y) ** 2).sum(0) + 1e-12)
    ref = np.median(tot[tot > tot.max() - 35])
    red = np.maximum(0, hi - (ref + thresh_db)) * (1 - 1 / ratio)
    g = np.zeros_like(red); cur = 0.0
    att = np.exp(-1 / (0.002 * SR / hop)); rel = np.exp(-1 / (0.06 * SR / hop))
    for i, v in enumerate(red):
        k = att if v > cur else rel
        cur = k * cur + (1 - k) * v; g[i] = cur
    knee = np.clip((fr - lo + 800) / 800, 0, 1)
    return librosa.istft(Y * 10 ** (-(knee[:, None] * g[None, :]) / 20), hop_length=hop, length=len(y))


def breathy(audio, voiced, amount, seed):
    """partial whisper: the voice mixed with its own whisper (dsp.whisper), matched in ear-weighted loudness.
    amount: 0 = the voice, 1 = all whisper (constant-power mix)"""
    w = whisper(audio, voiced, seed)
    w = w * 10 ** ((aw(audio) - aw(w)) / 20)
    return (np.sqrt(1 - amount) * audio + np.sqrt(amount) * w).astype(np.float32)


def whisper(audio, voiced, seed, p=None):
    """audio: the model's line; voiced: per-sample 0..1 (1 = has F0); p: a dict shaped like WHISPER"""
    from .whisper_profile import VOWEL, FRIC
    p = p or WHISPER
    n, hop = 1024, 120
    Y = librosa.stft(audio, n_fft=n, hop_length=hop); nf = Y.shape[1]
    fr = librosa.fft_frequencies(sr=SR, n_fft=n)
    P2 = np.abs(Y) ** 2
    lvl = 10 * np.log10(P2.sum(0) + 1e-12)
    vfr = np.array([voiced[i * hop:i * hop + hop].mean() if i * hop < len(voiced) else 0.0 for i in range(nf)])
    vm = (lvl > lvl.max() - 35) & (vfr > 0.7)                 # vowel frames
    um = (lvl > lvl.max() - 35) & (vfr < 0.3)                 # consonant frames
    # voice-like frames the pitch tracker missed: strong lows, little hiss
    buzzy = (lvl > lvl.max() - 30) & (vfr < 0.5) & (P2[(fr > 80) & (fr < 400)].sum(0) > 2 * P2[fr > 2500].sum(0))
    buzzy = np.pad(np.repeat(buzzy.astype(float), hop), (0, max(0, len(audio) - nf * hop)))[:len(audio)]
    mid = (fr > 490) & (fr < 3050)

    # breath: own vowel shapes, whisper's long-term tone
    E = _cepstral_env(Y, p['lifter'])
    Ev = E - E[:, vm].mean(1)[:, None] + (_curve(VOWEL, fr) * _LN)[:, None]
    Ev += (E - Ev).mean(0)[None, :]                           # each frame keeps its loudness
    Ev += p['tilt'] * _LN * np.maximum(np.log2(np.maximum(fr, 250) / 1000), 0)[:, None]
    L = Ev.mean(0); Ev -= p['steady'] * (L - _smooth_frames(L, 250, hop))[None, :]
    Ev = _smooth_frames(Ev, p['smooth_ms'], hop)
    rng = np.random.default_rng(seed)
    N = librosa.stft(rng.standard_normal(len(audio) + n), n_fft=n, hop_length=hop)[:, :nf]
    N *= 10 ** (_curve(p['colour'], fr) / 20)[:, None]
    N /= np.sqrt(np.mean(np.abs(N) ** 2))
    breath = librosa.istft(N * np.exp(Ev), hop_length=hop, length=len(audio))

    # consonants: the model's, EQ'd toward whisper hiss, with a tail where they stop dead
    Mu = nd.gaussian_filter1d(np.log(np.sqrt(P2[:, um].mean(1)) + 1e-7), 3)
    tf = _curve(FRIC, fr) * _LN
    eq = np.clip((tf - tf[mid].mean()) - (Mu - Mu[mid].mean()), -p['eq_max'] * _LN, p['eq_max'] * _LN)
    C = Y * np.exp(eq)[:, None]
    Lc = np.log(np.sqrt((np.abs(C) ** 2).mean(0)) + 1e-9); Ec = _cepstral_env(C, p['lifter'])
    Lr = Lc.copy(); H = np.zeros_like(Ec); hold = Ec[:, 0] - Ec[:, 0].mean()
    step = 40 * _LN * hop / (p['tail_ms'] / 1000 * SR)
    for i in range(nf):
        if i and Lr[i - 1] - step > Lc[i]:
            Lr[i] = Lr[i - 1] - step
        else:
            Lr[i] = Lc[i]; hold = Ec[:, i] - Ec[:, i].mean()
        H[:, i] = hold
    fill = np.sqrt(np.maximum(0, 1 - np.exp(2 * (Lc - Lr))))  # only the part that is missing
    Nt = librosa.stft(np.random.default_rng(seed + 1).standard_normal(len(audio) + n), n_fft=n, hop_length=hop)[:, :nf]
    Nt /= np.sqrt(np.mean(np.abs(Nt) ** 2))
    Hs = np.exp(H); Hs /= np.sqrt((Hs ** 2).mean(0))[None, :]
    cons = librosa.istft(C + Nt * Hs * (np.exp(Lr) * fill)[None, :], hop_length=hop, length=len(audio))

    vs = nd.gaussian_filter1d(voiced.astype(float), SR * p['xfade_ms'] / 1000)
    cg = 10 ** ((p['cons_db'] + p['duck_db'] * nd.gaussian_filter1d(buzzy, SR * 0.015)) / 20)
    out = (1 - vs) * cons * cg + vs * breath * 10 ** (p['vowel_db'] / 20)
    out = hp(deess(out, **p['deess']), p['low_cut'])
    for hz, g, q in p['cuts']:
        out = peaking(out, hz, g, q)
    return out.astype(np.float32)


# =====================================================================
# `internal`: thoughts = the voice plus a soft echo, with a trace of a long room
# =====================================================================
# Chosen 2026-11-12 over four listening rounds ("p_k_wet05"): the user wanted an echo rather than a room;
# a strong room on top of the echo compounds, so the room is only a trace.
# echo: delay (ms) between repeats, feedback = gain of each repeat relative to the one before,
#       level = gain of the first repeat, tone = low-pass applied again on every repeat (darker each time),
#       taps = explicit [(ms, gain), ...] instead of a regular delay
# room: decay = RT60 (s), wet = share of reverb (0..1), tone = reverb low-pass (Hz), predelay (ms)
# dry_tone: low-pass on the direct voice (None = untouched)
INTERNAL = dict(echo=dict(delay=220, feedback=0.2, level=0.11, tone=3200),     # 2026-11-12 round 1: echo halved
                room=dict(decay=0.9, wet=0.03, tone=3500, predelay=30),
                dry_tone=6500)


def room_ir(decay, tone, predelay, seed=7, **_):
    """exponentially decaying noise + a few early reflections, darkened; unit energy"""
    n = int(SR * (decay * 1.2 + predelay / 1000))
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal(n) * 10 ** (-3 * t / decay)               # -60 dB at t = decay
    ir[:int(SR * predelay / 1000)] = 0
    for ms, g in ((predelay + 4, 0.6), (predelay + 9, -0.45), (predelay + 15, 0.35), (predelay + 22, -0.25)):
        k = int(SR * ms / 1000)
        if k < n:
            ir[k] += g * 8
    ir = lp(ir, tone)
    return (ir / np.sqrt(np.sum(ir ** 2))).astype(np.float32)


def echo_ir(tone, delay=None, feedback=0.4, level=0.45, taps=None):
    """discrete repeats, each one darker than the last; the direct sound is not included"""
    if taps is None:
        taps, g, k = [], level, 1
        while g > 0.01 and k < 40:
            taps.append((delay * k, g)); g *= feedback; k += 1
    n = int(SR * (taps[-1][0] / 1000 + 0.05))
    ir = np.zeros(n)
    h = np.zeros(1024); h[0] = 1.0
    for ms, g in taps:
        h = lp(h, tone)                                                    # one more pass per repeat
        k = int(SR * ms / 1000)
        seg = h[:n - k]
        ir[k:k + len(seg)] += g * seg
    return ir.astype(np.float32)


def internal(y, params=None):
    """params: a dict shaped like INTERNAL (default); either 'echo' or 'room' may be left out"""
    p = params or INTERNAL
    tail = 0
    if 'echo' in p:
        e_ir = echo_ir(**p['echo']); tail += len(e_ir)
    if 'room' in p:
        r_ir = room_ir(**p['room']); tail += len(r_ir)
    dry = np.concatenate([y, np.zeros(tail, np.float32)])
    if p.get('dry_tone'):
        dry = lp(dry, p['dry_tone'])
    out = dry
    if 'echo' in p:
        out = out + fftconvolve(dry, e_ir)[:len(dry)]
    if 'room' in p:
        wet = fftconvolve(out, r_ir)[:len(dry)]
        # equal-loudness blend: match the reverb's level to what feeds it, then mix
        wet *= np.sqrt(np.sum(out ** 2) / (np.sum(wet ** 2) + 1e-12))
        out = (1 - p['room']['wet']) * out + p['room']['wet'] * wet
    return trim(out.astype(np.float32), top_db=55, lead=0.05, tail=0.05)
