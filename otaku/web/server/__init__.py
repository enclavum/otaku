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
instead, in `Handler.log_request` and `Server.handle_error`.

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

The package: this module is the dispatcher — `bind`, the route table
and `Handler`, every request's one door — built on `base` (the hooks,
the socket `Server`, and `Wire`, the handler methods that read a request
and write an answer), with the two halves of the handler as mixins:
`guards` decides who may ask, `streams` answers the two requests that
stream. `assets` is the closed table of what may be served, and `watch`
the poller behind the watch stream.
"""

import re
import ssl
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from otaku.backend import WebSettings
from otaku.backend.session import Refused, Session
from otaku.web import api
from otaku.web.server import assets
from otaku.web.server.assets import ASSETS, CSS, CUSTOM_FONTS, IMMUTABLE, NO_STORE
from otaku.web.server.base import LOOPBACK, Hooks, Server
from otaku.web.server.guards import LOGIN, Guards
from otaku.web.server.streams import WATCH, Streams
from otaku.web.thread import SessionRunner, StoppingError

__all__ = ["LOOPBACK", "Hooks", "bind"]

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

# What the backend is doing, as the page asks every few seconds. Neither
# lane, because it never takes the session's THREAD: the handler answers
# it itself, from what can be read from anywhere. That it is answered at
# all is how the page knows otaku is there — a session busy with a reply
# is still a running otaku.
_STATUS = "/api/status"

# What is never worth a line in the terminal: a page load is the
# document and the twenty files that came with it, and only the document
# is news — and neither is the page's own housekeeping, which is a beat
# every few seconds and one stream that never ends.
_QUIET = (set(ASSETS) | {"/custom.css", _STATUS, WATCH}) - {"/"}


def bind(
    config: WebSettings,
    runner: SessionRunner,
    pending: api.Pending,
    custom_web_dir: Path,
    hooks: Hooks,
    tls_context: ssl.SSLContext | None = None,
    password_hash: str | None = None,
) -> "Server":
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
    return Server(
        (config.host, config.port),
        Handler,
        runner=runner,
        pending=pending,
        custom_web_dir=custom_web_dir,
        hooks=hooks,
        tls_context=tls_context,
        password_hash=password_hash,
    )


class Handler(Guards, Streams):
    """The dispatcher: every request comes in here, passes the guards,
    and is answered — by a table row on the session's thread, by one of
    the answers the server holds itself, or as an asset. Its two halves
    are the mixins: `Guards` decides who may ask, `Streams` answers the
    two that stream; `Wire` under both is what goes on the wire."""

    # ---------- routing ----------

    def do_GET(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        if path == LOGIN:
            self._signed_in()
        elif path == _STATUS:
            self._status()
        elif path == WATCH:
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
        elif path in ASSETS:
            name, content_type, cache = ASSETS[path]
            self._send(assets.asset(name), content_type, cache)
        elif path == "/custom.css":
            self._send(assets.custom_css(self.server.custom_web_dir), CSS, NO_STORE)
        elif path.startswith(CUSTOM_FONTS):
            # Decoded like every path parameter (`_match`): the name on
            # disk is what iterdir lists, and "My%20Font.woff2" is not it.
            self._own_font(unquote(path.removeprefix(CUSTOM_FONTS)))
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        body = self._body()
        if path == LOGIN:
            self._sign_in(body)
        # The two that answer with a STREAM rather than a payload, so
        # neither can be a row in the table.
        elif path == "/api/play":
            try:
                files = api.files_from(body)
            except (KeyError, TypeError, ValueError) as e:
                self._failed(400, e)  # malformed, as every other body fault is
                return
            self._play(str(body.get("line", "")), files=files)
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
        if path == LOGIN:
            self._sign_out()
        else:
            self._route(body)

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

    # ---------- what the terminal is told ----------

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """One line per request the reader ASKED for — a page load's
        twenty assets are not news, so they are dropped here rather than
        drawn and scrolled away. The reader's own typefaces are assets
        of the same load, quiet by prefix because their names are
        theirs."""
        path = self._path()
        if path in _QUIET or path.startswith(CUSTOM_FONTS):
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
        elif isinstance(payload, api.Blob):
            # A file never changes once written: kept for a year, like
            # the fonts.
            self._send(payload.data, payload.media_type, IMMUTABLE)
        else:
            self._json(payload)

    def _own_font(self, name: str) -> None:
        """A typeface of the reader's own, or a 404 (`assets.own_font`)."""
        found = assets.own_font(self.server.custom_web_dir, name)
        if found is None:
            self.send_error(404)
            return
        data, media_type = found
        self._send(data, media_type, NO_STORE)
