"""The page over TLS, and the pair it is served under.

`cert/` is the one directory otaku writes into exactly once: an empty
one is generated into, and everything else there is somebody's decision
that a launch must not overwrite. What running the app cannot show is
WHICH file went out on the wire, and what became of the one that was
already there.
"""

import ssl
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from otaku.web import ServeError
from otaku.web.cert import get_context
from scenarios.support.server import ModelServer
from scenarios.web.conftest import Page, serving


class TestServingOverTls:
    def test_a_pair_is_generated_and_the_page_is_served_under_it(self, secure: Page) -> None:
        assert (secure.root / "cert" / "cert.pem").exists()
        assert (secure.root / "cert" / "key.pem").exists()
        assert b"<title>otaku</title>" in secure.get("/")

    def test_the_certificate_on_disk_is_the_one_on_the_wire(self, secure: Page) -> None:
        assert _served(secure) == (secure.root / "cert" / "cert.pem").read_text().strip()

    def test_the_key_is_the_reader_s_alone(self, secure: Page) -> None:
        # The one file here that is a secret, and the directory around it.
        assert (secure.root / "cert" / "key.pem").stat().st_mode & 0o777 == 0o600
        assert (secure.root / "cert").stat().st_mode & 0o777 == 0o700

    def test_tls_changes_the_transport_and_nothing_above_it(self, secure: Page) -> None:
        # The same lanes and the same answers: a read is still answered
        # off the session, and the heartbeat still says both its things.
        assert secure.status("/api/stories") == 200
        assert set(secure.get("/api/status")) == {"status", "notices"}

    def test_a_plain_address_writes_no_certificate(self, page: Page) -> None:
        # Nothing to serve under means nothing to generate, and a state
        # dir that never asked for TLS must not grow a key.
        assert not (page.root / "cert").exists()


class TestThePairAlreadyThere:
    def test_a_pair_of_the_reader_s_own_is_served_and_left_alone(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """The whole posture of the directory: what is in it is what is
        served. A reader who dropped in mkcert's or Tailscale's pair
        keeps it, byte for byte."""
        theirs: dict[str, bytes] = {}

        def their_own(root: Path) -> None:
            get_context(root / "cert")
            for name in ("cert.pem", "key.pem"):
                theirs[name] = (root / "cert" / name).read_bytes()

        with serving(server, tmp_path, https=True, prepare=their_own) as page:
            assert _served(page) == theirs["cert.pem"].decode().strip()
            for name, was in theirs.items():
                assert (page.root / "cert" / name).read_bytes() == was, name

    def test_half_a_pair_refuses_to_serve(self, server: ModelServer, tmp_path: Path) -> None:
        """Either half alone is a reader mid-way through installing their
        own, or a crash of ours between two renames. Generating over it
        would take a private key with it, so the launch stops instead."""
        for gone in ("cert.pem", "key.pem"):

            def only_one(root: Path, gone: str = gone) -> None:
                get_context(root / "cert")
                (root / "cert" / gone).unlink()

            with (
                pytest.raises(ServeError),
                serving(server, tmp_path / gone, https=True, prepare=only_one),
            ):
                pass

    def test_a_pair_that_cannot_be_loaded_refuses_to_serve(
        self, server: ModelServer, tmp_path: Path
    ) -> None:
        """Both halves there and useless — a key from another pair, a
        truncated file. Still not ours to replace."""

        def mismatched(root: Path) -> None:
            get_context(root / "cert")
            get_context(root / "other")
            (root / "cert" / "key.pem").write_bytes((root / "other" / "key.pem").read_bytes())

        with pytest.raises(ServeError), serving(server, tmp_path, https=True, prepare=mismatched):
            pass


def _served(page: Page) -> str:
    """The certificate the server actually presents, as a browser would
    be offered it."""
    where = urlsplit(page.url)
    assert where.hostname is not None and where.port is not None
    return ssl.get_server_certificate((where.hostname, where.port)).strip()
