# Канал отладки vastgame-desktop (только если задан VASTGAME_DEBUG_TOKEN): POST {token, cmd, timeout} на порт 8788.
# Порт не опубликован наружу — доступен только через Tailscale. Для починки ошибок в экспериментальном режиме.
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.environ.get("VASTGAME_DEBUG_TOKEN", "")


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if not TOKEN or req.get("token") != TOKEN:
            self.send_response(403)
            self.end_headers()
            return
        try:
            r = subprocess.run(["bash", "-c", req.get("cmd", "")], capture_output=True, text=True,
                               timeout=int(req.get("timeout") or 120), env={**os.environ, "DISPLAY": ":0"})
            out = {"code": r.returncode, "out": (r.stdout + r.stderr)[-20000:]}
        except subprocess.TimeoutExpired:
            out = {"code": None, "out": "timeout"}
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("0.0.0.0", 8788), H).serve_forever()
