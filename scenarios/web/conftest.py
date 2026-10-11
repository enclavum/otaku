"""The web frontend, served over a real app.

`page` is the browser's side of it: the same requests the page makes,
against `web.serve` over an app opened for it — and, as the page does,
the story it plays held on its side (`Page.story`), made by its first
line, since the server holds none.

The app is opened ON the serving thread, because that is where `otaku
web` opens it — a sqlite connection answers only its own thread, and the
whole of `web.thread` exists to keep every touch of the app there. So the
test thread never holds the app: it holds `page`, and it reads the store
through its own connection (`harness.read_store`), the way every other
scenario asserts.
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

from otaku.backend import launch as backend_launch
from otaku.backend.api import Api, ModelRef, Refused
from otaku.backend.domain.app._paths import Paths
from otaku.console.banner import address
from otaku.settings.config import WebSettings
from otaku.store import Store
from otaku.web import serve
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
        # The story this page plays, as the page holds it: made by its
        # first line, None until then.
        self.story: int | None = None

    def stop(self) -> None:
        """Stop the server the way Ctrl+C does, and wait for it to be
        over — for the stories that are about what happens next."""
        self._stop.set()
        time.sleep(0.5)

    def get(self, path: str) -> Any:
        with urllib.request.urlopen(self.url + path, timeout=10, context=self.context) as reply:
            return self._read(reply)

    def post(
        self, path: str, body: dict[str, Any] | None = None, *, if_match: str | None = None
    ) -> Any:
        return self.send("POST", path, body, if_match=if_match)

    def patch(
        self, path: str, body: dict[str, Any] | None = None, *, if_match: str | None = None
    ) -> Any:
        return self.send("PATCH", path, body, if_match=if_match)

    def delete(
        self, path: str, body: dict[str, Any] | None = None, *, if_match: str | None = None
    ) -> Any:
        return self.send("DELETE", path, body, if_match=if_match)

    def send(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        if_match: str | None = None,
    ) -> Any:
        """Every write, by the method that says what it is — the METHOD
        is the lane the server answers on — naming the head it moves
        from where it moves one (`if_match`). A refusal raises, as any
        status but a success does: `refused` is for the stories about
        one."""
        return self.answer(method, path, body, if_match=if_match, refusing=False)[2]

    def answer(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        if_match: str | None = None,
        refusing: bool = True,
    ) -> tuple[int, dict[str, str], Any]:
        """The whole answer — the status, the headers by lowercase name,
        and the payload — a refusal's problem body included where
        `refusing`, for the stories that are about the ANSWER."""
        headers = {"Content-Type": "application/json"}
        if if_match is not None:
            headers["If-Match"] = if_match
        request = urllib.request.Request(
            self.url + path,
            data=None if method == "GET" else json.dumps(body or {}).encode(),
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30, context=self.context) as reply:
                found = {name.lower(): value for name, value in reply.headers.items()}
                return int(reply.status), found, self._read(reply)
        except urllib.error.HTTPError as refused:
            if not refusing:
                raise
            found = {name.lower(): value for name, value in refused.headers.items()}
            return int(refused.code), found, self._read(refused)

    def refused(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        if_match: str | None = None,
    ) -> dict[str, Any]:
        """A request the backend refuses: answered under the status its
        kind says, with the sentence in a problem body — never a fault."""
        status, headers, problem = self.answer(method, path, body, if_match=if_match)
        assert status in {404, 409, 412, 422, 502}, (status, problem)
        assert headers["content-type"].startswith("application/problem+json")
        assert problem["detail"], problem
        return dict(problem)

    def made(self) -> int:
        """The story this page plays, made now where there is none yet —
        as a first line or a card makes it on the page."""
        if self.story is None:
            self.story = int(self.post("/api/stories")["value"]["id"])
        return self.story

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

    def play(
        self,
        line: str,
        *,
        regenerate: bool = False,
        files: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        """One story line played on the story this page holds — made by
        it where there is none — and every event the page would have
        drawn; a fresh take on the story's last reply where `regenerate`.
        Played on the head as it stands (`If-Match: *`), as the page
        writes to a story it has not read. A refusal is the one item,
        its problem body."""
        story = self.made()
        path = f"/api/stories/{story}/" + ("regens" if regenerate else "messages")
        body = {} if regenerate else {"line": line, **({"files": files} if files else {})}
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "If-Match": "*"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30, context=self.context) as reply:
                return [json.loads(row[5:].strip()) for row in reply if row.startswith(b"data:")]
        except urllib.error.HTTPError as refused:
            if not refused.headers.get("Content-Type", "").startswith("application/problem"):
                raise
            return [json.loads(refused.read())]

    def messages(self) -> list[dict[str, Any]]:
        """The story this page plays, as the page reads it: its messages,
        root to head; [] with no story yet."""
        if self.story is None:
            return []
        return list(self.get(f"/api/stories/{self.story}/messages")["items"])

    def facts(self) -> dict[str, Any]:
        """The story this page plays as the page reads it (`Story`)."""
        return dict(self.get(f"/api/stories/{self.made()}"))

    @staticmethod
    def _read(reply: Any) -> Any:
        body = reply.read()
        kind = reply.headers.get("Content-Type", "")
        if kind.startswith(("application/json", "application/problem+json")):
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
    provider: str = PROVIDER,
) -> Iterator[Page]:
    """A served session a story opens for itself, for the ones the
    fixtures cannot cover: what is in the state dir BEFORE the launch is
    what the story is about, the server asks for a password, or the
    model must be served by a named engine (`provider` picks the client
    the registry builds, as the harness's `set_config_provider` does)."""
    yield from _serving(
        server,
        tmp_path,
        host=host,
        https=https,
        password=password,
        prepare=prepare,
        provider=provider,
    )


def _serving(
    server: ModelServer,
    tmp_path: Path,
    *,
    host: str,
    https: bool = False,
    password: str = "",
    prepare: Callable[[Path], None] | None = None,
    provider: str = PROVIDER,
) -> Iterator[Page]:
    root = tmp_path / "state"
    set_config_provider(root, server, name=provider)
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
            app = backend_launch.open_app(root)
            # A story that launches twice over one state dir finds the
            # model already remembered: an answer, never a fault.
            with contextlib.suppress(Refused):
                Api(app).app.update(model=ModelRef(provider, MODEL))
            try:
                web = Api(app).settings.web.get()
                serving.put(address(where))
                serve(
                    app,
                    # The password hash the launch made, the typed password
                    # having never reached anything past the file.
                    replace(where, password=web.config.password),
                    web.custom_dir,
                    web.cert_dir,
                    stop=stop,
                )
            finally:
                app.close()
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
