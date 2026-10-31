"""The page served beside the chat (`/set serve`): one app for both
frontends — a story played in the browser while the chat plays its own,
the chat's line taking its turn on the app's thread after the browser's
reply, and neither story touching the other."""

import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from scenarios.support.harness import App
from scenarios.support.server import ModelServer
from scenarios.web.conftest import Page


class TestBesideTheChat:
    def test_the_browser_and_the_chat_each_play_their_own_story(
        self, app: App, server: ModelServer, monkeypatch
    ) -> None:
        port = _free_port(monkeypatch)
        app.play("/set serve on")
        page = Page(f"http://127.0.0.1:{port}", app.store, app.chat.stop_serving, app.paths.root)
        page.made()
        server.chunk_delay = 0.15  # the browser's reply takes a while
        asked = len(server.requests)
        browser: list[dict[str, Any]] = []
        playing = threading.Thread(target=lambda: browser.extend(page.play("The page speaks.")))
        playing.start()
        _until(lambda: len(server.requests) > asked)  # the browser's reply is streaming
        app.play("The chat speaks.")
        playing.join(30)
        assert not playing.is_alive()
        assert browser[-1]["type"] == "done"
        assert page.story != app.story_id
        for story in (page.story, app.story_id):
            assert story is not None
            roles = [row.role for row in app.store.stories.get_messages(story)]
            assert roles[-2:] == ["user", "assistant"], story


def _free_port(monkeypatch) -> int:
    """A port nothing listens on, made the one the page is served at —
    `OTAKU_WEB_PORT`, so no test reaches a real otaku's."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    monkeypatch.setenv("OTAKU_WEB_PORT", str(port))
    return port


def _until(holds: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not holds():
        assert time.monotonic() < deadline, "never came"
        time.sleep(0.02)
