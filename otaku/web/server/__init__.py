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
The thread that owns the app is `web.thread`; everything above HTTP
(the banner, the tail, Ctrl+C, the wiring of all three) is `web.run`,
which is also the only place anything is printed: the serving is handed
one line per request worth showing (`Hooks`) and knows nothing about
where it appears. What the stdlib would have put in the terminal on its
own — an access line per font, a traceback per closed tab — is answered
instead, in `Handler.log_request` and `Server.handle_error`.

What may be served is a CLOSED table: a request path is looked up, never
joined onto a directory, so no request can compose its way to
`configs/providers.toml`.

The API is two tables of methods and paths — `api.ROUTES`, answered
with a payload, and `api.STREAMS`, the two writes answered with the
reply as it arrives — and the METHOD is the lane. A GET only reads the
app and is answered on the read queue — during a reply as well as
between them, which is what keeps every screen answerable while the
model talks. Every other method moves the story and takes the one
thread in turn, in the order it arrived. An answer about one story says
the head it stands at as the `ETag`, and a write that moves the head
names the one it moves from in `If-Match` — 428 where it does not, 412
where the head has moved.

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

from otaku.web import api
from otaku.web.api import Api, App, Refused
from otaku.web.api.app.settings import WebSettings
from otaku.web.server import assets
from otaku.web.server.assets import ASSETS, CSS, CUSTOM_FONTS, NO_STORE, PRIVATE_IMMUTABLE
from otaku.web.server.base import LOOPBACK, RETRY_AFTER, Hooks, Server
from otaku.web.server.guards import LOGIN, Guards
from otaku.web.server.streams import WATCH, Streams
from otaku.web.thread import AppRunner, StoppingError

__all__ = ["LOOPBACK", "Hooks", "bind"]

# The lane is the METHOD. A GET only READS the app and is answered on
# the read queue — during a reply as well as between them, which is what
# keeps every screen answerable while the model talks; every other method
# moves the story and waits its turn. HTTP already draws that line, so a
# handler cannot be filed under the wrong one by accident.
_READING = "GET"

# The path parameters that are ROW IDS. They match digits alone, so a
# page that lost its story cannot address `/api/stories/null/…`: no route
# matches, the answer is a plain 404, and no handler is ever handed a
# number that is not one. Every other parameter is a NAME — a provider, a
# model, a setting, a picture — and names may be anything a segment can
# hold.
_NUMERIC = ("story", "message", "scene", "character", "journal")


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
# so pattern length would order them exactly backwards). A stream keeps
# its own mark: its answer is the reply as it arrives, not a payload.
_Row = tuple[str, "re.Pattern[str]", Any, bool]

_ROUTES: tuple[_Row, ...] = tuple(
    (method, _pattern(template), call, streams)
    for method, template, call, streams in sorted(
        (
            (method, template, call, streams)
            for table, streams in ((api.ROUTES, False), (api.STREAMS, True))
            for (method, template), call in table.items()
        ),
        key=lambda row: (row[1].count("{"), -len(row[1])),
    )
)

# What the backend is doing, as the page asks every few seconds. Neither
# lane, because it never takes the app's THREAD: the handler answers
# it itself, from what can be read from anywhere. That it is answered at
# all is how the page knows otaku is there — an app busy with a reply
# is still a running otaku.
_STATUS = "/api/app/status"

# The paths the server answers itself, by the methods it answers them
# with — what a 405 names in `Allow` beside the tables' own.
_HELD: dict[str, tuple[str, ...]] = {
    LOGIN: ("GET", "POST", "DELETE"),
    _STATUS: ("GET",),
    WATCH: ("GET",),
}

# What is never worth a line in the terminal: a page load is the
# document and the twenty files that came with it, and only the document
# is news — and neither is the page's own housekeeping, which is a beat
# every few seconds and one stream that never ends.
_QUIET = (set(ASSETS) | {"/custom.css", _STATUS, WATCH}) - {"/"}


