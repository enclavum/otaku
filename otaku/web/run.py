"""The web frontend's life — everything `otaku web` is, above HTTP.

`run` is the whole of it over the app, as `chat.run` is the
terminal's: what the launch has to say, where the page is, and the tail
of requests until the reader stops it. Everything `otaku web` prints is
printed HERE, into the terminal it was launched from — `web.server`
below prints nothing and is handed one line per request worth showing.

`serve` is the composition root under `run`: the thread that owns the
app's thread, the state that spans requests, the hooks the server
answers the beat from, and the signal that makes Ctrl+C an orderly stop
are all wired here — so `server` stays HTTP and `thread` stays a queue.

`settings` is where this frontend listens: the app's own `[web]`
slice, with the development port override laid over it. The address is
the medium, like a key binding or a column width — so a frontend reads
one slice and only its own, and gets it the way the terminal gets its
looks, off the app rather than out of the file.
"""

import contextlib
import os
import signal
import ssl
import sys
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from otaku.console import banner, keys, sound, ticker
from otaku.web.api import Api, App
from otaku.web.api.app.settings import WebSettings
from otaku.web.cert import CertError, get_context
from otaku.web.server import Hooks, bind
from otaku.web.thread import AppRunner

__all__ = ["ServeError", "run", "serve", "settings"]

# For working ON otaku, and deliberately unadvertised (`settings`).
# There is no host variable to match — which interface a server answers
# on is a decision, and a decision belongs in the file.
_PORT_VAR = "OTAKU_WEB_PORT"


class ServeError(Exception):
    """The address in the config cannot be listened on — a port another
    program already holds, a host this machine does not answer to, a
    privileged port. Raised instead of the socket's own OSError so the
    caller has a sentence to print rather than a traceback to dump."""


def run(app: App, *, host: str | None = None, port: int | None = None) -> bool:
    """This frontend's whole life over the app, as `chat.run` is the
    terminal's: what its launch had to say, where the page is, and
    the tail of requests until the reader stops it. Everything printed
    by `otaku web` is printed here — into the terminal it was launched
    from, which is the only part of that terminal this frontend has.
    Closing the app stays the caller's, as it is for the other
    frontend. Returns whether the reader asked to be served again on
    fresh sources (Ctrl+R) — which is the caller's to do, over an app
    it has closed.

    `host` and `port` are a caller overriding where it listens for this
    run — the command line's own, laid over the configured address."""
    # The launch's own reports have nowhere to go in a browser that is
    # not open yet, so they go where the reader is: the terminal they
    # typed in, one blank line after the lot, as the terminal's launch
    # does.
    notices = Api(app).app.get().launch.notices
    for report in notices:
        print(report)
    if notices:
        print()
    restart = _serve_with_tail(app, host=host, port=port, restartable=True)
    # The shell prompt starts against a blank rather than against the
    # last request.
    print()
    return restart


def run_shared(app: App) -> None:
    """This frontend over an app another frontend has open — the
    terminal's `/web`, into a chat in progress: what its launch had to
    say was printed when the chat opened, so all this owes the reader is
    the address and the tail until they stop it. Never restartable, the process not being
    this caller's to replace; nothing printed after, the blank before
    the chat's next prompt being its own. Closing the app stays the
    caller's."""
    _serve_with_tail(app, host=None, port=None, restartable=False)


def _serve_with_tail(app: App, *, host: str | None, port: int | None, restartable: bool) -> bool:
    """Where the page is, then the server with the last requests under
    it until the reader stops it; whether they asked for a restart."""
    setup = Api(app).settings.web.get()
    config = settings(setup.config, host=host, port=port)
    # Said before the socket is bound, because the address is the
    # configuration's and not the socket's answer: a reader can be
    # opening the page while the first request is still arriving.
    print(banner.render_web(config))
    # The last few requests, kept under the address and rewritten in
    # place: proof that the browser is reaching this server, in a
    # terminal that stays the height it started at. It owns the terminal
    # while it runs — Ctrl+C is answered here, so the tty must not also
    # echo `^C` into the middle of the answer.
    with ticker.Ticker() as tail:

        def stopping(sentence: str) -> None:
            """The first Ctrl+C (or Ctrl+D, or Ctrl+R), in words — the
            last line of the log, said in the log's own voice because
            that is what the reader is already reading. Shown BEFORE the
            tail stops, which is the only order that works: a stopped
            tail draws nothing, and a line drawn as a row is one a later
            redraw keeps rather than takes back. Then the tail stops, and
            with it the terminal gets its own behaviour back — the NEXT
            press is the fatal one and would otherwise leave it without.
            The sentence is the truth: a reply already in flight is
            finished, not cut."""
            tail.show(sentence)
            tail.stop()

        restart = serve(
            app,
            config,
            setup.custom_dir,
            setup.cert_dir,
            show=tail.show,
            stopping=stopping,
            restartable=restartable,
        )
    return restart


