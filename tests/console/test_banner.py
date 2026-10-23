"""What the banner of a served session says about where it listens.

A launch states the address once, for the one host it was configured
with — so the cases that matter are the ones nobody runs.
"""

import pytest

from otaku.console.banner import WebFacts, _is_public, address, link_target, render_web


class TestAddress:
    """`banner.address` — the url the terminal prints and a reader pastes."""

    def test_the_scheme_follows_the_setting(self) -> None:
        assert address(_web()).startswith("http://")
        assert address(_web(is_https=True)).startswith("https://")

    def test_the_port_a_bare_scheme_already_means_is_not_printed(self) -> None:
        assert address(_web(port=80)) == "http://localhost"
        assert address(_web(port=443, is_https=True)) == "https://localhost"

    def test_the_other_scheme_s_default_is_a_port_like_any_other(self) -> None:
        # 443 means nothing to `http://`, and 80 means nothing to `https://`.
        assert address(_web(port=443)) == "http://localhost:443"
        assert address(_web(port=80, is_https=True)) == "https://localhost:80"

    def test_the_loopback_address_reads_as_localhost(self) -> None:
        assert address(_web(host="127.0.0.1")) == "http://localhost:9600"
        assert address(_web(host="localhost")) == "http://localhost:9600"

    def test_every_other_host_stays_as_configured(self) -> None:
        # A wildcard above all: it is public, and must not read as the
        # one address that is not.
        for host in ("0.0.0.0", "::", "::1", "192.168.1.5"):
            assert address(_web(host=host)) == f"http://{host}:9600", host

    def test_there_is_no_trailing_slash(self) -> None:
        assert not address(_web()).endswith("/")


class TestLinkTarget:
    """`banner.link_target` — the url a link to the page opens: the
    address, but a wildcard — every interface, no machine a browser can
    open — opened as `localhost`, where the page answers too."""

    def test_a_wildcard_opens_as_localhost(self) -> None:
        for host in ("0.0.0.0", "::", "[::]"):
            assert link_target(_web(host=host)) == "http://localhost:9600", host

    def test_every_other_host_opens_as_the_address_reads(self) -> None:
        for host in ("localhost", "127.0.0.1", "192.168.1.5", "otaku.local"):
            settings = _web(host=host)
            assert link_target(settings) == address(settings), host

    def test_the_scheme_and_the_port_are_the_addresses(self) -> None:
        assert link_target(_web(host="0.0.0.0", is_https=True)) == "https://localhost:9600"
        assert link_target(_web(host="0.0.0.0", port=80)) == "http://localhost"


class TestRenderWeb:
    """`banner.render_web` — whether the banner says the address is
    public: on a host that reaches past this machine without both TLS
    and a password; with both on, nothing is missing and nothing is
    said."""

    def test_the_address_is_said(self) -> None:
        facts = _web(host="192.168.1.5", is_https=True)
        assert address(facts) in render_web(facts)

    def test_a_loopback_host_is_not_said_to_be_public(self) -> None:
        for host in ("localhost", "127.0.0.1", "::1"):
            assert "public" not in render_web(_web(host=host)), host

    def test_any_other_host_is_said_to_be_public(self) -> None:
        for host in ("0.0.0.0", "::", "192.168.1.5", "otaku.local"):
            assert "public" in render_web(_web(host=host)), host

    def test_a_public_host_with_tls_and_a_password_gets_no_warning(self) -> None:
        # Both on is the configuration the file recommends: nothing is missing.
        assert "public" not in render_web(_web(host="0.0.0.0", is_https=True, password_set=True))
        # One of the two missing is still said.
        assert "no password" in render_web(_web(host="0.0.0.0", is_https=True))
        assert "no TLS" in render_web(_web(host="0.0.0.0", password_set=True))

    def test_a_password_is_noted(self) -> None:
        # On loopback, where no warning row speaks of one.
        assert "password" not in render_web(_web())
        assert "password" in render_web(_web(password_set=True))


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


def _web(
    host: str = "localhost", port: int = 9600, is_https: bool = False, password_set: bool = False
) -> WebFacts:
    """A served banner's facts: localhost:9600, plain, no password, but for
    what the case says."""
    return WebFacts(host=host, port=port, is_https=is_https, password_set=password_set)


@pytest.fixture(autouse=True)
def _plain(monkeypatch: pytest.MonkeyPatch) -> None:
    """No escape codes: what is asserted is what the banner says."""
    monkeypatch.setenv("NO_COLOR", "1")
