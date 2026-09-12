"""The one transport: three ways to ask a server, every failure mapped
onto `errors`. httpx lives here and nowhere else in the package, so no
caller ever sees a transport type.

Every call names the provider (`name`), for a failure's sentence, and
carries the headers the client composed. The handshake has a cap of
its own: a dead but routable host — a mistyped LAN address, a
firewalled port — hangs it, and a flat timeout would let it hold the
whole read budget, while a host that listens at all completes it in
milliseconds.
"""

import contextlib
from collections.abc import Generator
from typing import Any

import httpx

from otaku.formatting import printable
from otaku.providers.errors import ProviderError, StatusError, UnauthorizedError, UnreachableError

ASK_TIMEOUT = 10.0  # one question, one answer: a listing, a count, a balance
PROBE_TIMEOUT = 1.5  # a best-effort native read, never worth a turn's wait
LISTING_TIMEOUT = 5.0  # the picker's fan-out: one dead provider costs at most this
REPLY_TIMEOUT = 600.0  # a reply, read chunk by chunk for as long as a model takes

# The handshake's own cap, see the module docstring.
CONNECT_TIMEOUT = 2.0  # a request that has to get through
STREAM_CONNECT_TIMEOUT = 5.0  # a stream
QUIET_CONNECT_TIMEOUT = 1.0  # a best-effort probe, which must not wait

_DETAIL_WIDTH = 300  # how much of a server's explanation a sentence carries

# The sentences for what never reached the server, or never came back.
_UNSENDABLE = "A request to {name} could not be encoded: {detail}"
_SILENT = "{name} took the request but did not answer within {seconds:.0f} seconds."


def get_json(
    url: str,
    *,
    name: str,
    headers: dict[str, str],
    timeout: float,
    connect: float = CONNECT_TIMEOUT,
    quiet: bool = False,
) -> Any:
    """GET `url`, parsed. Raises the error family; `quiet` answers None
    to any failure instead, for the best-effort reads."""
    return _request("GET", url, None, name, headers, timeout, connect, quiet)


def post_json(
    url: str,
    body: dict[str, Any],
    *,
    name: str,
    headers: dict[str, str],
    timeout: float | None,
    connect: float = CONNECT_TIMEOUT,
    quiet: bool = False,
) -> Any:
    """POST `body`, parsed, as `get_json`; a None timeout waits as long
    as a model load takes."""
    return _request("POST", url, body, name, headers, timeout, connect, quiet)


def stream_lines(
    url: str,
    body: dict[str, Any],
    *,
    name: str,
    headers: dict[str, str],
    timeout: float,
    connect: float = STREAM_CONNECT_TIMEOUT,
) -> Generator[str, None, None]:
    """POST `body` and yield the answer's non-blank lines. An error
    status is raised before the first line; a transport failure is
    "could not reach" before the first line and "lost the connection"
    after; closing the generator closes the connection, which stops the
    server's generation. What the lines mean is the protocol's."""
    started = False
    try:
        with httpx.stream(
            "POST",
            url,
            json=body,
            headers=headers,
            timeout=_timeout(timeout, connect),
            follow_redirects=True,
        ) as response:
            if response.status_code >= 300:
                # Read now, while the stream is open: the explanation must
                # still be there once the connection closes.
                with contextlib.suppress(httpx.HTTPError):
                    response.read()
                _raise_for_status(response, name)
            # Split on newlines alone: httpx's own line splitting follows
            # str.splitlines and would cut a frame at a U+2028 or U+0085
            # an engine serialized raw, losing it.
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


def positive_int(value: object) -> int | None:
    """`value` when it is a positive int — how a count or a context size is
    taken off the untyped JSON a server answers with — else None."""
    return value if isinstance(value, int) and value > 0 else None


def _request(
    method: str,
    url: str,
    body: dict[str, Any] | None,
    name: str,
    headers: dict[str, str],
    timeout: float | None,
    connect: float,
    quiet: bool,
) -> Any:
    """One request, parsed. `quiet` answers None to any failure, with
    the handshake capped at a second: a best-effort probe must not wait
    on a dead host."""
    try:
        try:
            response = httpx.request(
                method,
                url,
                json=body,
                headers=headers,
                timeout=_timeout(timeout, connect if not quiet else QUIET_CONNECT_TIMEOUT),
                follow_redirects=True,
            )
            _raise_for_status(response, name)
            return response.json()
        except httpx.ReadTimeout as e:
            raise UnreachableError(_SILENT.format(name=name, seconds=timeout)) from e
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
    except ProviderError:
        if quiet:
            return None
        raise


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
