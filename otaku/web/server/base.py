"""What every part of the serving stands on: the hooks it reaches the
rest of the app through, the socket server with what a handler needs
behind it, and `Wire` — the handler methods that put bytes on the wire
and read them off it, which the guards, the streams and the dispatcher
all share. Nothing here decides what a path MEANS."""

import json
import ssl
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from otaku.web import api
from otaku.web.server.assets import JSON, NO_STORE
from otaku.web.thread import SessionRunner

# How a request ENDS when the reader closes a tab, reloads mid-reply or
# stops otaku while the page is still streaming — and, under TLS, how one
# never starts: a plain `http://` paste against this port, a scanner, any
# client with no cipher in common all raise SSLError out of the
# handshake. Every one of these is ordinary here, and none of them is a
# crash to report.
DISCONNECTED = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    TimeoutError,
    ssl.SSLError,
)

# The spellings of "this machine" — all of them reachable as `localhost`,
# which is what the printed address READS as (`console.banner.address`,
# this tuple's other reader and the reason it is public).
LOOPBACK = ("127.0.0.1", "0.0.0.0", "::", "::1", "localhost", "")

# The spellings of "every interface": a bind that is not this machine
# alone, and so not a name anything can be checked against.
_WILDCARD = ("0.0.0.0", "::", "")


@dataclass(frozen=True)
class Hooks:
    """What the serving reaches outside HTTP, injected at composition
    (`web.run.serve`) like every other frontend hook: where a request
    line worth showing goes, where a contained crash is recorded, the
    background worker's voice for the beat — its one-line status, and
    the sentences it has said since anybody last asked — and the ring a
    landed reply calls the reader back with. All are callable from any
    thread; nothing here prints conversation."""

    show: Callable[[str], None]
    record: Callable[[str, BaseException], str]
    working: Callable[[], str]
    sayings: Callable[[], list[str]]
    ring: Callable[[], None]