def settings(
    config: WebSettings, *, host: str | None = None, port: int | None = None
) -> WebSettings:
    """Where this frontend listens: the app's own `[web]` slice, with
    what outranks it laid over. The slice arrives from the app as the
    terminal's looks do — a frontend reads one slice and only its own,
    and neither reads the file for itself.

    Three answers, in the order they win. The FILE is the standing one, a
    decision made once and kept. `OTAKU_WEB_PORT` moves a development
    server off it so a real otaku can keep the port — unadvertised, and
    applied here rather than in `cli` because which port this frontend
    listens on is its own business wherever the answer comes from. An
    explicit `host` or `port` beats both: it was typed for this run, by
    somebody who is watching it."""
    wanted = os.environ.get(_PORT_VAR, "").strip()
    if wanted:
        if wanted.isdigit() and 1 <= int(wanted) <= 65535:
            config = replace(config, port=int(wanted))
        else:
            print(f"otaku: ignoring {_PORT_VAR}={wanted!r} — not a port number", file=sys.stderr)
    if host is not None:
        config = replace(config, host=host)
    if port is not None:
        config = replace(config, port=port)
    return config


def serve(
    app: App,
    config: WebSettings,
    custom_web_dir: Path,
    cert_dir: Path,
    *,
    show: Callable[[str], None] | None = None,
    stopping: Callable[[str], None] | None = None,
    stop: threading.Event | None = None,
    restartable: bool = False,
) -> bool:
    """Serve the app until interrupted — the composition root
    under `run`, the background worker started here for the same reason
    `chat.run` starts it. `config` is the configured address (`settings`
    above); `custom_web_dir` is the state dir's own web directory, where
    the reader's `custom.css` lives and nothing else is ever read from;
    `cert_dir` is where the TLS pair lives, read only when the address
    says https. Closing the app stays the caller's, like every other
    frontend's.

    Nothing here is printed. `show` is handed one line per request worth
    showing and `stopping` the sentence for the moment the reader asks
    for the door — where either APPEARS is the caller's, because this
    package draws nothing in a terminal.

    `stop` is how a caller that is not a terminal ends the serving: set
    it and this returns, the same way Ctrl+C does. Ctrl+C itself is only
    wired when this runs on the main thread, because that is the only
    thread a signal handler can be installed from — and the keys at the
    terminal (Ctrl+D as Ctrl+C; with `restartable`, Ctrl+R as a stop
    that asks to be served again) are watched only there too. Returns
    whether Ctrl+R asked."""
    app.start_worker()
    runner = AppRunner(app)
    # The interface the hooks below ask, each safe from any thread.
    interface = Api(app)
    tls_context = _get_tls_context(config, cert_dir, app, show)
    try:
        server = bind(
            config,
            runner,
            custom_web_dir,
            Hooks(
                show=show or (lambda line: None),
                # A contained crash goes where every frontend's contained
                # crashes go. Called off the app's thread by design:
                # it appends to a day-file and never reaches the store,
                # and a handler that queued for the app's thread to
                # report a crash would wait behind the very job that
                # crashed.
                record=app.record_crash,
                working=interface.app.status.get,
                # A landed reply rings the terminal this server was
                # launched from — one user, one desk: the browser and
                # this shell sit in front of the same reader, and the
                # sound machinery is the one the chat already rings.
                ring=lambda: sound.ring(interface.settings.terminal.get().notification_sound),
                # Slow on purpose, so asked on the handler's thread: it
                # reads the config alone, never the store.
                check_password=interface.settings.check_password,
            ),
            tls_context,
            config.password or None,
        )
    except OSError as e:
        # The address is configuration, and configuration that cannot be
        # honoured is answered with a sentence, not a stack.
        raise ServeError(f"cannot serve at {config.host}:{config.port} — {e.strerror or e}") from e
    if stop is not None:
        server.stopping = stop
    # The app's one thread is this frontend's too. Between frames a
    # reply gives it back (the server's pump drains), and while a reply
    # is only being WAITED for — the long silence before the first token
    # above all — the backend hands it over here. Either way it spends
    # the time answering reads, which is why a screen opens mid-reply.
    #
    # The hook is BORROWED, not taken: an app that came here from a chat
    # (`/web`) goes back to it, and the finally below puts it back.
    was_idle = app.set_on_idle(runner.drain)
    # Serving moves to a thread so that THIS one — the thread that opened
    # the app, and the only one its sqlite connection will answer —
    # stays free to run the work the handlers hand it.
    # The signal handler is installed before the serving thread starts,
    # so no request can race a window where Ctrl+C still meant Python's
    # default. Only the main thread may install one at all; a caller
    # serving from another has `stop` instead. A SECOND Ctrl+C never
    # reaches this code: the handler hands SIGINT back to the OS
    # default, so the next press is fatal at the C level — by design
    # (`_Server.stopping`), the way out of a wedged engine.
    on_main = threading.current_thread() is threading.main_thread()
    was = (
        signal.signal(signal.SIGINT, _interrupting(server.stopping, stopping, "Shutting down…"))
        if on_main
        else None
    )
    # The keys the terminal answers while it serves: Ctrl+D is Ctrl+C,
    # and — for `otaku web`, whose process is its own to replace —
    # Ctrl+R is a stop that asks to be served again on fresh sources.
    # Watched where the signal is wired, and off a pipe not at all. A
    # key stops without handing the signal back: that is the handler's
    # own move, and only the main thread may make it.
    asked = threading.Event()
    table: dict[bytes, Callable[[], None]] = {}
    if on_main:
        table[keys.CTRL_D] = _stopping(server.stopping, stopping, "Shutting down…")
    restart = _stopping(server.stopping, stopping, "Restarting…")

    def restarting() -> None:
        asked.set()
        restart()

    if restartable and on_main:
        table[keys.CTRL_R] = restarting
    try:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with keys.watching(table):
            runner.loop(server.stopping)
    finally:
        if was is not None:
            signal.signal(signal.SIGINT, was)
        server.shutdown()
        server.server_close()
        # Whoever was waiting on the app's thread is told there is no
        # answer coming, rather than waiting for one forever.
        runner.abandon()
        # The borrowed hook goes back to whoever held it before.
        app.set_on_idle(was_idle)
    return asked.is_set()


