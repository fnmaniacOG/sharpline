#!/usr/bin/env python3
"""
serve.py - SharpLine control panel.

Serves the dashboard AND lets the Start/Stop button on the page launch or kill the agent,
so there is no CLI dance. Run this one command, open the page, click Start.

    python3 serve.py
    # then open http://localhost:8000/dashboard/

Endpoints (used by the dashboard button):
    GET  /agent/status  -> {"running": bool}
    POST /agent/start   -> starts run_all.py (optional ?weight=0.4)
    POST /agent/stop    -> stops it
"""
import functools
import http.server
import json
import os
import socketserver
import sys
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))
_proc = None


def running() -> bool:
    return _proc is not None and _proc.poll() is None


def start(weight: str = "0.30") -> None:
    global _proc
    if running():
        return
    import subprocess
    try:
        os.remove(os.path.join(ROOT, "dashboard", "state.json"))   # fresh run resets the view
    except OSError:
        pass
    _proc = subprocess.Popen(
        [sys.executable, "agent/run_all.py",
         "--dashboard", "dashboard/state.json",
         "--log", "agent/decisions.jsonl",
         "--calibrate", "--model-weight", str(weight)],
        cwd=ROOT)


def stop() -> None:
    global _proc
    if running():
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
    _proc = None


class Handler(http.server.SimpleHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        p = urlparse(self.path)
        if p.path == "/agent/start":
            weight = parse_qs(p.query).get("weight", ["0.30"])[0]
            start(weight)
            self._json({"running": running()})
        elif p.path == "/agent/stop":
            stop()
            self._json({"running": running()})
        else:
            self.send_error(404)

    def do_GET(self):
        if urlparse(self.path).path == "/agent/status":
            self._json({"running": running()})
            return
        super().do_GET()

    def log_message(self, *a):
        pass   # quiet


def main():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    handler = functools.partial(Handler, directory=ROOT)
    with socketserver.ThreadingTCPServer(("127.0.0.1", PORT), handler) as httpd:
        print(f"SharpLine control panel:  http://localhost:{PORT}/dashboard/")
        print("Open that URL, then click Start agent. Ctrl+C here to shut down.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop()


if __name__ == "__main__":
    main()
