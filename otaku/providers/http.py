"""The one transport: three ways to ask a server, every failure mapped
onto `errors`. httpx lives here and nowhere else in the package, so no
caller ever sees a transport type.

Every call names the provider (`name`) — what a failure's sentence
names — and carries the headers the client composed. The timeout's
connect phase is capped on its own: a dead-but-routable host — a
mistyped LAN address, a firewalled port — hangs the handshake, and a
flat timeout would let it hold the whole read budget; a host that
listens at all completes the handshake in milliseconds, and a slow
answer is not a dead host.
"""

import contextlib
import json
from collections.abc import Generator
from typing import Any

import httpx

from otaku.formatting import printable
from otaku.providers.errors import ProviderError, StatusError, UnauthorizedError, UnreachableError

_DETAIL_WIDTH = 300  # of a server's explanation, how much a sentence carries
# The handshake's own cap (see the module docstring): a request that has
# to get through, a stream, and a best-effort probe that must not wait.
CONNECT_TIMEOUT = 2.0
STREAM_CONNECT_TIMEOUT = 5.0
QUIET_CONNECT_TIMEOUT = 1.0


def get_json(
    url: str,
    *,
    name: str,
    headers: dict[str, str],
    timeout: float,
    connect: float = CONNECT_TIMEOUT,
    quiet: bool = False,
) -> Any:
    """GET `url`, the answer parsed. Raises the error family — or,
    `quiet`, answers None to any failure: the best-effort native reads,
    where a server that will not say is itself an answer."""
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
    """POST `body`, the answer parsed. Raises the error family, or
    answers None when `quiet`, as `get_json`. A None timeout waits as
    long as the server takes — a model load."""
    return _request("POST", url, body, name, headers, timeout, connect, quiet)


def stream_events(
    url: str,
    body: dict[str, Any],
    *,
    name: str,
    headers: dict[str, str],
    timeout: float,
    connect: float = STREAM_CONNECT_TIMEOUT,
) -> Generator[dict[str, Any], None, None]:
    """POST `body` and yield the answer's SSE data events, one parsed
    object each, ending at `[DONE]`. An error status is raised before
    the first event, its explanation read while the stream is still
    open; anything else on the wire — comment lines, keepalives, a line
    that will not parse — is skipped, never fatal. Closing the generator
    closes the connection, which is how a consumer stops the server's
    generation."""
    started = False
    try:
        with httpx.stream(
            "POST", url, json=body, headers=headers, timeout=_timeout(timeout, connect)
        ) as response:
            if response.status_code >= 400:
                # Read now, while the stream is open: the explanation must
                # still be there once the connection closes.
                with contextlib.suppress(httpx.HTTPError):
                    response.read()
                _raise_for_status(response, name)
            for raw in response.iter_lines():
                line = raw.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    return
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    started = True
                    yield event
    except httpx.HTTPError as e:
        if started:
            raise UnreachableError(f"Lost the connection to {name}.") from e
        raise UnreachableError(f"Could not reach {name}.") from e


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
    """One request and its parsed answer, or the error its failure
    stands for — None for any of them when `quiet`, and then with the
    handshake capped at a second: a best-effort probe must not wait on
    a dead host the way a request that has to get through may."""
    if quiet:
        connect = QUIET_CONNECT_TIMEOUT
    try:
        response = httpx.request(
            method, url, json=body, headers=headers, timeout=_timeout(timeout, connect)
        )
        _raise_for_status(response, name)
        return response.json()
    except httpx.HTTPError as e:
        if quiet:
            return None
        raise UnreachableError(f"Could not reach {name}.") from e
    except ValueError as e:
        # The call returned, so `response` is bound: the body would not parse.
        if quiet:
            return None
        head = _excerpt(response.text, 20)
        raise StatusError(
            f"The answer from {name} is not JSON: {head!r}", response.status_code
        ) from e
    except ProviderError:
        if quiet:
            return None
        raise


def _raise_for_status(response: httpx.Response, name: str) -> None:
    status = response.status_code
    if status < 400:
        return
    if status in (401, 403):
        raise UnauthorizedError(f"The api key was rejected by {name}.")
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
