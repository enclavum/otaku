"""The local server: HTTP, and nothing else — bind, route, frame, reply.

One user on loopback, traffic close to zero — so the stdlib's threading
server is the whole of it, and every asset is read from disk per request
rather than held in memory, which is what makes an edit visible without
a restart. `/api/watch` is the other half of that promise: the page
holds it open and reloads itself when a file it is made of changes, so
an edit to the packaged page or to the reader's own `custom.css` shows
up without a restart and without a reload anybody has to remember.

What a request MEANS is `web.api`'s: a handler looks a path up — in the
asset table below, or in `api`'s own tables — and carries the result.
The thread that owns the session is `web.thread`; everything above HTTP
(the banner, the tail, Ctrl+C, the wiring of all three) is `web.run`,
which is also the only place anything is printed: the serving is handed
one line per request worth showing (`Hooks`) and knows nothing about
where it appears. What the stdlib would have put in the terminal on its
own — an access line per font, a traceback per closed tab — is answered
instead, in `_Handler.log_request` and `_Server.handle_error`.

What may be served is a CLOSED table: a request path is looked up, never
joined onto a directory, so no request can compose its way to
`configs/providers.toml`.

The API is one table of methods and paths (`api.ROUTES`), and the METHOD
is the lane. A GET only reads the session and is answered on the read
queue — during a reply as well as between them, which is what keeps
every screen answerable while the model talks. Every other method moves
the story and takes the one thread in turn, in the order it arrived.

The document, the stylesheet and the scripts are `no-store` — they are
small, they are local, and a stale one costs more than the bytes ever
will; the fonts are immutable and long-lived, which is why they carry
version numbers in their names instead.

`custom.css` is the one asset that is NOT packaged: it comes from the
state dir (`web/custom.css`), is absent by default, and is loaded last so
that anything in it wins. The token contract it writes against is
`docs/web_tokens.md`, and the HTTP surface is `otaku/web/api.yaml`.
"""

import contextlib
import http.cookies
import json
import re
import ssl
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from otaku.backend import WebSettings, passwords
from otaku.backend.api.play import PlayEvent
from otaku.backend.session import Refused, Session
from otaku.web import api, auth
from otaku.web.thread import SessionRunner, StoppingError

__all__ = ["LOOPBACK", "Hooks", "bind"]


# The packaged assets, read per request — and the same directory as a
# plain path, which is what a watcher can stat: a Traversable promises no
# mtime.
_STATIC = files("otaku.web") / "static"
_STATIC_PATH = Path(__file__).parent / "static"

_HTML = "text/html; charset=utf-8"
_CSS = "text/css; charset=utf-8"
_JS = "text/javascript; charset=utf-8"
_EVENT_STREAM = "text/event-stream"
_JSON = "application/json; charset=utf-8"
_WOFF2 = "font/woff2"
_MANIFEST = "application/manifest+json"

# The lane is the METHOD. A GET only READS the session and is answered on
# the read queue — during a reply as well as between them, which is what
# keeps every screen answerable while the model talks; every other method
# moves the story and waits its turn. HTTP already draws that line, so a
# handler cannot be filed under the wrong one by accident.
_READING = "GET"

# The path parameters that are ROW IDS. They match digits alone, so a
# page that lost its story cannot address `/api/stories/null/…`: no route
# matches, the answer is a plain 404, and no handler is ever handed a
# number that is not one. Every other parameter is a NAME — a provider, a
# model, a card token — and names may be anything a segment can hold.
_NUMERIC = ("story", "message", "scene", "character", "record")


def _pattern(template: str) -> "re.Pattern[str]":
    """One path template as a regex — declared here, above the constant
    it builds, because that constant is the only thing that needs it."""

    def segment(found: "re.Match[str]") -> str:
        name = found.group(1)
        return f"(?P<{name}>" + (r"\d+" if name in _NUMERIC else "[^/]+") + ")"

    return re.compile("^" + re.sub(r"\{(\w+)}", segment, template) + "$")


# Both API tables are keyed by template. One regex per template, compiled
# once: `{name}` matches a single segment and reaches the handler as
# `ask.params[name]`. Sorted on the TEMPLATE — fewest parameters first,
# then longest — so a literal segment is never eaten by a parameter that
# could also match it (a compiled `{name}` is LONGER than most literals,
# so pattern length would order them exactly backwards). A flow keeps its
# own mark, because it is the one kind of row that also needs `Pending`.
_Row = tuple[str, "re.Pattern[str]", Any, bool]

