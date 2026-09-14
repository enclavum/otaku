"""Signing in to the page, told on the wire.

An otaku with no password asks for nothing and says so. One with a
password answers its page and its own files to anybody, and its API only
to a browser holding the cookie a right password earns — a cookie the
page may not read, named for the port, kept only as long as the reader
asked. What running the app cannot show is the HEADERS: which cookie was
set, with what attributes, and what a request without one was answered.
"""

import base64
import json
import time
import tomllib
from collections.abc import Iterator
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from otaku.backend.paths import Paths
from otaku.web import auth
from scenarios.support.server import ModelServer
from scenarios.web.conftest import Page, serving

PASSWORD = "hunter2"


class TestNoPassword:
    def test_an_otaku_with_no_password_asks_for_none(self, page: Page) -> None:
        assert _ask(page, "GET", "/api/login")[2] == {"required": False, "signed_in": False}
        assert _ask(page, "GET", "/api/session")[0] == 200

    def test_signing_in_to_it_is_a_refusal_and_sets_nothing(self, page: Page) -> None:
        code, headers, answer = _ask(page, "POST", "/api/login", body={"password": "anything"})
        assert (code, answer["refused"]) == (200, True)
        assert "set-cookie" not in headers


class TestSigningIn:
    def test_the_page_is_answered_and_the_api_is_not(self, guarded: Page) -> None:
        for asset in ("/", "/app.css", "/app.js", "/custom.css"):
            assert _ask(guarded, "GET", asset)[0] == 200, asset
        for path in ("/api/session", "/api/status", "/api/stories"):
            code, headers, _ = _ask(guarded, "GET", path)
            assert code == 401, path
            # With one, the browser would open its own credential dialog
            # over the page's.
            assert "www-authenticate" not in headers, path

    def test_the_page_is_told_a_password_is_asked_for(self, guarded: Page) -> None:
        assert _ask(guarded, "GET", "/api/login")[2] == {"required": True, "signed_in": False}

    def test_a_wrong_password_is_unauthorized_and_sets_no_cookie(self, guarded: Page) -> None:
        code, headers, answer = _ask(guarded, "POST", "/api/login", body={"password": "wrong"})
        assert (code, answer["refused"]) == (401, True)
        assert "set-cookie" not in headers
        assert "www-authenticate" not in headers

    def test_the_right_password_sets_a_cookie_the_page_cannot_read(self, guarded: Page) -> None:
        code, headers, answer = _ask(guarded, "POST", "/api/login", body={"password": PASSWORD})
        assert (code, answer) == (200, {})
        name, attributes = _cookie(headers)
        assert name == f"otaku-{urlsplit(guarded.url).port}"
        assert {"HttpOnly", "SameSite=Strict", "Path=/"} <= attributes
        # Plain http: a Secure cookie would be dropped on arrival.
        assert "Secure" not in attributes

    def test_not_staying_signed_in_lasts_four_hours(self, guarded: Page) -> None:
        # The token carries it, not only the cookie: a browser that keeps
        # its cookies past that still cannot keep anyone in.
        body = {"password": PASSWORD, "remember": False}
        headers = _ask(guarded, "POST", "/api/login", body=body)[1]
        assert f"Max-Age={auth.SHORT_LIFETIME}" in _cookie(headers)[1]
        assert _token_lasts(headers) == pytest.approx(auth.SHORT_LIFETIME, abs=5)

    def test_staying_signed_in_lasts_thirty_days(self, guarded: Page) -> None:
        body = {"password": PASSWORD, "remember": True}
        headers = _ask(guarded, "POST", "/api/login", body=body)[1]
        assert f"Max-Age={auth.LONG_LIFETIME}" in _cookie(headers)[1]
        assert _token_lasts(headers) == pytest.approx(auth.LONG_LIFETIME, abs=5)

    def test_the_cookie_opens_the_api(self, guarded: Page) -> None:
        cookie = _signed_in(guarded)
        assert _ask(guarded, "GET", "/api/session", cookie=cookie)[0] == 200
        assert _ask(guarded, "GET", "/api/login", cookie=cookie)[2] == {
            "required": True,
            "signed_in": True,
        }

    def test_a_cookie_that_does_not_parse_is_refused_not_crashed_on(self, guarded: Page) -> None:
        # Under this server's own name, so each one reaches the token
        # check rather than being passed over as somebody else's cookie.
        name = f"otaku-{urlsplit(guarded.url).port}"
        for value in ('"unterminated', "été.é.é", "a.b.c", ";;;="):
            cookie = f"{name}={value}"
            assert _ask(guarded, "GET", "/api/session", cookie=cookie)[0] == 401, cookie

    def test_signing_out_clears_the_same_cookie(self, guarded: Page) -> None:
        cookie = _signed_in(guarded)
        code, headers, answer = _ask(guarded, "DELETE", "/api/login", cookie=cookie)
        assert (code, answer) == (200, {})
        name, attributes = _cookie(headers)
        assert name == cookie.split("=", 1)[0]
        assert {"Max-Age=0", "Path=/", "HttpOnly", "SameSite=Strict"} <= attributes

    def test_signing_out_leaves_the_connection_fit_for_the_next_request(
        self, guarded: Page
    ) -> None:
        """What the page does: a DELETE carrying `{}`, then a reload down
        the same kept-alive connection. A body left unread would be the
        start of that next request."""
        cookie = _signed_in(guarded)
        where = urlsplit(guarded.url)
        assert where.hostname is not None and where.port is not None
        connection = HTTPConnection(where.hostname, where.port, timeout=10)
        try:
            connection.request(
                "DELETE", "/api/login", "{}", {"Content-Type": "application/json", "Cookie": cookie}
            )
            connection.getresponse().read()
            connection.request("GET", "/")
            reply = connection.getresponse()
            reply.read()
        finally:
            connection.close()
        assert reply.status == 200

    def test_a_page_of_another_origin_cannot_guess_at_the_password(self, guarded: Page) -> None:
        where = urlsplit(guarded.url)
        code, headers, _ = _ask(
            guarded,
            "POST",
            "/api/login",
            body={"password": PASSWORD},
            headers={"Origin": "http://elsewhere.example", "Host": where.netloc},
        )
        assert code == 403
        assert "set-cookie" not in headers


