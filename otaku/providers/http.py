"""The one transport: `Http`, one per provider, asking a server three
ways — a GET, a POST, a stream — with every failure mapped onto
`errors` and filed with the sink. httpx lives here and nowhere else in
the package, so no caller ever sees a transport type.

The object carries what every call would otherwise repeat: the
provider's name, for a failure's sentence, and the headers every
request carries, composed once by the auth — the key in force is fixed
for a client's life, a changed section rebuilding the client. It holds
no state and is shared: the picker's fan-out
and the session's turn use one at the same time. What a SEQUENCE of
calls needs — one budget over llama.cpp's two reads or KoboldCpp's
five, rather than the full timeout for each, and one purpose to file a
failure under — is a `within` view: a copy under one deadline and one
purpose, a local of the method that runs the sequence, handed to what
it calls and never stored, so no method inherits another's clock.

A GET or a POST that fails is filed with the sink before it raises: the
provider, the purpose the request served and the exception, cause and
all, so a traceback can land in a log and the sentence in the request
log's status. A `quiet` failure is answered with None and filed nowhere
— best-effort reads are asked often, of servers that are often down. A
stream's failure is the caller's to file, past its own retry: a 400 the
completion half sends again without the reasoning knobs was no failure.
`record` is the one door for that, and for any sentence a caller makes
itself — a load that did not take, a key the engine's own check
rejected.

The handshake has a cap of its own: a dead but routable host — a
mistyped LAN address, a firewalled port — hangs it, and a flat timeout
would let it hold the whole read budget, while a host that listens at
all completes it in milliseconds.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, Protocol

import httpx

from otaku.formatting import printable
from otaku.providers.errors import ProviderError, StatusError, UnauthorizedError, UnreachableError

ASK_TIMEOUT = 10.0  # one question, one answer: a listing, a count, a balance
PROBE_TIMEOUT = 1.5  # a best-effort native read, never worth a turn's wait
LISTING_TIMEOUT = 5.0  # the picker's fan-out: one dead provider costs at most this
REPLY_TIMEOUT = 600.0  # a reply, read chunk by chunk for as long as a model takes

# The handshake's own cap, see the module docstring.
_CONNECT_TIMEOUT = 2.0  # a request that has to get through
_STREAM_CONNECT_TIMEOUT = 5.0  # a stream
_QUIET_CONNECT_TIMEOUT = 1.0  # a best-effort probe, which must not wait

_DETAIL_WIDTH = 300  # how much of a server's explanation a sentence carries

# The sentences for what never reached the server, or never came back.
_UNSENDABLE = "A request to {name} could not be encoded: {detail}"
_SILENT = "{name} took the request but did not answer within {seconds:.0f} seconds."
_SPENT = "{name} did not answer in time."


class ErrorSink(Protocol):
    """Where a failure is filed — a mirror of `logging.ErrorLog`, method
    for method: `record` appends the exception's traceback, its cause
    chained, under a `context` line, which the transport writes as the
    provider and the purpose the request served. Never raises."""

    def record(self, context: str, exc: BaseException) -> Path: ...


class Http:
    """One provider's transport, or a `within` view of it (see the
    module docstring). Every request has a purpose, the word a failure
    is filed under: the call's own, else the view's."""

    def __init__(
        self,
        name: str,
        headers: dict[str, str],
        error_sink: ErrorSink | None = None,
        *,
        purpose: str = "",
        deadline: float | None = None,
    ) -> None:
        self._name = name
        self._headers = headers
        self._error_sink = error_sink
        # A view's own: the purpose its calls serve, and the monotonic
        # instant its budget runs out; "" and None outside one.
        self._purpose = purpose
        self._deadline = deadline

    def within(self, timeout: float, purpose: str = "") -> Http:
        """A view for one sequence: this transport under one budget of
        `timeout` seconds from now, its calls filed under `purpose`
        unless they say otherwise. The remaining budget caps every call
        made through it — or the call's own cap, where that is shorter —
        and a call once it is spent fails as unreachable, unsent. Inside
        a view, the tighter of the two deadlines holds."""
        deadline = time.monotonic() + timeout
        if self._deadline is not None:
            deadline = min(deadline, self._deadline)
        return Http(
            self._name,
            self._headers,
            self._error_sink,
            purpose=purpose or self._purpose,
            deadline=deadline,
        )

    def get(
        self, url: str, *, purpose: str = "", timeout: float | None = None, quiet: bool = False
    ) -> Any:
        """GET `url`, parsed. `timeout` caps this one call — None leaves
        it to the budget, or to nothing outside one; `quiet` answers
        None to any failure instead of raising, for a best-effort read."""
        return self._request("GET", url, None, purpose, timeout, quiet)

    def post(
        self,
        url: str,
        body: dict[str, Any],
        *,
        purpose: str = "",
        timeout: float | None = None,
        quiet: bool = False,
    ) -> Any:
        """POST `body`, parsed, as `get`. With no cap and no budget it
        waits as long as a model load takes."""
        return self._request("POST", url, body, purpose, timeout, quiet)

    def stream(
        self, url: str, body: dict[str, Any], *, timeout: float
    ) -> Generator[str, None, None]:
        """POST `body` and yield the answer's non-blank lines. `timeout`
        is the longest silence the read waits out, never a budget: a
        reply runs for as long as the model writes (a view's remaining
        budget bounds the silence too). An error status is raised
        before the first line; a transport failure is "could not reach"
        before the first line and "lost the connection" after; closing
        the generator closes the connection, which stops the server's
        generation. What the lines mean is the protocol's, and a failure
        is the caller's to file."""
        name = self._name
        started = False
        try:
            with httpx.stream(
                "POST",
                url,
                json=body,
                headers=self._headers,
                timeout=_timeout(self._cap(timeout), _STREAM_CONNECT_TIMEOUT),
                follow_redirects=True,
            ) as response:
                if response.status_code >= 300:
                    # Read now, while the stream is open: the explanation
                    # must still be there once the connection closes.
                    with contextlib.suppress(httpx.HTTPError):
                        response.read()
                    _raise_for_status(response, name)
                # Split on newlines alone: httpx's own line splitting
                # follows str.splitlines and would cut a frame at a U+2028
                # or U+0085 an engine serialized raw, losing it.
                rest = ""
                for text in response.iter_text():
                    rest += text
                    while (cut := rest.find("\n")) != -1:
                        line, rest = rest[:cut], rest[cut + 1 :]
                        if line := line.strip():
                            started = True
                            yield line
                if line := rest.strip():
                    started = True
                    yield line
        except httpx.ReadTimeout as e:
            raise UnreachableError(_SILENT.format(name=name, seconds=timeout)) from e
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            if started:
                raise UnreachableError(f"Lost the connection to {name}.") from e
            raise UnreachableError(f"Could not reach {name}.") from e
        except UnicodeEncodeError as e:
            raise ProviderError(_UNSENDABLE.format(name=name, detail=e)) from e

    def record(self, exc: ProviderError, purpose: str = "") -> None:
        """File `exc` with the error sink under `purpose` — the call's
        own, else the view's — the way the transport files its own."""
        purpose = purpose or self._purpose
        if not purpose:
            raise ValueError("a failure needs a purpose to be filed under")
        if self._error_sink is not None:
            self._error_sink.record(f"{self._name} [{purpose}]", exc)

    def _request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None,
        purpose: str,
        timeout: float | None,
        quiet: bool,
    ) -> Any:
        """One request, parsed, its failure filed under `purpose` — the
        call's own, else the view's. `quiet` answers None to any failure
        instead, with the handshake capped at a second: a best-effort
        probe must not wait on a dead host — and files nothing, so it
        needs no purpose."""
        if not (purpose or self._purpose) and not quiet:
            raise ValueError("a request needs a purpose")  # nothing to file a failure under
        name = self._name
        try:
            try:
                capped = self._cap(timeout)
                response = httpx.request(
                    method,
                    url,
                    json=body,
                    headers=self._headers,
                    timeout=_timeout(capped, _QUIET_CONNECT_TIMEOUT if quiet else _CONNECT_TIMEOUT),
                    follow_redirects=True,
                )
                _raise_for_status(response, name)
                return response.json()
            except httpx.ReadTimeout as e:
                raise UnreachableError(_SILENT.format(name=name, seconds=capped)) from e
            except (httpx.HTTPError, httpx.InvalidURL) as e:
                # InvalidURL is httpx's own for a url that cannot be spelled
                # (a port with a letter in it) — a section's mistake, and a
                # sentence, never a traceback.
                raise UnreachableError(f"Could not reach {name}.") from e
            except UnicodeEncodeError as e:
                # Raised before anything is sent: a header carries ASCII only.
                raise ProviderError(_UNSENDABLE.format(name=name, detail=e)) from e
            except ValueError as e:
                # The call returned, so `response` is bound: the body would not parse.
                head = _excerpt(response.text, 20)
                raise StatusError(
                    f"The answer from {name} is not JSON: {head!r}", response.status_code
                ) from e
        except ProviderError as e:
            if quiet:
                return None
            self.record(e, purpose)
            raise

    def _cap(self, timeout: float | None) -> float | None:
        """`timeout` under the budget: the shorter of the two, or the one
        there is. Raises UnreachableError once the budget is spent."""
        if self._deadline is None:
            return timeout
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise UnreachableError(_SPENT.format(name=self._name))
        return remaining if timeout is None else min(timeout, remaining)


