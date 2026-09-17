"""Where the page is, and who else can reach it.

Two answers `web.run` gives before anything is bound: the address a
reader is handed, and whether that address reaches more than this
machine. A launch states each of them once, for the one host it was
configured with — so the cases that matter are the ones nobody runs.
"""

from otaku.backend import WebSettings
from otaku.web.run import _is_loopback, address, address_notes


class TestAddress:
    """`web.run.address` — the url the terminal prints and a reader pastes."""

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


class TestAddressNotes:
    """`web.run.address_notes` — whether the banner says the address is
    public: on every host but localhost and 127.0.0.1, whatever else is
    set."""

    def test_a_loopback_host_gets_no_note(self) -> None:
        for host in ("localhost", "127.0.0.1"):
            assert address_notes(WebSettings(host=host)) == "", host

    def test_any_other_host_is_said_to_be_public(self) -> None:
        for host in ("0.0.0.0", "::", "::1", "192.168.1.5"):
            assert "public" in address_notes(WebSettings(host=host)), host

    def test_a_public_host_is_still_said_so_with_tls_and_a_password(self) -> None:
        # Secured is not the same as private.
        notes = address_notes(WebSettings(host="0.0.0.0", https=True, password="hash"))
        assert "public" in notes and "no " not in notes


class TestLoopback:
    """`web.run._is_loopback` — whether an address reaches THIS MACHINE
    only, which is what decides whether a launch says anything about
    what the address is missing."""

    def test_every_loopback_address_is_one(self) -> None:
        for host in ("127.0.0.1", "127.0.0.53", "127.255.255.254", "::1", "localhost"):
            assert _is_loopback(host), host

    def test_a_bracketed_ipv6_address_is_read_inside_its_brackets(self) -> None:
        assert _is_loopback("[::1]")

    def test_no_wildcard_is_loopback(self) -> None:
        # The whole reason this is not `server.LOOPBACK`: that set
        # answers what ARRIVES here, and a wildcard bind does arrive as
        # localhost — while being the most exposed address there is.
        for host in ("0.0.0.0", "::", ""):
            assert not _is_loopback(host), host

    def test_an_address_on_the_network_is_not(self) -> None:
        for host in ("192.168.1.5", "10.0.0.7", "::ffff:c0a8:105"):
            assert not _is_loopback(host), host

    def test_no_name_but_localhost_is_taken_on_trust(self) -> None:
        # Anything else would be a DNS call at the launch, and a name
        # that resolves to 127.0.0.1 somewhere else is not this machine.
        for host in ("otaku.local", "localhost.localdomain", "example.com"):
            assert not _is_loopback(host), host
