"""Serve the UI study and allowlisted local icons on loopback; no app startup."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse

class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def allowed(self):
        path = self.path.split("?", 1)[0]
        allowed = {"/multi_diverse/UI/"+name for name in
                   ("index.html", "design.css", "remote-layout.css", "design.mjs", "model.mjs")}
        allowed |= {"/icon_asset/"+name for name in ("Codex_logo.png", "MonitorSolo_logo.png")}
        return path in allowed

    def do_GET(self):
        if not self.allowed():
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self):
        if not self.allowed():
            self.send_error(404)
            return
        super().do_HEAD()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18790)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(root)))
    print(f"http://127.0.0.1:{server.server_port}/multi_diverse/UI/index.html", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
