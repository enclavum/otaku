"""The answers that are a STREAM rather than a payload: a reply as it
arrives — a line played, a reply regenerated (`api.STREAMS`) — framed
as server-sent events on the app's thread, and the watch stream that
names each file the page is made of as it changes, held open for as long
as the tab is."""

import json
import select
from collections.abc import Callable, Iterator

from otaku.web import api
from otaku.web.api import Api, App, Refused
from otaku.web.api.story.messages import PlayEvent
from otaku.web.server import watch
from otaku.web.server.assets import EVENT_STREAM, NO_STORE, STATIC_PATH
from otaku.web.server.base import DISCONNECTED, RETRY_AFTER, Wire
from otaku.web.thread import StoppingError

# The other request the page makes that nobody asked for: held open for
# as long as the tab is, and answered a filename at a time as the files
# the page is made of change.
WATCH = "/api/watch"

# How long a browser waits before reopening the watch stream (the SSE
# `retry` field).
_RETRY_MS = 2000


class Streams(Wire):
    """The handler's streaming half."""

    def _stream(self, produce: Callable[[Api], Iterator[PlayEvent]]) -> None:
        """A reply streamed as it arrives — a line played, or a fresh
        take on the standing one.

        The whole turn is ONE job on the app's thread: the line is
        recorded and its reply streamed without the thread going free in
        between, so nothing can move the story out from under a line
        already accepted. `produce` validates before the first event is
        asked for — a refusal, a head that moved from the one `If-Match`
        named, a malformed body arrive before a byte of the stream. The
        handler thread only waits, so the two never write at once. A
        browser that goes away breaks the write, which closes the
        generator: the backend's cancel-and-keep, reached by the same
        door Ctrl+C uses in the terminal."""
        self._streaming = False
        try:
            self.server.runner.run(lambda app: self._pump(produce(Api(app)), app))
        except StoppingError:
            self._fault(503, headers=(("Retry-After", RETRY_AFTER),))
        except api.PreconditionRequired as e:
            self._fault(428, e)
        except Refused as e:
            # Checked before it plays, so this arrives before a byte of
            # the stream: invalid syntax leaves the story untouched, and
            # the usage line is the whole answer.
            self._outcome(e)
        except (KeyError, TypeError, ValueError) as e:
            # Before the stream, a body that is not what it claims; inside
            # it, a bug like any other below.
            if not self._streaming:
                self._fault(400, e)
            else:
                self.server.crashed(f"web {self.command} {self._path()}", e)
        except Exception as e:
            # Every other request reaches this through `_answer`; this one
            # cannot, because its answer is a stream. Once a frame has
            # gone out there is no status left to send — the page reads
            # the short stream as a finished reply — so the log is the
            # only place left to say what happened.
            self.server.crashed(f"web {self.command} {self._path()}", e)
            if not self._streaming:
                self._fault(500, e)

    def _pump(self, events: Iterator[PlayEvent], app: App) -> None:
        """Write the stream out, on the app's thread. Whatever ends
        it — the last event, a closed tab, a failure — the generator is
        closed, which is what records a partial reply."""
        self._streaming = True
        # Between frames the page can only be seen to be gone by a
        # write; before the first token there is none. So for this reply
        # the idle hook drains and then answers whether the page is still
        # there, and a vanished page ends the reply at once.
        was_idle = app.set_on_idle(self._drain_and_look)
        try:
            # Inside the try from the first byte: the header flush is a
            # socket write too, and a tab that RST'd while this job sat
            # queued behind another reply fails right here — the same
            # ordinary end as a disconnect mid-stream, never a crash.
            self.send_response(200)
            self.send_header("Content-Type", EVENT_STREAM)
            self.send_header("Cache-Control", NO_STORE)
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
                # app's thread for all of it. Between two frames is
                # where the reads waiting on that thread get answered —
                # a rail button pressed mid-reply opens its screen now,
                # not when the model stops.
                self.server.runner.drain()
        except DISCONNECTED:
            # The whole family, not just the POSIX two: a closed tab on
            # Windows raises ConnectionAbortedError, and filing that as
            # a crash would log a traceback per reload.
            return
        finally:
            app.set_on_idle(was_idle)
            events.close()  # type: ignore[attr-defined]
        # The turn ran to its natural end and the reader has the whole
        # reply — the moment the screen wants them back. Not on a Stop or
        # a closed tab (the returns above): whoever cut it either acted
        # or left — the terminal's own "not after a Ctrl+C" rule.
        if api.app_settings.notifies(Api(app)):
            self.server.hooks.ring()

    def _drain_and_look(self) -> bool:
        """The idle hook for one reply: the runner's drain, then whether
        the page is still at the other end. The request is read in full
        before the reply starts, so a socket that becomes readable during
        it can only carry the peer's close (a closed tab, an aborted
        fetch); a server asked to stop counts as gone too."""
        self.server.runner.drain()
        if self.server.stopping.is_set():
            return False
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
        except (OSError, ValueError):
            return False
        return not readable

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
        self.send_header("Content-Type", EVENT_STREAM)
        self.send_header("Cache-Control", NO_STORE)
        # Framed like the reply stream, and for the same reason: no
        # length to declare, so the close is the end of it.
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self._frame(None, retry=_RETRY_MS)
        try:
            for name in watch.changes(STATIC_PATH, self.server.custom_web_dir):
                # A stopped server lets the stream go: held open, it
                # would ping a still-working socket for the life of the
                # process after `/web` hands the app back.
                if self.server.stopping.is_set():
                    return
                if name is None:
                    self._ping()
                else:
                    self._frame(name)
        except DISCONNECTED:
            # the reply stream's rule: a closed tab is the normal end,
            # on every platform's spelling of it
            return
