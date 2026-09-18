"""What the banner of a served session says about where it listens.

A launch states the address once, for the one host it was configured
with — so the cases that matter are the ones nobody runs.
"""

import pytest

from otaku.backend import WebSettings
from otaku.console.banner import _is_public, address, render_web


class TestAddress:
    """`banner.address` — the url the terminal prints and a reader pastes."""

    def test_the_scheme_follows_the_setting(self) -> None:
        assert address(WebSettings()).startswith("http://")
        assert address(WebSettings(https=True)).startswith("https://")

    def test_the_port_a_bare_scheme_already_means_is_not_printed(self) -> None:
        assert address(WebSettings(port=80)) == "http://localhost"
        assert address(WebSettings(port=443, https=True)) == "https://localhost"

    def test_the_other_scheme_s_default_is_a_port_like_any_other(self) -> None:
        # 443 means nothing to `http://`, and 80 means nothing to `https://`.
        assert address(WebSettings(port=443)) == "http://localhost:443"
        assert address(WebSettings(port=80, https=True)) == "https://localhost:80"

    def test_the_loopback_address_reads_as_localhost(self) -> None:
        assert address(WebSettings(host="127.0.0.1")) == "http://localhost:9600"
        assert address(WebSettings(host="localhost")) == "http://localhost:9600"

    def test_every_other_host_stays_as_configured(self) -> None:
        # A wildcard above all: it is public, and must not read as the
        # one address that is not.
        for host in ("0.0.0.0", "::", "::1", "192.168.1.5"):
            assert address(WebSettings(host=host)) == f"http://{host}:9600", host

    def test_there_is_no_trailing_slash(self) -> None:
        assert not address(WebSettings()).endswith("/")


class TestRenderWeb:
    """`banner.render_web` — whether the banner says the address is
    public: on every host that reaches past this machine, whatever else
    is set."""

    def test_the_address_is_said(self) -> None:
        where = WebSettings(host="192.168.1.5", https=True)
        assert address(where) in render_web(where)

    def test_a_loopback_host_is_not_said_to_be_public(self) -> None:
        for host in ("localhost", "127.0.0.1", "::1"):
            assert "public" not in render_web(WebSettings(host=host)), host

    def test_any_other_host_is_said_to_be_public(self) -> None:
        for host in ("0.0.0.0", "::", "192.168.1.5", "otaku.local"):
            assert "public" in render_web(WebSettings(host=host)), host

    def test_a_public_host_is_still_said_so_with_tls_and_a_password(self) -> None:
        # Secured is not the same as private.
        banner = render_web(WebSettings(host="0.0.0.0", https=True, password="hash"))
        assert "public" in banner and "no " not in banner

    def test_a_password_is_noted(self) -> None:
        # On loopback, where no warning row speaks of one.
        assert "password" not in render_web(WebSettings())
        assert "password" in render_web(WebSettings(password="hash"))


class TestPublic:
    """`banner._is_public` — whether an address reaches PAST this
    machine, which is what decides whether a launch says anything about
    what the address is missing."""

    def test_no_loopback_address_is_public(self) -> None:
        for host in ("127.0.0.1", "127.0.0.53", "127.255.255.254", "::1", "localhost"):
            assert not _is_public(host), host

    def test_a_bracketed_ipv6_address_is_read_inside_its_brackets(self) -> None:
        assert not _is_public("[::1]")

    def test_every_wildcard_is_public(self) -> None:
        # The whole reason this is not `server.LOOPBACK`: that set
        # answers what ARRIVES here, and a wildcard bind does arrive as
        # localhost — while being the most exposed address there is.
        for host in ("0.0.0.0", "::", ""):
            assert _is_public(host), host

    def test_an_address_on_the_network_is_public(self) -> None:
        for host in ("192.168.1.5", "10.0.0.7", "::ffff:c0a8:105"):
            assert _is_public(host), host

    def test_no_name_but_localhost_is_taken_on_trust(self) -> None:
        # Anything else would be a DNS call at the launch, and a name
        # that resolves to 127.0.0.1 somewhere else is not this machine.
        for host in ("otaku.local", "localhost.localdomain", "example.com"):
            assert _is_public(host), host


@pytest.fixture(autouse=True)
def _plain(monkeypatch: pytest.MonkeyPatch) -> None:
    """No escape codes: what is asserted is what the banner says."""
    monkeypatch.setenv("NO_COLOR", "1")
