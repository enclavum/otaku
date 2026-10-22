"""The web frontend's life — everything `otaku serve` is, above HTTP.

`run` is the whole of it over the app, as `chat.run` is the
terminal's: what the launch has to say, where the page is, and the tail
of requests until the reader stops it. Everything `otaku serve` prints is
printed HERE, into the terminal it was launched from — `web.server`
below prints nothing and is handed one line per request worth showing.

`run_beside` is its other life: the page served beside a frontend that
holds the app and keeps its own terminal — the chat's `/set serve` — on
threads of its own, until that frontend stops the server it answers.

`_serve` is the composition root under `run`: the server over the app,
the hooks it answers the beat from, and the signal that makes Ctrl+C an
orderly stop are all wired here — so `server` stays HTTP. The app's
thread is the backend's own (`App.run`): this one only waits for the
stop.

Where it listens is the app's own `[web]` slice in force
(`api.settings.web.get`), the environment's over the file's. The address is the medium, like a key
binding or a column width — so a frontend reads one slice and only its
own, and gets it the way the terminal gets its looks, off the app rather
than out of the file.
"""

import contextlib
import signal
import ssl
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from otaku.console import banner, keys, sound, ticker
from otaku.web.api import Api, App
from otaku.web.api.app.settings import WebSettings
from otaku.web.cert import CertError, get_context
from otaku.web.server import Hooks, Server, bind

__all__ = ["ServeError", "Server", "run", "run_beside"]

# How often the serving thread polls its wait for the stop (`server.stopping`)
# — a stop no event announces, the timeout is what notices it.
_STOP_POLL_INTERVAL = 0.25


class ServeError(Exception):
    """The address in the config cannot be listened on — a port another
    program already holds, a host this machine does not answer to, a
    privileged port. Raised instead of the socket's own OSError so the
    caller has a sentence to print rather than a traceback to dump."""


# ---------- its two lives ----------


def run(app: App) -> bool:
    """This frontend's whole life over the app, as `chat.run` is the
    terminal's: what its launch had to say, where the page is, and
    the tail of requests until the reader stops it. Everything printed
    by `otaku serve` is printed here — into the terminal it was launched
    from, which is the only part of that terminal this frontend has.
    Closing the app stays the caller's, as it is for the other
    frontend. Returns whether the reader asked to be served again on
    fresh sources (Ctrl+R) — which is the caller's to do, over an app
    it has closed."""
    # The launch's own reports have nowhere to go in a browser that is
    # not open yet, so they go where the reader is: the terminal they
    # typed in, one blank line after the lot, as the terminal's launch
    # does.
    notices = Api(app).app.get().launch.notices
    for report in notices:
        print(report)
    if notices:
        print()
    setup = Api(app).settings.web.get()
    # Said before the socket is bound, because the address is the
    # configuration's and not the socket's answer: a reader can be
    # opening the page while the first request is still arriving.
    print(banner.render_web(setup.config))
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

        restart = _serve(
            app,
            setup.config,
            setup.custom_dir,
            setup.cert_dir,
            show=tail.show,
            stopping=stopping,
        )
    # The shell prompt starts against a blank rather than against the
    # last request.
    print()
    return restart


def run_beside(app: App) -> Server:
    """The page served beside the frontend that holds the app, until the
    server's `stop`: at the configured address, nothing printed, no keys
    and no signal — those are the holder's — and a landed reply ringing
    the terminal as `otaku serve` rings it. Raises ServeError where the
    address cannot be served."""
    setup = Api(app).settings.web.get()
    server = _bind_server(app, setup.config, setup.custom_dir, setup.cert_dir, None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------- the server ----------


def _serve(
    app: App,
    config: WebSettings,
    custom_web_dir: Path,
    cert_dir: Path,
    *,
    show: Callable[[str], None] | None = None,
    stopping: Callable[[str], None] | None = None,
) -> bool:
    """Serve the app until interrupted — the composition root
    under `run`, the background worker started here for the same reason
    `chat.run` starts it. `config` is the address in force
    (`api.settings.web`); `custom_web_dir` is the state dir's own web directory, where
    the reader's `custom.css` lives and nothing else is ever read from;
    `cert_dir` is where the TLS pair lives, read only when the address
    says https. Closing the app stays the caller's, like every other
    frontend's.

    Nothing here is printed. `show` is handed one line per request worth
    showing and `stopping` the sentence for the moment the reader asks
    for the door — where either APPEARS is the caller's, because this
    package draws nothing in a terminal.

    Ctrl+C — and SIGTERM, a `docker stop` or a `kill`, taken as the first
    Ctrl+C — is only wired when this runs on the main thread, because that
    is the only thread a signal handler can be installed from — and the
    keys at the terminal (Ctrl+D as Ctrl+C, Ctrl+R as a stop that asks to
    be served again) are watched only there too. Returns whether Ctrl+R
    asked."""
    app.start_worker()
    server = _bind_server(app, config, custom_web_dir, cert_dir, show)
    # Serving runs on a thread of its own, and every handler hands its
    # work to the app's (`App.run`), so THIS one only waits for the stop.
    # The signal handler is installed before the serving thread starts,
    # so no request can race a window where Ctrl+C still meant Python's
    # default. Only the main thread may install one at all. A SECOND Ctrl+C never
    # reaches this code: the handler hands SIGINT back to the OS
    # default, so the next press is fatal at the C level — by design
    # (`_Server.stopping`), the way out of a wedged engine.
    on_main = threading.current_thread() is threading.main_thread()
    was = (
        signal.signal(signal.SIGINT, _interrupting(server.stopping, stopping, "Shutting down…"))
        if on_main
        else None
    )
    halt = _stopping(server.stopping, stopping, "Shutting down…")
    was_term = signal.signal(signal.SIGTERM, lambda *_: halt()) if on_main else None
    # The keys the terminal answers while it serves: Ctrl+D is Ctrl+C,
    # and — for `otaku serve`, whose process is its own to replace —
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

    if on_main:
        table[keys.CTRL_R] = restarting
    try:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with keys.watching(table):
            while not server.stopping.wait(_STOP_POLL_INTERVAL):
                pass
    finally:
        if was is not None:
            signal.signal(signal.SIGINT, was)
        if was_term is not None:
            signal.signal(signal.SIGTERM, was_term)
        server.stop()
    return asked.is_set()


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


def _bind_server(
    app: App,
    config: WebSettings,
    custom_web_dir: Path,
    cert_dir: Path,
    show: Callable[[str], None] | None,
) -> Server:
    """The server bound over the app, its hooks wired — not serving yet.
    Raises ServeError where the address cannot be served."""
    # The interface the hooks below ask, each safe from any thread.
    interface = Api(app)
    tls_context = _get_tls_context(config, cert_dir, app, show)
    try:
        return bind(
            config,
            app,
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
