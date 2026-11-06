"""A web site of the test's own, on this machine: files served at the
paths the test names, any other path answering 404 — what a card file's
own link points at, without the network."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Site:
    """The site, served from its own thread until closed."""

    def __init__(self) -> None:
        files: dict[str, tuple[str, bytes]] = {}
        self._files = files

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                found = files.get(self.path)
                if found is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                kind, data = found
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: object) -> None:
                pass  # the test's output is the test's

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def serve(self, path: str, data: bytes, content_type: str) -> str:
        """`data` served at `path` as `content_type`; its address."""
        self._files[path] = (content_type, data)
        return self.url(path)

    def url(self, path: str) -> str:
        """The address of `path` on the site, served or not."""
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}{path}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@contextmanager
def serving() -> Iterator[Site]:
    """A site for the length of the block."""
    site = Site()
    try:
        yield site
    finally:
        site.close()
