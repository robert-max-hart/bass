"""Small helpers with no heavy imports (safe for script parsing and adapters)."""
import hashlib

# Kokoro stock voices: <language><f|m>_name (a = US, b = UK, e, f, h, i, j, p, z)
STOCK_PREFIXES = tuple(f'{l}{g}_' for l in 'abefhijpz' for g in 'fm')


def make_seed(*parts):
    """stable across runs and platforms (Python's hash() is salted per process)"""
    h = hashlib.sha256('\x1f'.join(str(p) for p in parts).encode('utf-8')).digest()
    return int.from_bytes(h[:8], 'little')