_ROUTES: tuple[_Row, ...] = tuple(
    (method, _pattern(template), call, flow)
    for method, template, call, flow in sorted(
        (
            (method, template, call, flow)
            for table, flow in ((api.ROUTES, False), (api.FLOWS, True))
            for (method, template), call in table.items()
        ),
        key=lambda row: (row[1].count("{"), -len(row[1])),
    )
)

# The extraction poll: a GET the server answers itself, so it is not a
# row in either table and needs its own pattern — the story captured,
# because the pending runs are kept per story.
_EXTRACTION = re.compile(r"^/api/stories/(?P<story>\d+)/extraction$")

# A local page is never worth a stale byte; the fonts change about once
# per Plex release and their names change with them.
_NO_STORE = "no-store, no-cache, must-revalidate, max-age=0"
_IMMUTABLE = "public, max-age=31536000, immutable"

# Where the reader's own typefaces are asked for, and what may be one.
# Under a prefix of its own rather than `/fonts/`, so a name of theirs can
# never shadow one of ours — the packaged table is looked up first, and a
# reader debugging their own stylesheet should not have to know that.
_CUSTOM_FONTS = "/web-fonts/"
_FONT_SUFFIXES = {".woff2": _WOFF2, ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}


def _packaged(*where: str, suffix: str) -> tuple[str, ...]:
    """Every packaged file of one kind, listed from the directory it
    lives in — sorted, so the table is built the same way twice.

    Read at import, which is what makes the table below CLOSED: its keys
    are exactly the files that shipped, and a request path is looked up
    in it rather than joined onto a directory. Listing rather than typing
    them out is only about who keeps the inventory: adding a script or a
    font is then one file, not one file and one row. A file added while
    otaku is running needs a restart to be served — editing one does
    not, because the bytes are read per request."""
    directory = _STATIC_PATH.joinpath(*where)
    prefix = "".join(f"{part}/" for part in where)
    if not directory.is_dir():
        return ()
    return tuple(
        sorted(f"{prefix}{found.name}" for found in directory.iterdir() if found.suffix == suffix)
    )


# The page's own modules and its typefaces, both taken from the package.
_SCRIPTS = ("app.js", *_packaged("js", suffix=".js"))
_FONTS = _packaged("fonts", suffix=".woff2")

# request path -> (packaged file, content type, cache policy). A path is
# LOOKED UP here and never joined onto a directory, which is what keeps a
# request from composing its way to `configs/providers.toml`. That is a
# property of the lookup, not of who wrote the list — so the two families
# that grow are listed from disk above.
_ASSETS: dict[str, tuple[str, str, str]] = {
    "/": ("index.html", _HTML, _NO_STORE),
    "/app.css": ("app.css", _CSS, _NO_STORE),
    # what a home screen reads to open the page as an app of its own
    "/manifest.webmanifest": ("manifest.webmanifest", _MANIFEST, _NO_STORE),
    **{f"/{name}": (name, _JS, _NO_STORE) for name in _SCRIPTS},
    **{f"/{name}": (name, _WOFF2, _IMMUTABLE) for name in _FONTS},
}

# What the backend is doing, as the page asks every few seconds. Neither
# lane, because it never takes the session's THREAD: the handler answers
# it itself, from what can be read from anywhere. That it is answered at
# all is how the page knows otaku is there — a session busy with a reply
# is still a running otaku.
_STATUS = "/api/status"

# The other request the page makes that nobody asked for: held open for
# as long as the tab is, and answered a filename at a time as the files
# the page is made of change.
_WATCH = "/api/watch"

# What is never worth a line in the terminal: a page load is the
# document and the twenty files that came with it, and only the document
# is news — and neither is the page's own housekeeping, which is a beat
# every few seconds and one stream that never ends.
_QUIET = (set(_ASSETS) | {"/custom.css", _STATUS, _WATCH}) - {"/"}

# Signing in, as one address with three methods: GET says whether a
# password is asked for and whether this browser is through it, POST takes
# the password and sets the cookie, DELETE takes the cookie away. Answered
# without a credential, since the first two are how one is got and the
# third has nothing to protect.
_LOGIN = "/api/login"

