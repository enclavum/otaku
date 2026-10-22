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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from otaku.web import api
from otaku.web.api import Api, App, HeadMoved, Invalid, NotFound, Refused, Unavailable
from otaku.web.server.assets import JSON, NO_STORE, PROBLEM

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

# What a request body may weigh. A turn's pictures are the heaviest thing
# the page sends — eight of 10 MB each at most (the play's
# `_MAX_PICTURES` and `backend.app.pictures.services.MAX_BYTES`, copied),
# base64 in the JSON — and nothing else comes close.
MAX_BODY = 8 * 10 * 1024 * 1024 * 4 // 3 + 1024 * 1024

# When to ask again after a 503: otaku is stopping, and a reader's Ctrl+R
# brings it back within a second or two.
RETRY_AFTER = "5"

# How often the serving loop polls for a shutdown request (`serve_forever`'s
# poll interval): the stdlib's half second is a pause the reader sees at
# `/set serve off` and on the way out.
_SHUTDOWN_POLL_INTERVAL = 0.05


class BodyError(Exception):
    """A request body this server will not read: too large (413), not
    JSON by its type (415), not JSON by its bytes or not an object (400).
    `status` is the answer; the message is what a developer reads."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status


@dataclass(frozen=True)
class Hooks:
    """What the serving reaches outside HTTP, injected at composition
    (`web.run.serve`) like every other frontend hook: where a request
    line worth showing goes, where a contained crash is recorded, the
    background worker's one-line status for the beat, the ring a landed
    reply calls the reader back with, and the sign-in's password check
    (`api.settings.check_password`, Refused for a wrong one). All are
    callable from any thread; nothing here prints conversation."""

    show: Callable[[str], None]
    record: Callable[[str, BaseException], str]
    working: Callable[[], str]
    ring: Callable[[], None]
    check_password: Callable[[str], None]


class Server(ThreadingHTTPServer):
    """The threading server plus what a handler needs: the app and the
    interface over it, where the reader's own files live, and the hooks —
    and where it is (`address`), as the banner says it, and its `stop`.
    Daemon threads because a held-open event stream must never outlive the
    interrupt that stopped the server."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        bound: tuple[str, int],
        handler: Any,
        *,
        app: App,
        address: str,
        custom_web_dir: Path,
        hooks: Hooks,
        tls_context: ssl.SSLContext | None = None,
        password_hash: str | None = None,
    ) -> None:
        super().__init__(bound, handler)
        self.app = app
        self.api = Api(app)
        self.address = address
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
        # open whatever the app thread is doing — most likely a
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

    def serve_forever(self, poll_interval: float = _SHUTDOWN_POLL_INTERVAL) -> None:
        """Serve until stopped, looking for the stop every `poll_interval`
        — the longest a stop waits."""
        super().serve_forever(poll_interval)

    def stop(self) -> None:
        """Stop serving: a reply a browser is reading ends where it stands,
        kept, as at the first Ctrl+C, and the socket closes."""
        self.stopping.set()
        self.shutdown()
        self.server_close()

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

    def _problem(
        self,
        status: int,
        detail: str | None = None,
        *,
        headers: tuple[tuple[str, str], ...] = (),
        extra: dict[str, object] | None = None,
    ) -> None:
        """An answer that is not a payload, as RFC 9457 problem details:
        the status, its phrase as `title`, `detail` where there is one to
        say, and `extra`'s members beside them. In the BODY, never the
        status line: a detail carries a story title, a character name, a
        provider's own error text, and the status line is latin-1 — a
        reader writing in Cyrillic or Japanese would get a dropped
        connection and nothing else."""
        problem: dict[str, object] = {
            "type": "about:blank",
            "title": HTTPStatus(status).phrase,
            "status": status,
        }
        if detail is not None:
            problem["detail"] = detail
        problem.update(extra or {})
        self._send(json.dumps(problem).encode(), PROBLEM, NO_STORE, status=status, headers=headers)

    def _outcome(self, refused: Refused) -> None:
        """What the backend declined, as the status that says why, its own
        sentence the `detail` the page shows: a subject that is not there
        (404); input it cannot take (422); a service it depends on that
        failed it (502); a state that rules it out (409); a head that
        moved from the one `If-Match` named (412), saying where it stands
        now — `head`, and as the `ETag`. An ANSWER, not a fault: the
        connection stays open."""
        if isinstance(refused, HeadMoved):
            self._problem(
                412,
                str(refused),
                extra={"head": refused.head},
                headers=(("ETag", api.etag(refused.head)),),
            )
            return
        self._problem(_status_of(refused), str(refused))

    def _fault(
        self,
        status: int,
        why: Exception | None = None,
        *,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """A request this server could not answer as asked — malformed,
        unmatched, too large, from the wrong place, a bug — as a problem
        body, `why` in a developer's words: the page logs it, because a
        fault has no wording anybody chose and a reader can do nothing
        with it. The connection is closed, since what is left on it
        cannot be trusted."""
        self.close_connection = True
        detail = f"{type(why).__name__}: {why}" if why is not None else None
        self._problem(status, detail, headers=(*headers, ("Connection", "close")))

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """The stdlib's own refusals — a request line it could not parse,
        a method it has no handler for — as problem bodies too, never its
        HTML page."""
        self.log_error("code %d, message %s", code, message)
        self._fault(code)

    def _query(self) -> dict[str, str]:
        """The request's query, one value per name — a read's argument
        is a value, never a list."""
        parsed = parse_qs(urlsplit(self.path).query)
        return {name: values[0] for name, values in parsed.items() if values}

    def _body(self) -> dict[str, Any]:
        """The request's JSON object — {} where it carries no body — or a
        `BodyError`: a length that is no number (400), one over
        `MAX_BODY` (413, left unread), a body that is not
        `application/json` by its type (415) or by its bytes, or no
        object (400). Anything that scans this port can send any of
        these, and none is a crash to file."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raise BodyError(400, "the Content-Length is not a number") from None
        # Never read with a negative length: `read(-1)` reads to EOF, which
        # for a connection a browser holds open is forever.
        if length < 0:
            raise BodyError(400, "the Content-Length is negative")
        if length > MAX_BODY:
            raise BodyError(413, f"{length} bytes is over the {MAX_BODY}-byte limit")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        kind = self.headers.get_content_type()
        if kind != "application/json":
            raise BodyError(415, f"{kind} is not application/json")
        try:
            parsed = json.loads(raw)
        except ValueError as e:
            raise BodyError(400, f"the body is not JSON: {e}") from None
        if not isinstance(parsed, dict):
            raise BodyError(400, "the body is not a JSON object")
        return parsed

    def _read_body(self) -> dict[str, Any] | None:
        """The request's body, or None once its fault is answered."""
        try:
            return self._body()
        except BodyError as e:
            self._fault(e.status, e)
            return None

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


def _status_of(refused: Refused) -> int:
    """The status a refusal answers with, by its kind: what a caller tells
    apart is what the status says (`backend.errors`). A refusal of no
    kind is the state ruling the request out."""
    if isinstance(refused, NotFound):
        return 404
    if isinstance(refused, Invalid):
        return 422
    if isinstance(refused, Unavailable):
        return 502
    return 409
