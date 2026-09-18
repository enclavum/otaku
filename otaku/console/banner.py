"""The banner: what a session opens with in the shell that started it —
the mark beside three lines for a chat, where the page is for a served
session. Each ends on a blank row, which is what separates it from
whatever is printed under it. The mark is the one thing in colour; the
lines are the terminal's own text, dimmed but for what a reader looks
for — the name, the address, a warning.
"""

import ipaddress
import os
import sys
from dataclasses import dataclass
from typing import Protocol

from otaku import __version__
from otaku.console import BOLD, DIM, RESET
from otaku.formatting import format_context

# The mark: a house — お宅, "your house", is what the name means — three
# rows for the three lines beside it. Quadrant blocks — four pixels a
# cell — in one colour, so it is the same drawing with colour off. Drawn
# as it looks: a row's leading spaces count, its trailing ones are not
# needed — the rows are padded to the widest where they are printed.
_MARK = """
 ▄██▄
▀████▀
 █▌▐█
"""


@dataclass(frozen=True)
class SessionFacts:
    """What a chat banner states, as its caller reads them off the
    session it is opening — arriving ready to print: the banner draws
    them and nothing else, and asks no store and no provider anything."""

    model: str  # "(no model)" when none
    provider: str  # the provider serving it; "" when none
    max_context: int | None  # the context the model gets, when a LOCAL provider answers


class WebFacts(Protocol):
    """Where a served session listens and what it asks of whoever
    reaches it — the `[web]` settings slice, as its caller hands it
    over. A protocol rather than the type: `console` imports nothing
    above it, so it names the fields it reads and no more."""

    @property
    def host(self) -> str: ...
    @property
    def port(self) -> int: ...
    @property
    def https(self) -> bool: ...
    @property
    def password(self) -> str: ...  # a hash; "" is no password at all


@dataclass(frozen=True)
class _Style:
    """The banner's escape codes — or empty strings when colour is off."""

    mark: str = ""
    bold: str = ""
    dim: str = ""
    reset: str = ""


_COLOUR = _Style(
    # The page's favicon (#b06636, `web/static/index.html`), for the
    # favicon's reason: neither can ask what background it is on, and
    # this shade holds on either — 4.4:1 on white, 4.8:1 on black.
    mark="\x1b[38;2;176;102;54m",
    bold=BOLD,
    dim=DIM,
    reset=RESET,
)
_PLAIN = _Style()


def render_terminal(facts: SessionFacts) -> str:
    """The banner a chat session opens with: the mark, and beside it the
    name, the model with the context it gets, and the provider serving
    it — then where the commands are, which the prompt does not say."""
    style = _style()
    model = facts.model
    if facts.max_context:
        model += f" · {format_context(facts.max_context)} context"
    last = " · ".join(part for part in (facts.provider, "/help for commands") if part)
    said = [
        _name(style),
        f"{style.dim}{model}{style.reset}",
        f"{style.dim}{last}{style.reset}",
    ]
    marks = _MARK.strip("\n").split("\n")
    width = max(len(mark) for mark in marks)
    rows = [
        f"{style.mark}{mark.ljust(width)}{style.reset}  {text}"
        for mark, text in zip(marks, said, strict=True)
    ]
    return "\n".join([*rows, ""])


def render_web(where: WebFacts) -> str:
    """What a served session opens with, said the way a server says it:
    the name, where the page is and how to quit — no mark, and the model
    and the story are on the page itself. After the address, that a
    password is set; under it, on any host that reaches past this
    machine, that the address is public, with what it is missing — the
    one row of words that is not dimmed."""
    style = _style()
    note = f"{style.dim} (password set){style.reset}" if where.password else ""
    rows = [_name(style), f"{style.dim}Running on{style.reset} {address(where)}{note}"]
    if _is_public(where.host):
        missing = [
            name
            for name, on in (("no TLS", where.https), ("no password", where.password))
            if not on
        ]
        if missing:
            rows.append(f"Warning: public, yet with {' and '.join(missing)} - set in config.toml")
        else:
            rows.append("Warning: public")
    rows.append(f"{style.dim}Press CTRL+C to quit{style.reset}")
    return "\n".join([*rows, ""])


def _is_public(host: str) -> bool:
    """Whether this address reaches PAST this machine: every one but a
    loopback address.

    Not the complement of `web.server.LOOPBACK`, which is the wider
    question that one asks — every spelling that ARRIVES here, the
    wildcards among them, because a wildcard bind does answer as
    localhost too. Here `0.0.0.0` is the most exposed address there is,
    so it has to come out true, and a set that excuses it is the wrong
    set. Nor `is_private`, which is true of a wildcard and of every
    address on the LAN.

    A name is never resolved: that is a DNS call at the launch, and the
    only name taken on trust is the one everybody means by it."""
    name = host.strip("[]")
    if name == "localhost":
        return False
    try:
        return not ipaddress.ip_address(name).is_loopback
    except ValueError:
        return True


def address(where: WebFacts) -> str:
    """The URL the banner prints and a reader pastes: the host as
    configured, `127.0.0.1` read as `localhost`, the scheme's own port
    left unsaid."""
    scheme = "https" if where.https else "http"
    reachable = "localhost" if where.host == "127.0.0.1" else where.host
    if where.port == (443 if where.https else 80):
        return f"{scheme}://{reachable}"
    return f"{scheme}://{reachable}:{where.port}"


def _name(style: _Style) -> str:
    """What every banner opens with: the name and the version."""
    return f"{style.bold}otaku{style.reset} {style.dim}v{__version__}{style.reset}"


def _style() -> _Style:
    """The escape codes, or none — settled once per banner."""
    return _COLOUR if _colour() else _PLAIN


def _colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() or os.environ.get("OTAKU_COLOR") == "1"
