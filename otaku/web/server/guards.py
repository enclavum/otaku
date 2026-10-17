"""The three guards in front of every request, and the sign-in behind
the third: the `Host` must name this machine (else 421), a WRITE must
prove it came from otaku's own page (else 403), and where a password is
set the request must carry a good credential (else 401). Which headers
count, and why a request carrying neither of the first two is left to
the bind, are in `_from_this_machine` and `_from_our_page`; what is
answered without a credential is `OPEN`. A change to any of them is a
change to what a LAN can do to this server."""

import http.cookies
from typing import Any

from otaku.web.api import Conflict, Invalid, Refused
from otaku.web.server import auth
from otaku.web.server.assets import ASSETS, CUSTOM_FONTS
from otaku.web.server.base import RETRY_AFTER, Server, Wire
from otaku.web.server.streams import WATCH

# Signing in, as one address with three methods: GET says whether a
# password is asked for and whether this browser is through it, POST takes
# the password and sets the cookie, DELETE takes the cookie away. Answered
# without a credential, since the first two are how one is got and the
# third has nothing to protect.
LOGIN = "/api/login"

# The challenge a 401 carries, as HTTP asks every 401 to: a scheme of
# otaku's own, which no browser implements — so none opens its credential
# dialog over the page, which has a sign-in of its own.
_CHALLENGE = 'Cookie realm="otaku"'

# What is answered without a credential at all, and why each is: the
# packaged page and the reader's own files carry nothing of anybody's, and
# the page has to LOAD before it can ask for a password; the watch stream
# names only files the page is made of, and a refusal would end an
# EventSource for good rather than let it retry once the reader is in.
# `/api/app/status` is deliberately not here — it says what the background
# worker is doing, which is the app's.
OPEN = set(ASSETS) | {"/custom.css", WATCH, LOGIN}


