"""Optional loopback-only preview. Double-clicking index.html also works."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    server = ThreadingHTTPServer(("127.0.0.1", 18826), partial(SimpleHTTPRequestHandler, directory=str(root)))
    print("Design preview: http://127.0.0.1:18826/index.html", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