class TestThePasswordOnDisk:
    def test_a_typed_password_is_kept_as_a_hash(self, guarded: Page) -> None:
        config = Paths.resolve(guarded.root).config_file.read_text()
        stored = tomllib.loads(config)["web"]["password"]
        assert stored.startswith("$scrypt$")
        assert PASSWORD not in config

    def test_the_backup_that_edit_left_does_not_keep_it(self, guarded: Page) -> None:
        backups = sorted(Paths.resolve(guarded.root).config_backups_dir.glob("config-*.toml"))
        assert backups, "hashing the password is an edit, and an edit leaves a backup"
        for backup in backups:
            assert PASSWORD not in backup.read_text(), backup.name

    def test_changing_the_password_signs_everybody_out(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        with serving(server, tmp_path, password=PASSWORD) as before:
            token = _signed_in(before).split("=", 1)[1]
        with serving(server, tmp_path, password="another") as after:
            # The same token, under the cookie name this server reads.
            stale = f"otaku-{urlsplit(after.url).port}={token}"
            assert _ask(after, "GET", "/api/session", cookie=stale)[0] == 401
            code, _, refused = _ask(after, "POST", "/api/login", body={"password": PASSWORD})
            assert (code, refused["refused"]) == (401, True)
            signed_in = _ask(after, "POST", "/api/login", body={"password": "another"})
            assert "set-cookie" in signed_in[1]


class TestSigningInOverTls:
    def test_the_cookie_is_secure(self, server: ModelServer, tmp_path: Path) -> None:
        with serving(server, tmp_path, https=True, password=PASSWORD) as page:
            _, attributes = _cookie(
                _ask(page, "POST", "/api/login", body={"password": PASSWORD})[1]
            )
            assert "Secure" in attributes


@pytest.fixture
def guarded(server: ModelServer, tmp_path: Path) -> Iterator[Page]:
    """`otaku web` with `[web] password` set, over plain http."""
    with serving(server, tmp_path, password=PASSWORD) as page:
        yield page


def _ask(
    page: Page,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    cookie: str = "",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], Any]:
    """One request with the headers a browser would send, and the whole
    answer back — status, headers lower-cased, and the body parsed when
    it is JSON. urllib keeps no cookies and hides Set-Cookie well, so
    this goes underneath it."""
    where = urlsplit(page.url)
    assert where.hostname is not None and where.port is not None
    connection = (
        HTTPSConnection(where.hostname, where.port, timeout=10, context=page.context)
        if where.scheme == "https"
        else HTTPConnection(where.hostname, where.port, timeout=10)
    )
    sent = dict(headers or {})
    if body is not None:
        sent["Content-Type"] = "application/json"
    if cookie:
        sent["Cookie"] = cookie
    try:
        connection.request(method, path, json.dumps(body) if body is not None else None, sent)
        reply = connection.getresponse()
        raw = reply.read()
        answered = {name.lower(): value for name, value in reply.getheaders()}
    finally:
        connection.close()
    parsed = (
        json.loads(raw) if answered.get("content-type", "").startswith("application/json") else raw
    )
    return reply.status, answered, parsed


def _cookie(headers: dict[str, str]) -> tuple[str, set[str]]:
    """The cookie a Set-Cookie header sets: its name, and its attributes."""
    first, *attributes = headers["set-cookie"].split("; ")
    return first.split("=", 1)[0], set(attributes)


def _token_lasts(headers: dict[str, str]) -> float:
    """How many seconds from now the token a Set-Cookie carries expires —
    read off its own claims, which anyone holding it can read."""
    token = headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    claims = token.split(".")[1]
    expires = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))["exp"]
    return float(expires) - time.time()


def _signed_in(page: Page) -> str:
    """A browser through the password: the Cookie header it now sends."""
    headers = _ask(page, "POST", "/api/login", body={"password": PASSWORD})[1]
    return headers["set-cookie"].split(";", 1)[0]