class Guards(Wire):
    """The handler's guard half — `_admitted` is the whole of it, the
    rest are the guards it runs and the sign-in that satisfies the
    last."""

    def _admitted(self, path: str) -> bool:
        """Whether this request may be answered at all — the same guards,
        in the same order, for every method, each of them answering its
        own refusal. What differs between a read and a write is decided
        INSIDE the guard it concerns (`_from_our_page`), not by which
        methods call which guards. The login is a request like any other
        here — a page of another origin must not get to guess at the
        password — and is let through only by `_authorized`."""
        return (
            self._still_serving()
            and self._from_this_machine()
            and self._from_our_page()
            and self._authorized(path)
        )

    def _still_serving(self) -> bool:
        """Whether this server is still the one to ask. A stopped server
        closes its LISTENING socket, but a page's pooled keep-alive
        connections outlive that — its heartbeat would go on being
        answered 200 by handler threads of a server that is gone, and
        the page would never learn it (sharpest after `/web` hands the
        app back to the chat, where the process lives on). Answered
        503 with the connection closed, so the page's next attempt has
        to reconnect — which is what fails, and what tells it."""
        if not self.server.stopping.is_set():
            return True
        self._fault(503, headers=(("Retry-After", RETRY_AFTER),))
        return False

    def _signed_in(self) -> None:
        """What a page needs before it draws anything: whether this otaku
        asks for a password at all, and whether this browser is already
        through it. The page cannot tell the second for itself — the
        cookie is `HttpOnly`, which is the point of it."""
        password_hash = self.server.password_hash
        self._json(
            {
                "required": password_hash is not None,
                "signed_in": password_hash is not None
                and auth.verify_token(self._cookie_token(), password_hash),
            }
        )

    def _sign_in(self, body: dict[str, Any]) -> None:
        """A password in, a cookie out — the only way to get one.

        Answered on THIS thread and in neither lane: it never touches the
        app, and the derivation below would otherwise queue behind a
        reply and stop the story for as long as it takes.

        The typed password is checked by the backend (`hooks.check_password`)
        against the stored hash, never against a password: there is none in
        the file to compare with. That check is slow and memory-hard by
        design, and its cost is the whole of the rate limiting — a couple
        of dozen guesses a second, and no state to keep to do it.

        A wrong password is an authentication failure and answers 401, with
        the sentence as its `detail`. Signing in where no password is set
        is not: nothing is wrong with the caller, so that is a refusal,
        409."""
        password_hash = self.server.password_hash
        if password_hash is None:
            # The backend's own refusal (`api.settings.check_password`),
            # copied: a server started with no password has no hash to sign
            # a token with, whatever the file says since.
            self._outcome(Conflict("This otaku asks for no password."))
            return
        try:
            self.server.hooks.check_password(str(body.get("password", "")))
        except Invalid as e:
            self._problem(401, str(e), headers=(("WWW-Authenticate", _CHALLENGE),))
            return
        except Refused as e:
            self._outcome(e)
            return
        # The cookie lasts as long as the token in it, so the browser never
        # holds one the server would refuse.
        lifetime = auth.LONG_LIFETIME if body.get("remember") is True else auth.SHORT_LIFETIME
        self._set_cookie(auth.generate_token(password_hash, lifetime), lifetime)

    def _sign_out(self) -> None:
        """The cookie taken away. Only the server can: the page is not
        allowed to see it, let alone clear it. A stateless token copied
        elsewhere stays good until it runs out — this is about what the
        browser keeps offering, not about the credential."""
        self._set_cookie("", 0)

    def _set_cookie(self, value: str, age: int) -> None:
        """The answer to a sign-in or a sign-out: `{}`, carrying the one
        `Set-Cookie` this server ever sends, set and cleared alike — the
        attributes must match for a clearing to reach the cookie it means.

        Named for the PORT, because a browser scopes cookies by host and
        not by port: two otakus on one machine would otherwise take each
        other's. `Secure` only under TLS, since a browser drops a secure
        cookie that arrives over plain http. An age of 0 clears it."""
        parts = [f"{_cookie_name(self.server)}={value}", "Path=/", "HttpOnly", "SameSite=Strict"]
        if self.server.tls_context is not None:
            parts.append("Secure")
        parts.append(f"Max-Age={age}")
        self._json({}, headers=(("Set-Cookie", "; ".join(parts)),))

    def _from_this_machine(self) -> bool:
        """Whether the request came to an address this server answers to.

        The reader's own browser sends the address they typed; a page
        that got here by pointing its own hostname at 127.0.0.1 — DNS
        rebinding, the one attack a loopback bind does not stop — sends
        that hostname instead. Checking it costs nothing and is the only
        thing standing between a story library and any tab in the
        browser. A request with no Host at all is HTTP/1.0 or a script:
        allowed, because neither is a page."""
        answers_to = self.server.answers_to
        host = self.headers.get("Host", "")
        if not answers_to or not host or _named(host) in answers_to:
            return True
        self._fault(421)
        return False

    def _authorized(self, path: str) -> bool:
        """Whether whoever is asking has shown they know the password.

        True for everyone when none is set, which is the default and the
        whole of what a loopback otaku ever needed. `OPEN` says what is
        answered anyway, and the reader's own typefaces go with their
        stylesheet — both are files they put there themselves.

        401 with a challenge in a scheme no browser implements
        (`_CHALLENGE`): a Basic one would open the browser's own credential
        dialog over the page, which has a login of its own and a name for
        what it is asking for."""
        password_hash = self.server.password_hash
        if password_hash is None or path in OPEN or path.startswith(CUSTOM_FONTS):
            return True
        if auth.verify_token(self._cookie_token(), password_hash):
            return True
        self._fault(401, headers=(("WWW-Authenticate", _CHALLENGE),))
        return False

    def _cookie_token(self) -> str:
        """The token off this server's cookie — "" when there is none, or
        when the header does not parse, which `auth.verify_token` refuses like
        any other non-token. Anything that can reach the port can send a
        Cookie header, so a malformed one is an ordinary refusal and not
        a crash."""
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            return ""
        morsel = jar.get(_cookie_name(self.server))
        return morsel.value if morsel is not None else ""

    def _from_our_page(self) -> bool:
        """Whether a WRITE came from otaku's own page.

        A cross-origin form can POST here without a preflight, and a
        write needs no answer to do its damage: repointing a provider at
        an attacker sends the reader's api key with the next turn. So a
        write must prove where it came from — `Sec-Fetch-Site:
        same-origin`, which no page can forge, or an `Origin` that is
        exactly the one this request was addressed to.

        The Origin arm carries the weight, because fetch metadata is
        only sent for a TRUSTWORTHY url: `http://localhost` is one,
        `http://192.168.1.5:9600` is not — and that is the very
        configuration where another app, one port over on the same host,
        can reach this port. So the whole origin is compared, scheme and
        port included.

        A request with neither header is not a browser (curl, a script
        on this machine); the bind is what guards those.

        A READ needs no proof: a page of another origin can make the
        browser send one but cannot see the answer, and a GET moves
        nothing (the METHOD is the lane). Asking it of reads would refuse
        the reader following a link to otaku from another site, whose
        document request arrives `cross-site`."""
        if self.command == "GET":
            return True
        site = self.headers.get("Sec-Fetch-Site")
        if site in ("same-origin", "none"):
            return True
        origin = self.headers.get("Origin")
        if site is None and origin is None:
            return True
        addressed = self.headers.get("Host", "")
        if origin in (f"http://{addressed}", f"https://{addressed}"):
            return True
        self._fault(403)
        return False


def _named(host: str) -> str:
    """The name half of a Host header — the port dropped, brackets off.
    `[::1]:9600` and a bare `[::1]` are the same machine."""
    name = host.rsplit(":", 1)[0] if ":" in host.rsplit("]", 1)[-1] else host
    return name.strip("[]")


def _cookie_name(server: "Server") -> str:
    """This server's cookie: one per port, since the browser keeps one
    jar per host whatever the port."""
    return f"otaku-{server.server_address[1]}"