# What is answered without a credential at all, and why each is: the
# packaged page and the reader's own files carry nothing of anybody's, and
# the page has to LOAD before it can ask for a password; the watch stream
# names only files the page is made of, and a refusal would end an
# EventSource for good rather than let it retry once the reader is in.
# `/api/status` is deliberately not here — it says what the background
# worker is doing, which is the session's.
_OPEN = set(_ASSETS) | {"/custom.css", _WATCH, _LOGIN}

# How a request ENDS when the reader closes a tab, reloads mid-reply or
# stops otaku while the page is still streaming — and, under TLS, how one
# never starts: a plain `http://` paste against this port, a scanner, any
# client with no cipher in common all raise SSLError out of the
# handshake. Every one of these is ordinary here, and none of them is a
# crash to report.
_DISCONNECTED = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    TimeoutError,
    ssl.SSLError,
)

# The spellings of "this machine" — all of them reachable as `localhost`,
# which is what the printed address READS as (`web.run.address`, this
# tuple's other reader and the reason it is public).
LOOPBACK = ("127.0.0.1", "0.0.0.0", "::", "::1", "localhost", "")

# The spellings of "every interface": a bind that is not this machine
# alone, and so not a name anything can be checked against.
_WILDCARD = ("0.0.0.0", "::", "")

# The watch: how long a browser waits before reopening the stream (the
# SSE `retry` field), and how often the files behind it are looked at.
# A second is under the time it takes to alt-tab back to the browser,
# and a stat of thirty files costs nothing next to that.
_RETRY_MS = 2000
_WATCH_INTERVAL = 1.0


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


def bind(
    config: WebSettings,
    runner: SessionRunner,
    pending: api.Pending,
    custom_web_dir: Path,
    hooks: Hooks,
    tls_context: ssl.SSLContext | None = None,
    password_hash: str | None = None,
) -> "_Server":
    """The socket, and everything a handler needs behind it. What it
    raises is the socket's own OSError — turning the address that could
    not be honoured into a sentence is `web.run`'s, which is also why
    this is a separate function: the caller catches exactly the
    binding.

    `tls_context` is what every accepted connection is wrapped in, and None is
    the plain server. Whether there is one at all is the configuration's
    (`[web] https`), and finding a certificate to build it from happened
    before this was called — nothing here can fail for want of one.

    `password_hash` is the hash of `[web] password` the launch made —
    never the typed password (`backend.passwords`). None is the default
    configuration, where nobody is asked for anything."""
    return _Server(
        (config.host, config.port),
        _Handler,
        runner=runner,
        pending=pending,
        custom_web_dir=custom_web_dir,
        hooks=hooks,
        tls_context=tls_context,
        password_hash=password_hash,
    )


