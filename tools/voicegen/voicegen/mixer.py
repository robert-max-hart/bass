"""voicegen mixer: a local web page for blending and tuning voice packs by ear.

    voicegen mixer --voices-dir stories/voices [--script stories/x/voice_script.txt] [--port 8765]

Serves on 127.0.0.1 only. Saving writes <name>.pt and <name>.recipe.json to --save-dir (default: the first
--voices-dir); an existing file is kept as <name>.pt.bak-<time> before it is replaced.
"""
import base64
import io
import json
import re
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import voicelab

PAGE = Path(__file__).with_name('mixer.html')
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


class Mixer:
    def __init__(self, voices_dirs, save_dir, script=None, model_dir=None):
        from .engine import Engine
        print('voicegen mixer: loading Kokoro...')
        self.eng = Engine(model_dir=model_dir)
        self.lab = voicelab.Lab(self.eng, voices_dirs)
        self.lab.axes()
        self.save_dir = Path(save_dir) if save_dir else None
        self.lock = threading.Lock()                     # the engine is not thread-safe
        self.lines = []
        if script:
            from .script import Script
            jobs, _ = Script.load(script).jobs(voices_dirs[0] if voices_dirs else None)
            self.lines = [{'id': j.id, 'speaker': j.speaker, 'direction': str(j.direction), 'text': j.text}
                          for j in jobs]

    # ------------------------------------------------------------------ API
    def info(self, _):
        return {'sources': self.lab.sources(), 'axes': self.lab.axes_info(), 'lines': self.lines,
                'save_dir': str(self.save_dir) if self.save_dir else None, 'halves': list(voicelab.HALVES)}

    def load(self, q):
        src = q['source']
        with self.lock:
            path = self.lab.user.get(src)
            if path and voicelab.recipe_path(path).exists():
                r = self.lab.local(voicelab.load_recipe(voicelab.recipe_path(path)), Path(path).parent)
                return {'recipe': r, 'how': f'its recipe file ({voicelab.recipe_path(path).name})'}
            r, fits = self.lab.decompose(src)
            exact = [h for h, f in fits.items() if f < 1e-4]
            how = ('found the stock blend it was made from' if len(exact) == 2 else
                   f'found the stock blend for its {exact[0]}; the rest is the file itself' if exact else
                   'the file itself (not a blend of stock voices)')
            return {'recipe': r, 'how': how}

    def render(self, q):
        with self.lock:
            pack = self.lab.build(q['recipe'])
            t = time.time()
            r = self.eng.render(q.get('text') or 'Hello.', pack, q.get('direction') or '', seed=int(q.get('seed', 0)))
            took = time.time() - t
            pos = self.lab.position(pack)
        import soundfile as sf
        buf = io.BytesIO()
        sf.write(buf, r.audio, 24000, format='WAV', subtype='PCM_16')
        return {'audio': base64.b64encode(buf.getvalue()).decode('ascii'), 'seconds': round(len(r.audio) / 24000, 2),
                'took': round(took, 1), 'position': pos,
                'warnings': [f'could not read "{w}"' for w in r.unknown_words] +
                            [f'mark on "{w}" did not land' for w in r.unplaced_marks]}

    def position(self, q):
        with self.lock:
            return {'position': self.lab.position(self.lab.build(q['recipe']))}

    def variants(self, q):
        halves = [h for h in q.get('halves', voicelab.HALVES) if h in voicelab.HALVES]
        return {'recipes': self.lab.variations(q['recipe'], int(q.get('n', 6)), float(q.get('step', 0.5)), halves)}

    def save(self, q):
        if not self.save_dir:
            raise ValueError('no --save-dir (or --voices-dir) to save into')
        name = str(q.get('name', '')).strip()
        if name.endswith('.pt'):
            name = name[:-3]
        if not NAME_RE.match(name):
            raise ValueError('name: letters, digits, _ - . only')
        pt = self.save_dir / f'{name}.pt'
        backup = None
        if pt.exists():
            if not q.get('overwrite'):
                return {'exists': True, 'path': str(pt)}
            stamp = time.strftime('%Y%m%d-%H%M%S')
            backup = pt.with_name(f'{pt.name}.bak-{stamp}')
            pt.rename(backup)
            rp = voicelab.recipe_path(pt)
            if rp.exists():
                rp.rename(rp.with_name(f'{rp.name}.bak-{stamp}'))
        with self.lock:
            self.lab.save(q['recipe'], pt)
        return {'path': str(pt), 'backup': str(backup) if backup else None, 'sources': self.lab.sources()}


def serve(mixer, port=8765, open_browser=True):
    routes = {'/api/info': mixer.info, '/api/load': mixer.load, '/api/render': mixer.render,
              '/api/position': mixer.position, '/api/variants': mixer.variants, '/api/save': mixer.save}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ('/', '/index.html'):
                self.send(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
            elif self.path == '/api/info':
                self.api(mixer.info, {})
            else:
                self.send(404, b'not found', 'text/plain')

        def do_POST(self):
            fn = routes.get(self.path)
            if not fn:
                return self.send(404, b'not found', 'text/plain')
            n = int(self.headers.get('Content-Length') or 0)
            try:
                q = json.loads(self.rfile.read(n) or b'{}')
            except ValueError:
                return self.send(400, b'bad json', 'text/plain')
            self.api(fn, q)

        def api(self, fn, q):
            try:
                out, code = fn(q), 200
            except (ValueError, KeyError, FileNotFoundError, RuntimeError) as e:
                out, code = {'error': str(e)}, 400
            self.send(code, json.dumps(out).encode('utf-8'), 'application/json')

    httpd = ThreadingHTTPServer(('127.0.0.1', port), H)
    url = f'http://127.0.0.1:{port}/'
    print(f'voicegen mixer: {url}  (Ctrl+C to stop)')
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
