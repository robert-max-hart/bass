"""voicegen: directed voice lines from Kokoro TTS (styles, word stress, variation, effects).

    from voicegen import Engine, write
    eng = Engine()
    r = eng.render('Close your [eyes](+)...', 'voices/harris.pt', 'trance 0.6', seed=1)
    write('line.ogg', r.audio)
"""
__version__ = '0.1.0'
ENGINE_VERSION = 1      # bump when the sound changes, so cached clips are rebuilt

from .common import make_seed   # noqa: E402,F401


def __getattr__(name):          # the engine pulls in torch/librosa; load it only when it's used
    if name in ('Engine', 'write'):
        from . import engine
        return getattr(engine, name)
    raise AttributeError(name)
