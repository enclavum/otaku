"""The answers that are a STREAM rather than a payload: a reply as it
arrives — a line played, a reply regenerated (`api.STREAMS`) — framed
as server-sent events as the backend hands them across, and the watch
stream that names each file the page is made of as it changes, held open
for as long as the tab is."""

import json
import select
import threading
from collections.abc import Callable, Iterator

from otaku.web import api
from otaku.web.api import Api, Refused, StoppingError
from otaku.web.api.story.messages import PlayEvent
from otaku.web.server import watch
from otaku.web.server.assets import EVENT_STREAM, NO_STORE, STATIC_PATH
from otaku.web.server.base import DISCONNECTED, RETRY_AFTER, Wire

# The other request the page makes that nobody asked for: held open for
# as long as the tab is, and answered a filename at a time as the files
# the page is made of change.
WATCH = "/api/watch"

# How long a browser waits before reopening the watch stream (the SSE
# `retry` field).
_RETRY_MS = 2000

# How often a reply looks whether its page is still there, the model's
# silence before the first token included.
_LOOK_SECONDS = 0.25


class Streams(Wire):
    """The handler's streaming half."""

    def _stream(self, produce: Callable[[Api], Iterator[PlayEvent]]) -> None:
        """A reply streamed as it arrives — a line played, or a fresh
        take on the standing one.

        The backend runs the whole turn as ONE job on the app's thread
        (`App.stream`): the line recorded and its reply streamed without
        the thread going free in between, so nothing can move the story
        out from under a line already accepted. `produce` validates
        before the first event — a refusal, a head that moved from the
        one `If-Match` named, a malformed body arrive before a byte of
        the stream. The frames are written here, on the request's own
        thread, so a slow socket never holds the app's."""
        try:
            events = produce(self.server.api)
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
            # A body that is not what it claims.
            self._fault(400, e)
        except Exception as e:
            self.server.crashed(f"web {self.command} {self._path()}", e)
            self._fault(500, e)
        else:
            self._pump(events)

    def _pump(self, events: Iterator[PlayEvent]) -> None:
        """Write the stream out. Whatever ends it — the last event, a
        closed tab, a stop, a failure — the stream is closed, which is
        the backend's cancel-and-keep: a partial reply recorded."""
        # Set when the page went away or the server was asked to stop:
        # the reply was cut, and nobody is rung for it.
        cut = threading.Event()
        over = threading.Event()
        try:
            # Inside the try from the first byte: the header flush is a
            # socket write too, and a tab that RST'd while this reply sat
            # queued behind another fails right here — the same ordinary
            # end as a disconnect mid-stream, never a crash.
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
            threading.Thread(target=self._watch_page, args=(events, cut, over), daemon=True).start()
            for happened in events:
                self._frame(json.dumps(api.event(happened)))
        except DISCONNECTED:
            # The whole family, not just the POSIX two: a closed tab on
            # Windows raises ConnectionAbortedError, and filing that as
            # a crash would log a traceback per reload.
            cut.set()
        except StoppingError:
            # otaku closed under the reply: an ordinary end.
            cut.set()
        except Exception as e:
            # Once a frame has gone out there is no status left to send —
            # the page reads the short stream as a finished reply — so the
            # log is the only place left to say what happened.
            cut.set()
            self.server.crashed(f"web {self.command} {self._path()}", e)
        finally:
            over.set()
            events.close()  # type: ignore[attr-defined]
        # The turn ran to its natural end and the reader has the whole
        # reply — the moment the screen wants them back. Not on a Stop or
        # a closed tab: whoever cut it either acted or left — the
        # terminal's own "not after a Ctrl+C" rule.
        if not cut.is_set() and api.app_settings.notifies(self.server.api):
            self.server.hooks.ring()

    def _watch_page(
        self, events: Iterator[PlayEvent], cut: threading.Event, over: threading.Event
    ) -> None:
        """Close the reply the moment its page is gone or the server is
        asked to stop — while the model is still silent too, when no
        write is there to fail. The request was read in full before the
        reply started, so a socket that becomes readable can only carry
        the peer's close (a closed tab, an aborted fetch)."""
        while not over.wait(_LOOK_SECONDS):
            if self.server.stopping.is_set() or self._is_page_gone():
                cut.set()
                events.close()  # type: ignore[attr-defined]
                return

    def _is_page_gone(self) -> bool:
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
        except (OSError, ValueError):
            return True
        return bool(readable)

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
                # process after the chat stops serving (`/set serve off`).
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
