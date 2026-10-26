"""Tiny loopback-only static server for the renderer page. Only four things are reachable, all under a random per-run token prefix:
  /<tok>/index.html, host.js      engines/web
  /<tok>/v/<file>                 engines/web/vendor (flat)
  /<tok>/core.js                  the one Cubism Core file the user picked
  /<tok>/m/<path>                 the model folder (path traversal is rejected)
"""
import http.server, secrets
from pathlib import Path
from urllib.parse import unquote, urlsplit

MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.json': 'application/json', '.png': 'image/png',
        '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.moc3': 'application/octet-stream', '.vrm': 'model/gltf-binary',
        '.glb': 'model/gltf-binary', '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg'}
PAGE_FILES = {'index.html', 'host.js'}


def resolve(rel, web_dir, vendor_dir, model_dir, core_path):
    """URL path after the token -> Path or None."""
    parts = [p for p in rel.split('/') if p]
    if not parts: return None
    if len(parts) == 1 and parts[0] in PAGE_FILES: return Path(web_dir) / parts[0]
    if len(parts) == 1 and parts[0] == 'core.js': return Path(core_path) if core_path else None
    if len(parts) == 2 and parts[0] == 'v':
        f = Path(vendor_dir) / parts[1]
        return f if f.suffix == '.js' and f.is_file() and parts[1] == f.name else None
    if parts[0] == 'm' and model_dir:
        root = Path(model_dir).resolve()
        f = (root / Path(*parts[1:])).resolve()
        return f if root in f.parents and f.is_file() else None
    return None


def make_server(web_dir, vendor_dir, model_dir=None, core_path=None, token=None):
    """-> (server, port, token). Caller runs server.serve_forever() in a thread."""
    token = token or secrets.token_urlsafe(12)
    conf = {'model_dir': model_dir, 'core_path': core_path}      # changeable at runtime: a warm renderer is re-pointed at another model

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            pre = '/%s/' % token
            f = resolve(path[len(pre):], web_dir, vendor_dir, conf['model_dir'], conf['core_path']) if path.startswith(pre) else None
            if f is None: self.send_error(404); return
            try: data = f.read_bytes()
            except OSError: self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', MIME.get(f.suffix.lower(), 'application/octet-stream'))
            self.send_header('Content-Length', str(len(data))); self.send_header('Cache-Control', 'no-store'); self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    srv.daemon_threads = True; srv.conf = conf
    return srv, srv.server_address[1], token
