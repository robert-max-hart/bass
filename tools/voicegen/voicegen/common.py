"""Small helpers with no heavy imports (safe for script parsing and adapters)."""
import hashlib

STOCK_PREFIXES = ('af_', 'am_', 'bf_', 'bm_')


def make_seed(*parts):
    """stable across runs and platforms (Python's hash() is salted per process)"""
    h = hashlib.sha256('\x1f'.join(str(p) for p in parts).encode('utf-8')).digest()
    return int.from_bytes(h[:8], 'little')