class Server(ThreadingHTTPServer):
    """The threading server plus what a handler needs: the runner that
    reaches the session, the state that spans requests, where the
    reader's own files live, and the hooks. Daemon threads because a
    held-open event stream must never outlive the interrupt that stopped
    the server."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        bound: tuple[str, int],
        handler: Any,
        *,
        runner: SessionRunner,
        pending: api.Pending,
        custom_web_dir: Path,
        hooks: Hooks,
        tls_context: ssl.SSLContext | None = None,
        password_hash: str | None = None,
    ) -> None:
        super().__init__(bound, handler)
        self.runner = runner
        self.pending = pending
        self.custom_web_dir = custom_web_dir
        self.hooks = hooks
        self.tls_context = tls_context
        self.password_hash = password_hash
        # The names this server answers to: the configured one, and every
        # spelling of the machine it runs on. A request addressed to
        # anything else did not come from a reader typing an address.
        #
        # Empty for a _WILDCARD bind, which means every name: a reader who
        # set `0.0.0.0` asked to be reachable from the network, where the
        # address is the machine's own and otaku cannot know it. There is
        # nothing left for a rebinding guard to protect there — the port
        # is already open to everyone who can route to it.
        named = bound[0].strip("[]")
        self.answers_to = (
            frozenset() if named in _WILDCARD else frozenset({named, *LOOPBACK} - {""})
        )
        # Set when the reader asks otaku to stop. Ctrl+C is the USER
        # SPEAKING, not a crash: raising it where it lands would tear
        # open whatever the session thread is doing — most likely a
        # reply, mid-sentence, inside the provider's own generator,
        # where the backend's cancel-and-keep can no longer run and the
        # words already on the reader's screen are lost. So the FIRST
        # one asks: the stream closes itself at the next frame, the
        # partial is recorded exactly as when a browser goes away, and
        # the loop ends after the turn it was in.
        #
        # The first one also hands SIGINT back to Python, so the SECOND
        # is fatal in the ordinary way. A request can only be noticed
        # between events, and a provider that has not reached its first
        # token yields none — without the escalation, pressing it again
        # would do nothing and the only way out of a wedged engine would
        # be `kill`.
        self.stopping = threading.Event()

    def get_request(self) -> tuple[Any, Any]:
        """The accepted connection, wrapped when this server serves TLS —
        and NOT handshaken here. This runs on the accept loop, where one
        client that opens a connection and then says nothing would hold
        every other connection out; the handshake is a conversation, so
        it belongs on the thread this connection is about to get."""
        conn, addr = super().get_request()
        if self.tls_context is None:
            return conn, addr
        return self.tls_context.wrap_socket(
            conn, server_side=True, do_handshake_on_connect=False
        ), addr

    def finish_request(self, request: Any, client_address: Any) -> None:
        """The handshake, on this connection's own thread, before the
        handler reads a byte of it. What it raises is SSLError, which is
        the answer to a plain `http://` paste against this port as much
        as to a scanner — ordinary, and quiet by `DISCONNECTED`."""
        if self.tls_context is not None:
            request.do_handshake()
        super().finish_request(request, client_address)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """What escaped a handler. The stdlib prints a traceback per
        connection to stderr — three screens of socketserver frames,
        under a banner trying to stay six lines tall, for something that
        is usually not a fault at all: a closed tab, a reload mid-reply,
        a Ctrl+C while the page was streaming. Those are the NORMAL end
        of a request here. Anything else is a real bug, and goes where
        every contained crash goes."""
        error = sys.exc_info()[1]
        if error is None or isinstance(error, DISCONNECTED):
            return
        self.crashed("web request", error)

    def crashed(self, context: str, exc: BaseException) -> str:
        """One contained crash into the day's error log, and one line
        where the reader is. Returns the log's pretty path — "" if even
        the logging failed, which is not a reason to raise again."""
        path = self.hooks.record(context, exc)
        self.hooks.show(f"{context} — {type(exc).__name__}, in {path}" if path else context)
        return path


class Wire(BaseHTTPRequestHandler):
    """The handler's wire half: what is read off a request — its path,
    its query, its body — and how an answer goes out: a JSON payload, a
    body of bytes with its type and cache policy, a server-sent event, a
    failure written for a developer. The mixins and the dispatcher are
    built on it; it decides nothing about any path."""

    server: Server  # narrowed from BaseServer, so the fields above are visible
    # Whether a reply's headers have gone out: after them there is no
    # status left to send, and a failure can only be logged.
    _streaming = False
    server_version = "otaku"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def _path(self) -> str:
        """What was asked for, without the query. Read defensively: a
        request line malformed enough to be refused before it is parsed
        never set one."""
        return str(getattr(self, "path", "")).split("?", 1)[0]

    def _asked(self) -> str:
        """The request as one phrase, for the line the terminal shows —
        or "?" for a request line that never parsed into either half.
        The raw line is deliberately NOT shown: it is bytes off a
        socket, and this ends up in a terminal."""
        return f"{self.command} {self._path()}".strip() if self.command else "?"

    def _failed(self, status: int, why: Exception) -> None:
        """A failure as a BODY, never as the status line. The reason is a
        story title, a character name, a provider's own error text — and
        the status line is latin-1, so putting it there answers a reader
        writing in Cyrillic or Japanese with a dropped connection and
        nothing else.

        Written for a DEVELOPER, not for the reader: the page logs this
        to the console and shows a sentence of its own (`api.js`),
        because a fault has no wording anybody chose. `refused` is
        absent, which is what tells the two apart — a refusal is an
        answer and comes back 200."""
        self._json({"notice": f"{type(why).__name__}: {why}"}, status=status)

    def _query(self) -> dict[str, str]:
        """The request's query, one value per name — a read's argument
        is a value, never a list."""
        parsed = parse_qs(urlsplit(self.path).query)
        return {name: values[0] for name, values in parsed.items() if values}

    def _body(self) -> dict[str, Any]:
        """The request's JSON object, or an empty one — a malformed body
        is a missing field, which every caller already handles. That
        includes a malformed LENGTH: anything that scans this port can
        send one, and a crash here would answer nothing and file a
        traceback in the day's error log for something that is not a
        bug."""
        try:
            # Never negative: `read(-1)` reads to EOF, which for a
            # connection a browser holds open is forever — one held
            # thread and socket per request, answering nothing.
            length = max(0, int(self.headers.get("Content-Length", 0) or 0))
            parsed = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _json(
        self,
        payload: object,
        *,
        status: int = 200,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._send(json.dumps(payload).encode(), JSON, NO_STORE, status=status, headers=headers)

    def _frame(self, data: str | None, *, retry: int | None = None) -> None:
        """One server-sent event, flushed — a stream nobody flushes is a
        stream nobody sees."""
        head = f"retry: {retry}\n" if retry is not None else ""
        body = f"data: {data}\n" if data is not None else ""
        self.wfile.write(f"{head}{body}\n".encode())
        self.wfile.flush()

    def _ping(self) -> None:
        """A comment frame, which the browser ignores. It is here for the
        WRITE: a stream that only writes when a file changes never finds
        out that its reader closed the tab, and would hold this thread
        and its socket for the life of the process — one of each per
        reload. A file changes rarely; a tab is closed constantly."""
        self.wfile.write(b": \n\n")
        self.wfile.flush()

    def _send(
        self,
        body: bytes,
        content_type: str,
        cache: str,
        *,
        status: int = 200,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
