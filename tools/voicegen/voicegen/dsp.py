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


def env_db(y, ms=10):
    r = np.sqrt(nd.uniform_filter1d(y ** 2, int(SR * ms / 1000)) + 1e-12)
    return 20 * np.log10(r)


def frames_to_samples(mask_frames, n_samples):
    """a per-F0-frame curve stretched to sample rate"""
    return np.interp(np.arange(n_samples) * len(mask_frames) / n_samples, np.arange(len(mask_frames)),
                     mask_frames.astype(float))


# =====================================================================
# Whisper ("w65"): breath colour, shaped by the spoken line
# =====================================================================
def cut_breath(audio, unvoiced):
    """keep the unvoiced 'h'-like stretches of a sentence of h-words; returns them joined, each normalised.
    unvoiced: per-sample 0..1 (1 = no F0)."""
    n, hop = 1024, 256
    S = np.abs(librosa.stft(audio, n_fft=n, hop_length=hop)) ** 2
    fr = librosa.fft_frequencies(sr=SR, n_fft=n)
    cen = (S * fr[:, None]).sum(0) / (S.sum(0) + 1e-12)
    lvl = 10 * np.log10(S.sum(0) + 1e-12)
    keep = np.zeros(len(audio), bool)
    for i in range(S.shape[1]):
        s0, s1 = i * hop, min(len(audio), i * hop + hop)
        if s0 < s1 and unvoiced[s0:s1].mean() > 0.8 and 300 < cen[i] < 2600 and lvl[i] > lvl.max() - 40:
            keep[s0:s1] = True
    pieces, i = [], 0
    while i < len(audio):
        if keep[i]:
            j = i
            while j < len(audio) and keep[j]:
                j += 1
            if j - i > 0.02 * SR:
                r = audio[i:j].copy(); fade = int(0.008 * SR)
                r[:fade] *= np.linspace(0, 1, fade); r[-fade:] *= np.linspace(1, 0, fade)
                pieces.append(r / (np.sqrt(np.mean(r ** 2)) + 1e-9))
            i = j
        else:
            i += 1
    if not pieces:
        raise RuntimeError('no breath could be cut from this voice')
    return np.concatenate(pieces).astype(np.float32)


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


def whisper_mix(audio, voiced, breath, seed, vowel_db=-10.0, expand=1.6):
    """consonants straight from the model; voiced stretches replaced by breath following the line's loudness.
    voiced: per-sample 0..1."""
    vm = nd.gaussian_filter1d(voiced, SR * 0.008)
    br = shaped_breath(audio, coloured_noise(breath, len(audio) + 1024, seed))
    tgt = env_db(audio); top = tgt.max()
    gain = 10 ** (nd.gaussian_filter1d(top + (tgt - top) * expand - env_db(br), SR * 0.004) / 20)
    return ((1 - vm) * audio + vm * br * gain * 10 ** (vowel_db / 20)).astype(np.float32)


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
INTERNAL = dict(echo=dict(delay=220, feedback=0.25, level=0.22, tone=3200),
                room=dict(decay=0.9, wet=0.05, tone=3500, predelay=30),
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