def positive_int(value: object) -> int | None:
    """`value` when it is a positive int — how a count or a context size is
    taken off the untyped JSON a server answers with — else None."""
    return value if isinstance(value, int) and value > 0 else None


def _raise_for_status(response: httpx.Response, name: str) -> None:
    """Anything but a 2xx is a refusal — a redirect included, when one
    is still here after following: it named nowhere to go. Only a 401
    is the key's fault: a 403 is the request refused — a gated model, a
    policy — on the catalogs, and every engine answers a wrong key
    with 401."""
    status = response.status_code
    if status < 300:
        return
    if status == 401:
        raise UnauthorizedError(f"The api key was rejected by {name}.")
    detail = ""
    with contextlib.suppress(httpx.StreamError):
        # A body that could not be read (the connection dropped mid-body)
        # is no explanation, and no reason to leave the error family.
        detail = _excerpt(response.text, _DETAIL_WIDTH)
    raise StatusError(
        f"Refused by {name} with HTTP {status}" + (f": {detail}" if detail else "."), status
    )


def _excerpt(text: str, width: int) -> str:
    """The start of a server's text as a sentence can carry it: one line,
    printable, at most `width` characters."""
    return printable(" ".join(text.split()))[:width]


def _timeout(total: float | None, connect: float) -> httpx.Timeout:
    if total is None:
        return httpx.Timeout(None, connect=connect)
    return httpx.Timeout(total, connect=min(connect, total))
