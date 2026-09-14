"""The web frontend, served over a real session.

`page` is the browser's side of it: the same requests the page makes,
against `web.serve` over a session opened for it.

The session is opened ON the serving thread, because that is where the
app opens it — a sqlite connection answers only its own thread, and the
whole of `web.thread` exists to keep every touch of the session there. So the
test thread never holds the session: it holds `page`, and it reads the
store through its own connection (`harness.read_store`), the way every
other scenario asserts.
"""

import contextlib
import json
import queue
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from otaku.backend import WebSettings
from otaku.backend import launch as backend_launch
from otaku.backend.api import providers as api_providers
from otaku.backend.paths import Paths
from otaku.backend.session import Refused
from otaku.store import Store
from otaku.web import address, serve
from scenarios.support.harness import MODEL, PROVIDER, read_store, set_config_provider
from scenarios.support.server import ModelServer


class Page:
    """What the browser does, in the order it does it — plus the store,
    for the half of every story that is not on the wire."""

    def __init__(
        self,
        url: str,
        store: Store,
        stop: threading.Event,
        root: Path,
        context: ssl.SSLContext | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.store = store
        # What a browser would only have after clicking through: the
        # certificate is otaku's own, so nothing can verify it. None for
        # a plain address, where urlopen ignores it anyway.
        self.context = context
        # The state dir, for the half of a story that is files on disk —
        # the reader's own stylesheet and typefaces above all.
        self.root = root
        self._stop = stop

    def stop(self) -> None:
        """Stop the server the way Ctrl+C does, and wait for it to be
        over — for the stories that are about what happens next."""
        self._stop.set()
        time.sleep(0.5)

    def get(self, path: str) -> Any:
        with urllib.request.urlopen(self.url + path, timeout=10, context=self.context) as reply:
            return self._read(reply)

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.send("POST", path, body)

    def put(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.send("PUT", path, body)

    def patch(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.send("PATCH", path, body)

    def delete(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return self.send("DELETE", path, body)

    def send(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        """Every write, by the method that says what it is — the METHOD
        is the lane the server answers on."""
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=30, context=self.context) as reply:
            return self._read(reply)

    def sent(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, str, Any]:
        """The whole answer — code, `Location` and payload — for the
        stories that are about the ANSWER and not only what it said."""
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=30, context=self.context) as reply:
            return int(reply.status), reply.headers.get("Location", ""), self._read(reply)

    def status(
        self,
        path: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> int:
        """The code alone, for the paths that are meant to be refused —
        with the headers a page of another origin would send."""
        request = urllib.request.Request(self.url + path, method=method)
        if method != "GET":
            request.data = data if data is not None else b"{}"
            request.add_header("Content-Type", "application/json")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=10, context=self.context) as reply:
                reply.read()
                return int(reply.status)
        except urllib.error.HTTPError as refused:
            refused.read()
            return int(refused.code)

    def headers(self, path: str) -> dict[str, str]:
        with urllib.request.urlopen(self.url + path, timeout=10, context=self.context) as reply:
            reply.read()
            return {name.lower(): value for name, value in reply.headers.items()}

    def stream(self, path: str) -> Any:
        """A response left OPEN, for the one request the page never
        finishes reading. The caller reads what it needs and closes it —
        reading to the end would be waiting forever, which is the point
        of the endpoint."""
        return urllib.request.urlopen(self.url + path, timeout=10, context=self.context)

    def play(self, line: str, *, regenerate: bool = False) -> list[dict[str, Any]]:
        """One story line, and every event the page would have drawn.
        Regenerating is the same act on the last exchange, so it is a
        POST to that."""
        request = urllib.request.Request(
            f"{self.url}/api/play/last" if regenerate else f"{self.url}/api/play",
            data=json.dumps({} if regenerate else {"line": line}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30, context=self.context) as reply:
            if reply.headers.get("Content-Type", "").startswith("application/json"):
                return [json.loads(reply.read())]
            return [json.loads(row[5:].strip()) for row in reply if row.startswith(b"data:")]

    @staticmethod
    def _read(reply: Any) -> Any:
        body = reply.read()
        if reply.headers.get("Content-Type", "").startswith("application/json"):
            return json.loads(body)
        return body


@pytest.fixture
def page(server: ModelServer, tmp_path: Path) -> Iterator[Page]:
    """`otaku web` over a fresh state dir, on a port nothing else holds.
    The address is the configuration's, exactly as the terminal reads
    it, and the fixture stops the server the way Ctrl+C would."""
    yield from _serving(server, tmp_path, host="127.0.0.1")


@pytest.fixture
def secure(server: ModelServer, tmp_path: Path) -> Iterator[Page]:
    """The same over TLS — `[web] https = true`, the pair generated into
    the state dir's `cert/` because there is nothing there yet."""
    yield from _serving(server, tmp_path, host="127.0.0.1", https=True)


@pytest.fixture
def wide(server: ModelServer, tmp_path: Path) -> Iterator[Page]:
    """The same, bound to every interface — what `[web] host = 0.0.0.0`
    does, and the configuration whose whole point is that other machines
    can reach the port."""
    yield from _serving(server, tmp_path, host="0.0.0.0")


@contextlib.contextmanager
def serving(
    server: ModelServer,
    tmp_path: Path,
    *,
    host: str = "127.0.0.1",
    https: bool = False,
    password: str = "",
    prepare: Callable[[Path], None] | None = None,
) -> Iterator[Page]:
    """A served session a story opens for itself, for the ones the
    fixtures cannot cover: what is in the state dir BEFORE the launch is
    what the story is about, or the server asks for a password."""
    yield from _serving(
        server, tmp_path, host=host, https=https, password=password, prepare=prepare
    )


def _serving(
    server: ModelServer,
    tmp_path: Path,
    *,
    host: str,
    https: bool = False,
    password: str = "",
    prepare: Callable[[Path], None] | None = None,
) -> Iterator[Page]:
    root = tmp_path / "state"
    set_config_provider(root, server)
    if password:
        type_password(root, password)
    if prepare is not None:
        prepare(root)
    stop = threading.Event()
    serving: queue.Queue[str] = queue.Queue()
    failed: queue.Queue[BaseException] = queue.Queue()
    # A port of the OS's picking, taken and given back before the server
    # asks for it: `otaku web` serves where the configuration says, and
    # a test that wants a free port has to find one the same way anybody
    # writing that configuration would.
    where = WebSettings(host=host, port=_free_port(), https=https)
    # Nothing can verify a certificate otaku signed itself, so the test
    # does what a reader does at the interstitial: goes on anyway.
    context = ssl._create_unverified_context() if https else None

    def run() -> None:
        try:
            session = backend_launch.open_session(root)
            # A story that launches twice over one state dir finds the
            # model already remembered, which is a refusal and not a fault.
            with contextlib.suppress(Refused):
                api_providers.switch_model(session, PROVIDER, MODEL)
            try:
                serving.put(address(where))
                serve(
                    session,
                    # The password hash the launch made, the typed password
                    # having never reached anything past the file.
                    replace(where, password=session.web.password),
                    Paths.resolve(root).custom_web_dir,
                    Paths.resolve(root).cert_dir,
                    stop=stop,
                )
            finally:
                session.close()
        except BaseException as e:  # carried to the test, never swallowed
            failed.put(e)

    served = threading.Thread(target=run, name="scenario-web", daemon=True)
    served.start()
    try:
        url = serving.get(timeout=15)
    except queue.Empty as never:
        # Whatever stopped it before it could serve — the launch above
        # all — is the test's answer, not a timeout.
        if not failed.empty():
            raise failed.get_nowait() from never
        raise AssertionError("the server never started") from never
    if host in ("0.0.0.0", "::"):
        # The address READS as `localhost` for a wildcard bind, which is
        # where the test reaches it; the Host header is what the stories
        # vary.
        url = f"{'https' if https else 'http'}://127.0.0.1:{where.port}/"
    _reachable(url, failed, context)
    store = read_store(root)
    try:
        yield Page(url, store, stop, root, context)
    finally:
        stop.set()
        served.join(timeout=10)
        store.close()
        if not failed.empty():
            raise failed.get_nowait()


def type_password(root: Path, password: str) -> None:
    """`[web] password` set the way a reader sets it — typed into
    config.toml in plain, whatever the line held before — for the launch
    to replace with its hash."""
    config = Paths.resolve(root).config_file
    lines = config.read_text().splitlines()
    typed = [
        f"password = {json.dumps(password)}" if line.startswith("password =") else line
        for line in lines
    ]
    config.write_text("\n".join(typed) + "\n")


def _free_port() -> int:
    """A port nothing is listening on, the way a person picking one for
    config.toml would find out — by asking the OS and letting it go."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _reachable(
    url: str, failed: "queue.Queue[BaseException]", context: ssl.SSLContext | None = None
) -> None:
    """Wait until the socket answers. The address is known before the
    server binds — it is the configuration's, not the socket's answer —
    so the first request can otherwise arrive a moment early."""
    for _ in range(150):
        if not failed.empty():
            raise failed.get_nowait()
        try:
            with urllib.request.urlopen(url, timeout=1, context=context) as reply:
                reply.read()
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise AssertionError(f"the server never answered at {url}")