def bind(
    config: WebSettings,
    runner: AppRunner[App],
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
    never the typed password (`backend.domain.app._passwords`). None is the default
    configuration, where nobody is asked for anything."""
    return Server(
        (config.host, config.port),
        Handler,
        runner=runner,
        custom_web_dir=custom_web_dir,
        hooks=hooks,
        tls_context=tls_context,
        password_hash=password_hash,
    )


class Handler(Guards, Streams):
    """The dispatcher: every request comes in here, passes the guards,
    and is answered — by a table row on the app's thread, by one of
    the answers the server holds itself, or as an asset. Its two halves
    are the mixins: `Guards` decides who may ask, `Streams` answers what
    streams; `Wire` under both is what goes on the wire."""

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
            self._fault(404)

    def do_POST(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        body = self._read_body()
        if body is None:
            return
        if path == LOGIN:
            self._sign_in(body)
        else:
            self._route(body)

    def do_PUT(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        body = self._read_body()
        if body is not None:
            self._route(body)

    def do_PATCH(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        body = self._read_body()
        if body is not None:
            self._route(body)

    def do_DELETE(self) -> None:
        path = self._path()
        if not self._admitted(path):
            return
        # Read whether it is needed or not: a body left in the socket is
        # the first bytes of the NEXT request on a kept-alive connection,
        # which then arrives as a method called `{}GET`.
        body = self._read_body()
        if body is None:
            return
        if path == LOGIN:
            self._sign_out()
        else:
            self._route(body)

    def _route(self, body: dict[str, Any] | None = None) -> None:
        """One request against the API tables: the first template this
        method and path match wins, and what the path captured reaches
        the handler as its `params`. The METHOD decides the lane, which
        is what lets a GET be answered while a reply streams. A path some
        other method answers is 405, naming those in `Allow`; one nothing
        answers, 404."""
        found = self._match(body or {})
        if found is not None:
            call, ask, streams = found
            if streams:
                self._stream(lambda interface: call(interface, ask))
            else:
                self._answer(lambda app: call(Api(app), ask), reading=self.command == _READING)
            return
        allowed = self._allowed()
        if allowed:
            self._fault(405, headers=(("Allow", ", ".join(allowed)),))
        else:
            self._fault(404)

    def _allowed(self) -> list[str]:
        """The methods this path is answered with — the tables' and the
        server's own."""
        path = self._path()
        found = {method for method, pattern, _, _ in _ROUTES if pattern.match(path)}
        return sorted(found | set(_HELD.get(path, ())))

    def _match(self, body: dict[str, Any]) -> tuple[Any, api.Ask, bool] | None:
        """The row this request names, the request taken apart for it,
        and whether its answer streams."""
        path = self._path()
        for method, pattern, call, streams in _ROUTES:
            found = pattern.match(path) if method == self.command else None
            if found is None:
                continue
            named = {name: unquote(value) for name, value in found.groupdict().items()}
            ask = api.Ask(
                params=named,
                query=self._query(),
                body=body,
                if_match=self.headers.get("If-Match"),
            )
            return call, ask, streams
        return None

    def _status(self) -> None:
        """What the backend is doing, told without asking for the app's
        thread: its one-line status. Reaching this at all is the first
        answer — an otaku deep in a reply is still a running otaku.
        `notices` stays empty: nothing speaks after the launch but a
        model switch, whose notes ride its own answer."""
        self._json({"status": self.server.hooks.working(), "notices": []})

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

    def _answer(self, produce: Callable[[App], object], *, reading: bool = False) -> None:
        """Run on the app's thread and reply with what came back.
        `reading` declares that `produce` only READS the app — it is
        what lets the job run between the frames of a reply, and every
        screen in the app is one of those. A Refused is an ANSWER — "No
        such story." is the sentence to show — so it comes back as the
        status its kind says, the sentence its `detail` (`_outcome`);
        only a bug is a 500, and its traceback goes where the reader
        is."""
        try:
            payload = self.server.runner.run(produce, reading=reading)
        except StoppingError:
            # The reader stopped otaku while this was in the queue. An
            # ordinary end, not a fault: no traceback, nothing recorded.
            self._fault(503, headers=(("Retry-After", RETRY_AFTER),))
        except api.PreconditionRequired as e:
            # A write that moves a head, sent without the one it moves
            # from: the page's fault, not the reader's.
            self._fault(428, e)
        except Refused as e:
            self._outcome(e)
        except (KeyError, TypeError, ValueError) as e:
            # A field the page did not send, or one it sent wrong — a
            # null where a number belongs included. Malformed wherever it
            # came from, and never a crash to file: anything that scans
            # this port can send one.
            self._fault(400, e)
        except Exception as e:
            self.server.crashed(f"web {self.command} {self._path()}", e)
            self._fault(500, e)
        else:
            self._answered(payload)

    def _answered(self, payload: object) -> None:
        """What a handler gave back, as a reply. A `Tagged` answer says the
        head its story stands at as the `ETag`; a `Created` is 201 and an
        `Accepted` 202, each saying in `Location` where the thing it
        made, or the work it started, now lives; a `Blob` is bytes;
        anything else is the payload itself."""
        headers: tuple[tuple[str, str], ...] = ()
        if isinstance(payload, api.Tagged):
            headers = (("ETag", api.etag(payload.head)),)
            payload = payload.answer
        if isinstance(payload, api.Created | api.Accepted):
            self._json(
                payload.answer,
                status=201 if isinstance(payload, api.Created) else 202,
                headers=(*headers, ("Location", payload.location)),
            )
        elif isinstance(payload, api.Blob):
            # A file never changes once written: kept for a year, like
            # the fonts — by this browser alone, as it is behind the sign-in.
            self._send(payload.data, payload.media_type, PRIVATE_IMMUTABLE, headers=headers)
        else:
            self._json(payload, headers=headers)

    def _own_font(self, name: str) -> None:
        """A typeface of the reader's own, or a 404 (`assets.own_font`)."""
        found = assets.own_font(self.server.custom_web_dir, name)
        if found is None:
            self._fault(404)
            return
        data, media_type = found
        self._send(data, media_type, NO_STORE)