class _Server(ThreadingHTTPServer):
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
        # Empty for a WILDCARD bind, which means every name: a reader who
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
        as to a scanner — ordinary, and quiet by `_DISCONNECTED`."""
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
        if error is None or isinstance(error, _DISCONNECTED):
            return
        self.crashed("web request", error)

    def crashed(self, context: str, exc: BaseException) -> str:
        """One contained crash into the day's error log, and one line
        where the reader is. Returns the log's pretty path — "" if even
        the logging failed, which is not a reason to raise again."""
        path = self.hooks.record(context, exc)
        self.hooks.show(f"{context} — {type(exc).__name__}, in {path}" if path else context)
        return path


class _Handler(BaseHTTPRequestHandler):
    server: _Server  # narrowed from BaseServer, so the fields above are visible
    # Whether a reply's headers have gone out: after them there is no
    # status left to send, and a failure can only be logged.
    _streaming = False
    server_version = "otaku"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # ---------- routing ----------

    def do_GET(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        if path == _LOGIN:
            self._signed_in()
        elif path == _STATUS:
            self._status()
        elif path == _WATCH:
            self._watch_stream()
        elif (extraction := _EXTRACTION.match(path)) is not None:
            # The one read that never queues: the answer may be "the pass
            # that owns the session's thread is still running", and a
            # run's `poll` is channel-safe by contract, so this thread
            # answers it — the path's own story's run, no other's.
            run = self.server.pending.extraction.get(int(extraction.group("story")))
            self._json({"report": run.poll() if run is not None else None})
        elif path.startswith("/api/"):
            self._route()
        elif path in _ASSETS:
            name, content_type, cache = _ASSETS[path]
            self._send(self._asset(name), content_type, cache)
        elif path == "/custom.css":
            self._send(self._custom_css(), _CSS, _NO_STORE)
        elif path.startswith(_CUSTOM_FONTS):
            # Decoded like every path parameter (`_match`): the name on
            # disk is what iterdir lists, and "My%20Font.woff2" is not it.
            self._own_font(unquote(path.removeprefix(_CUSTOM_FONTS)))
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        body = self._body()
        if path == _LOGIN:
            self._sign_in(body)
        # The two that answer with a STREAM rather than a payload, so
        # neither can be a row in the table.
        elif path == "/api/play":
            self._play(str(body.get("line", "")))
        elif path == "/api/play/last":
            self._play("", regenerate=True)
        else:
            self._route(body)

    def do_PUT(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        self._route(self._body())

    def do_PATCH(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        self._route(self._body())

    def do_DELETE(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        # Read whether it is needed or not: a body left in the socket is
        # the first bytes of the NEXT request on a kept-alive connection,
        # which then arrives as a method called `{}GET`.
        body = self._body()
        if path == _LOGIN:
            self._sign_out()
        else:
            self._route(body)

    def _admitted(self, path: str) -> bool:
        """Whether this request may be answered at all — the same guards,
        in the same order, for every method, each of them answering its
        own refusal. What differs between a read and a write is decided
        INSIDE the guard it concerns (`_from_our_page`), not by which
        methods call which guards. The login is a request like any other
        here — a page of another origin must not get to guess at the
        password — and is let through only by `_authorized`."""
        return (
            self._still_serving()
            and self._from_this_machine()
            and self._from_our_page()
            and self._authorized(path)
        )

    def _still_serving(self) -> bool:
        """Whether this server is still the one to ask. A stopped server
        closes its LISTENING socket, but a page's pooled keep-alive
        connections outlive that — its heartbeat would go on being
        answered 200 by handler threads of a server that is gone, and
        the page would never learn it (sharpest after `/web` hands the
        session back to the chat, where the process lives on). Answered
        503 with the connection closed, so the page's next attempt has
        to reconnect — which is what fails, and what tells it."""
        if not self.server.stopping.is_set():
            return True
        self.close_connection = True
        self.send_error(503, "otaku is stopping")
        return False

    def _route(self, body: dict[str, Any] | None = None) -> None:
        """One request against the API tables: the first template this
        method and path match wins, and what the path captured reaches
        the handler as its `params`. The METHOD decides the lane, which
        is what lets a GET be answered while a reply streams."""
        work = self._match(body or {})
        if work is None:
            self.send_error(404)
            return
        self._answer(work, reading=self.command == _READING)

    def _match(self, body: dict[str, Any]) -> Callable[[Session], Any] | None:
        """The work this request names, ready to run on the session's
        thread — a flow taking the cross-request state as well, which is
        the only way the two kinds differ once matched."""
        path = self._path()
        for method, pattern, call, flow in _ROUTES:
            found = pattern.match(path) if method == self.command else None
            if found is None:
                continue
            named = {name: unquote(value) for name, value in found.groupdict().items()}
            ask = api.Ask(params=named, query=self._query(), body=body)
            return self._work(call, ask, flow=flow)
        return None

    def _work(self, call: Any, ask: api.Ask, *, flow: bool) -> Callable[[Session], Any]:
        """One matched row as the single-argument job the runner takes —
        a flow given the cross-request state it declared it needs."""
        if flow:
            return lambda session: call(session, ask, self.server.pending)
        return lambda session: call(session, ask)

    def _status(self) -> None:
        """What the backend is doing and has to say, told without asking
        for the session's thread: its one-line status, and anything it
        has said since the page last asked.
        Reaching this at all is the first answer — an otaku deep in a
        reply is still a running otaku — and the other two ride along
        because a reader with the page open should learn that a pass ran
        the same way a terminal does, rather than finding the lore there
        later."""
        self._json({"status": self.server.hooks.working(), "notices": self.server.hooks.sayings()})

    def _signed_in(self) -> None:
        """What a page needs before it draws anything: whether this otaku
        asks for a password at all, and whether this browser is already
        through it. The page cannot tell the second for itself — the
        cookie is `HttpOnly`, which is the point of it."""
        password_hash = self.server.password_hash
        self._json(
            {
                "required": password_hash is not None,
                "signed_in": password_hash is not None
                and auth.verify_token(self._cookie_token(), password_hash),
            }
        )

    def _sign_in(self, body: dict[str, Any]) -> None:
        """A password in, a cookie out — the only way to get one.

        Answered on THIS thread and in neither lane: it never touches the
        session, and the derivation below would otherwise queue behind a
        reply and stop the story for as long as it takes.

        The typed password is checked against the stored hash, never
        against a password: there is none in the file to compare with. That
        check is slow and memory-hard by design, and its cost is the whole
        of the rate limiting — a couple of dozen guesses a second, and no
        state to keep to do it.

        A wrong password is an authentication failure and answers 401, with
        the sentence in the body. Signing in where no password is set is
        not: nothing is wrong with the caller, so that stays a refusal."""
        password_hash = self.server.password_hash
        if password_hash is None:
            self._json({"notice": "This otaku asks for no password.", "refused": True})
            return
        given = str(body.get("password", ""))
        if not passwords.check(given, password_hash):
            self._json({"notice": "Check the password and try again.", "refused": True}, status=401)
            return
        # The cookie lasts as long as the token in it, so the browser never
        # holds one the server would refuse.
        lifetime = auth.LONG_LIFETIME if body.get("remember") is True else auth.SHORT_LIFETIME
        self._set_cookie(auth.generate_token(password_hash, lifetime), lifetime)

    def _sign_out(self) -> None:
        """The cookie taken away. Only the server can: the page is not
        allowed to see it, let alone clear it. A stateless token copied
        elsewhere stays good until it runs out — this is about what the
        browser keeps offering, not about the credential."""
        self._set_cookie("", 0)

    def _set_cookie(self, value: str, age: int) -> None:
        """The answer to a sign-in or a sign-out: `{}`, carrying the one
        `Set-Cookie` this server ever sends, set and cleared alike — the
        attributes must match for a clearing to reach the cookie it means.

        Named for the PORT, because a browser scopes cookies by host and
        not by port: two otakus on one machine would otherwise take each
        other's. `Secure` only under TLS, since a browser drops a secure
        cookie that arrives over plain http. An age of 0 clears it."""
        parts = [f"{_cookie_name(self.server)}={value}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if self.server.tls_context is not None:
            parts.append("Secure")
        parts.append(f"Max-Age={age}")
        self._json({}, headers=(("Set-Cookie", "; ".join(parts)),))

    # ---------- who is asking ----------

    def _from_this_machine(self) -> bool:
        """Whether the request came to an address this server answers to.

        The reader's own browser sends the address they typed; a page
        that got here by pointing its own hostname at 127.0.0.1 — DNS
        rebinding, the one attack a loopback bind does not stop — sends
        that hostname instead. Checking it costs nothing and is the only
        thing standing between a story library and any tab in the
        browser. A request with no Host at all is HTTP/1.0 or a script:
        allowed, because neither is a page."""
        answers_to = self.server.answers_to
        host = self.headers.get("Host", "")
        if not answers_to or not host or _named(host) in answers_to:
            return True
        self.send_error(421, "Misdirected Request")
        return False

    def _authorized(self, path: str) -> bool:
        """Whether whoever is asking has shown they know the password.

        True for everyone when none is set, which is the default and the
        whole of what a loopback otaku ever needed. `_OPEN` says what is
        answered anyway, and the reader's own typefaces go with their
        stylesheet — both are files they put there themselves.

        401 with NO `WWW-Authenticate`: send one and the browser opens
        its own credential dialog over the page, which has a login of its
        own and a name for what it is asking for."""
        password_hash = self.server.password_hash
        if password_hash is None or path in _OPEN or path.startswith(_CUSTOM_FONTS):
            return True
        if auth.verify_token(self._cookie_token(), password_hash):
            return True
        self.send_error(401, "Unauthorized")
        return False

    def _cookie_token(self) -> str:
        """The token off this server's cookie — "" when there is none, or
        when the header does not parse, which `auth.verify_token` refuses like
        any other non-token. Anything that can reach the port can send a
        Cookie header, so a malformed one is an ordinary refusal and not
        a crash."""
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return ""
        morsel = jar.get(_cookie_name(self.server))
        return morsel.value if morsel is not None else ""

    def _from_our_page(self) -> bool:
        """Whether a WRITE came from otaku's own page.

        A cross-origin form can POST here without a preflight, and a
        write needs no answer to do its damage: repointing a provider at
        an attacker sends the reader's api key with the next turn. So a
        write must prove where it came from — `Sec-Fetch-Site:
        same-origin`, which no page can forge, or an `Origin` that is
        exactly the one this request was addressed to.

        The Origin arm carries the weight, because fetch metadata is
        only sent for a TRUSTWORTHY url: `http://localhost` is one,
        `http://192.168.1.5:9600` is not — and that is the very
        configuration where another app, one port over on the same host,
        can reach this port. So the whole origin is compared, scheme and
        port included.

        A request with neither header is not a browser (curl, a script
        on this machine); the bind is what guards those.

        A READ needs no proof: a page of another origin can make the
        browser send one but cannot see the answer, and a GET moves
        nothing (the METHOD is the lane). Asking it of reads would refuse
        the reader following a link to otaku from another site, whose
        document request arrives `cross-site`."""
        if self.command == "GET":
            return True
        site = self.headers.get("Sec-Fetch-Site")
        if site in ("same-origin", "none"):
            return True
        origin = self.headers.get("Origin")
        if site is None and origin is None:
            return True
        addressed = self.headers.get("Host", "")
        if origin in (f"http://{addressed}", f"https://{addressed}"):
            return True
        self.send_error(403, "Cross-origin request")
        return False

    # ---------- what the terminal is told ----------

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """One line per request the reader ASKED for — a page load's
        twenty assets are not news, so they are dropped here rather than
        drawn and scrolled away. The reader's own typefaces are assets
        of the same load, quiet by prefix because their names are
        theirs."""
        path = self._path()
        if path in _QUIET or path.startswith(_CUSTOM_FONTS):
            return
        self.server.hooks.show(f"{self._asked()} {getattr(code, 'value', code)}")

    def log_error(self, format: str, *args: Any) -> None:
        """What the stdlib reports without ever sending a status: a
        request line too broken to parse, a connection that timed out
        mid-request. Everything else it reports here — `send_error`'s
        own "code %d, message %s" — is already on its way through
        `log_request`, and saying it twice would read as two requests."""
        if args and isinstance(args[0], int):
            return
        self.server.hooks.show(f"{self._asked()} {format % args}")

    def log_message(self, format: str, *args: Any) -> None:
        """Silence. Both of the base class's callers are answered above;
        this is the sink for anything that finds a third way here."""

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

    # ---------- answering ----------

    def _answer(self, produce: Callable[[Session], object], *, reading: bool = False) -> None:
        """Run on the session's thread and reply with what came back.
        `reading` declares that `produce` only READS the session — it is
        what lets the job run between the frames of a reply, and every
        screen in the app is one of those. A Refused is an ANSWER —
        "Nothing to export yet" is the sentence to show — so it comes
        back 200 as a notice, marked so the page never has to read the
        wording; only a bug is a 500, and its traceback goes where the
        reader is."""
        try:
            payload = self.server.runner.run(produce, reading=reading)
        except StoppingError:
            # The reader stopped otaku while this was in the queue. An
            # ordinary end, not a fault: no traceback, nothing recorded.
            self.send_error(503, "otaku is stopping")
        except api.NotFound:
            # The path parsed but names a subject that is not there — a
            # story another tab deleted. The same 404 an unmatched path
            # gets, because the spec draws no line between the two.
            self.send_error(404)
        except Refused as e:
            self._json({"notice": str(e), "refused": True})
        except (KeyError, TypeError, ValueError) as e:
            # A field the page did not send, or one it sent wrong — a
            # null where a number belongs included. Malformed wherever it
            # came from, and never a crash to file: anything that scans
            # this port can send one.
            self._failed(400, e)
        except Exception as e:
            self.server.crashed(f"web {self.command} {self._path()}", e)
            self._failed(500, e)
        else:
            self._answered(payload)

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

    def _answered(self, payload: object) -> None:
        """What a handler gave back, as a reply. A bare sentence is a
        notice; a `Created` is 201 and says in `Location` where the thing
        it made now lives; anything else is the payload itself."""
        if isinstance(payload, api.Created):
            self._json(
                {"notice": payload.notice, **payload.extra},
                status=201,
                headers=(("Location", payload.location),),
            )
        elif isinstance(payload, str):
            self._json({"notice": payload})
        else:
            self._json(payload)

    # ---------- the reply stream ----------

    def _play(self, line: str, *, regenerate: bool = False) -> None:
        """One story line, its reply streamed as it arrives — or a fresh
        take on the standing one, which streams the same way.

        The whole turn is ONE job on the session's thread: the line is
        recorded and its reply streamed without the thread going free in
        between, so nothing — another tab landing a story, a `/new` — can
        move the story out from under a line already accepted. The
        handler thread only waits, so the two never write at once. A
        browser that goes away breaks the write, which closes the
        generator: the backend's cancel-and-keep, reached by the same
        door Ctrl+C uses in the terminal."""
        produce = api.regenerate if regenerate else (lambda session: api.play(session, line))
        self._streaming = False
        try:
            self.server.runner.run(lambda session: self._pump(produce(session), session))
        except StoppingError:
            self.send_error(503, "otaku is stopping")
        except Refused as e:
            # Checked before it plays, so this arrives before a byte of
            # the stream: invalid syntax leaves the story untouched, and
            # the usage line is the whole answer.
            self._json({"notice": str(e), "refused": True})
        except Exception as e:
            # Every other POST reaches this through `_answer`; this one
            # cannot, because its answer is a stream. Once a frame has
            # gone out there is no status left to send — the page reads
            # the short stream as a finished reply — so the log is the
            # only place left to say what happened.
            self.server.crashed(f"web {self.command} {self._path()}", e)
            if not self._streaming:
                self._failed(500, e)

    def _pump(self, events: Iterator[PlayEvent], session: Session) -> None:
        """Write the stream out, on the session's thread. Whatever ends
        it — the last event, a closed tab, a failure — the generator is
        closed, which is what records a partial reply."""
        self._streaming = True
        try:
            # Inside the try from the first byte: the header flush is a
            # socket write too, and a tab that RST'd while this job sat
            # queued behind another reply fails right here — the same
            # ordinary end as a disconnect mid-stream, never a crash.
            self.send_response(200)
            self.send_header("Content-Type", _EVENT_STREAM)
            self.send_header("Cache-Control", _NO_STORE)
            # A stream has no length to declare, so the close IS the end
            # of it: on a keep-alive connection the reader would sit
            # waiting for a next turn that never comes, and the reply
            # would never look finished.
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            for happened in events:
                # Asked to stop: leaving the loop closes the generator
                # below, which is the backend's cancel-and-keep door —
                # the same one a vanished browser goes through.
                if self.server.stopping.is_set():
                    return
                self._frame(json.dumps(api.event(happened)))
                # A reply is the longest job there is, and it owns the
                # session's thread for all of it. Between two frames is
                # where the reads waiting on that thread get answered —
                # a rail button pressed mid-reply opens its screen now,
                # not when the model stops.
                self.server.runner.drain()
        except _DISCONNECTED:
            # The whole family, not just the POSIX two: a closed tab on
            # Windows raises ConnectionAbortedError, and filing that as
            # a crash would log a traceback per reload.
            return
        finally:
            events.close()  # type: ignore[attr-defined]
        # The turn ran to its natural end and the reader has the whole
        # reply — the moment the screen wants them back. Not on a Stop or
        # a closed tab (the returns above): whoever cut it either acted
        # or left — the terminal's own "not after a Ctrl+C" rule.
        if session.notification:
            self.server.hooks.ring()

    # ---------- the watch stream ----------

    def _watch_stream(self) -> None:
        """Name each file the page is made of as it changes, for as long
        as the tab is open — the page reloads itself on the name, or
        swaps its stylesheets when the name is one.

        Held open by design, which is why the retry is sent first: a
        server that goes away gets the browser back at the interval this
        names rather than at its own, and the reopened stream is how the
        page finds out that a restarted otaku is serving a different
        build."""
        self.send_response(200)
        self.send_header("Content-Type", _EVENT_STREAM)
        self.send_header("Cache-Control", _NO_STORE)
        # Framed like the reply stream, and for the same reason: no
        # length to declare, so the close is the end of it.
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self._frame(None, retry=_RETRY_MS)
        try:
            for name in _changes(_STATIC_PATH, self.server.custom_web_dir):
                # A stopped server lets the stream go: held open, it
                # would ping a still-working socket for the life of the
                # process after `/web` hands the session back.
                if self.server.stopping.is_set():
                    return
                if name is None:
                    self._ping()
                else:
                    self._frame(name)
        except _DISCONNECTED:
            # the reply stream's rule: a closed tab is the normal end,
            # on every platform's spelling of it
            return

    # ---------- what goes on the wire ----------

    @staticmethod
    def _asset(name: str) -> bytes:
        """One packaged file, read now — which is why editing it needs no
        restart. The name comes from the table, never from the request."""
        target = _STATIC
        for part in name.split("/"):
            target = target / part
        return target.read_bytes()

    def _own_font(self, name: str) -> None:
        """A typeface of the reader's own, from `web/fonts/` in the state
        dir — what makes `custom.css` a whole theme and not a palette:
        `@font-face { src: url("/web-fonts/Mine.woff2") }` and it is
        theirs. Their directory, their files.

        Looked up the way the packaged assets are: the directory is
        LISTED and the name must be one of its entries, so nothing a
        request carries is ever joined onto a path — `..` is not a name
        `iterdir` returns. Not cached, unlike the packaged fonts: those
        carry a version in the name and these do not."""
        directory = self.server.custom_web_dir / "fonts"
        try:
            found = next(
                (f for f in directory.iterdir() if f.name == name and f.suffix in _FONT_SUFFIXES),
                None,
            )
            if found is None:
                return self.send_error(404)
            self._send(found.read_bytes(), _FONT_SUFFIXES[found.suffix], _NO_STORE)
        except OSError:
            # No such directory is the normal case, and a file that went
            # away between the listing and the read is the same answer.
            self.send_error(404)

    def _custom_css(self) -> bytes:
        """The reader's own stylesheet, or nothing. Absent is the normal
        case, so it answers with an empty stylesheet rather than a 404 —
        a red line in the console is not a state to design for."""
        try:
            return (self.server.custom_web_dir / "custom.css").read_bytes()
        except OSError:
            return b""

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
        self._send(json.dumps(payload).encode(), _JSON, _NO_STORE, status=status, headers=headers)

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


def _named(host: str) -> str:
    """The name half of a Host header — the port dropped, brackets off.
    `[::1]:9600` and a bare `[::1]` are the same machine."""
    name = host.rsplit(":", 1)[0] if ":" in host.rsplit("]", 1)[-1] else host
    return name.strip("[]")


def _cookie_name(server: "_Server") -> str:
    """This server's cookie: one per port, since the browser keeps one
    jar per host whatever the port."""
    return f"otaku-{server.server_address[1]}"


# ---------- the watch poller ----------


def _changes(*watched: Path) -> Iterator[str | None]:
    """Each file of the page's that changed, forever — written, added or
    removed. A deletion counts: the browser would otherwise keep running
    a script that is no longer there. A tick where nothing changed yields
    None, so the caller can write to its socket often enough to notice a
    reader that has gone.

    Polled rather than watched, because a dependency on a file watcher
    buys nothing here: a browser reload is slower than the interval, and
    the two directories hold about thirty files between them."""
    seen = _stamps(watched)
    while True:
        time.sleep(_WATCH_INTERVAL)
        now = _stamps(watched)
        # The NAME, not where it lives: all the page does with it is tell
        # a stylesheet from everything else, and a server's own paths are
        # nothing to hand a browser.
        moved = now.keys() | seen.keys()
        changed = [path.name for path in moved if now.get(path) != seen.get(path)]
        seen = now
        if not changed:
            yield None
        yield from changed


def _stamps(watched: tuple[Path, ...]) -> dict[Path, float]:
    """Every file under the watched directories and when it was written.
    A file that goes away between the listing and the stat is simply
    absent from this one — an editor saving atomically does exactly
    that, and it must not take the stream down with it. So is a whole
    directory that is not there: the reader's own is absent until they
    make it."""
    stamps: dict[Path, float] = {}
    for directory in watched:
        with contextlib.suppress(OSError):
            for path in directory.rglob("*"):
                with contextlib.suppress(OSError):
                    if path.is_file():
                        stamps[path] = path.stat().st_mtime
    return stamps