def _get_tls_context(
    config: WebSettings,
    cert_dir: Path,
    app: App,
    show: Callable[[str], None] | None,
) -> ssl.SSLContext | None:
    """What every accepted connection is wrapped in, or None where the
    address is a plain one. Asked for before the socket is, so a
    configuration that cannot be served under never gets as far as
    printing an address nobody can open.

    A failure here is the reader's to act on and not a crash to dump:
    the traceback goes to the error log the way every contained crash
    does, and what comes back out is the sentence plus where to read the
    rest."""
    if not config.https:
        return None
    try:
        return get_context(cert_dir, show)
    except CertError as e:
        where = app.record_crash("web certificate", e)
        raise ServeError(f"{e}" + (f" (recorded in {where})" if where else "")) from e


def _interrupting(
    stop: threading.Event, said: Callable[[str], None] | None, sentence: str
) -> Callable[..., None]:
    """The first Ctrl+C, and what it says. The handler gives the signal
    back to Python before setting the flag, so a reader who presses it
    again is answered at once rather than waiting on a stream that may
    not yield for minutes."""
    halt = _stopping(stop, said, sentence)

    def interrupt(*_: Any) -> None:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        halt()

    return interrupt


def _stopping(
    stop: threading.Event, said: Callable[[str], None] | None, sentence: str
) -> Callable[[], None]:
    """A stop asked for, and what it says: the flag set, then `sentence`
    said through `said` — the caller's, this package having no terminal
    — and a hook may not be what stops a shutdown."""

    def halt() -> None:
        stop.set()
        if said is not None:
            with contextlib.suppress(Exception):
                said(sentence)

    return halt
