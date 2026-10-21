"""What a request carries and what an answer is made of, with no HTTP
in sight: `Ask` — what the path named, what the query asked for, what
the body carried, and the tags a write's `If-Match` names — and the
answers beside a plain payload: `Created` for a write that made
something, `Accepted` for one that started work still running, `Tagged`
for an answer about one story told with the head it stands at, `Blob`
for a read that answers with bytes. `plain` is the JSON of a
representation and `changed` of a write's answer; `PreconditionRequired`
is the fault a row may raise itself; `Route`
and `Stream` are the two shapes a table's row takes. `web.server` takes
a request apart into these and carries the result back."""

import enum
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from typing import Any

from otaku.backend.api import Api, Changed, InjectionPosition
from otaku.backend.story.messages.schemas import PlayEvent
from otaku.formatting import Money


@dataclass(frozen=True)
class Ask:
    """One request as a handler sees it, with no HTTP in sight: what the
    PATH named, what the query asked for, what the body carried, and the
    `If-Match` a write that moves a story's head names its head by (None
    where the request sent none). `web.server` takes a request apart and
    hands over these four."""

    params: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    body: Mapping[str, Any] = field(default_factory=dict)
    if_match: str | None = None

    def id(self, name: str) -> int:
        """One numeric path segment. The router has already matched the
        template, so a value that is not a number is a bug here, not a
        request to refuse."""
        return int(self.params[name])

    def text(self, name: str, default: str = "") -> str:
        """A body field the request may leave out."""
        return str(self.body.get(name, default))

    def need(self, name: str) -> str:
        """A body field the request MUST carry. Missing is a malformed
        request — the server answers 400 — and never a silent default:
        a PATCH with no text would blank what it was meant to correct.
        A field sent as `null` is the same fault wearing a value, and is
        refused for the same reason: coerced, it would store the literal
        title "None"."""
        value = self.body[name]
        if value is None:
            raise TypeError(f"{name} is null")
        return str(value)

    def flag(self, name: str) -> bool:
        """A body field that is a JSON boolean; anything else, a string
        "true" included, is a malformed request."""
        value = self.body[name]
        if not isinstance(value, bool):
            raise TypeError(f"{name} is not true or false")
        return value

    def integer(self, name: str) -> int:
        """A body field that is a JSON integer; a boolean, a string or a
        null is a malformed request."""
        return checked_integer(self.body[name], name)

    def strings(self, name: str) -> tuple[str, ...]:
        """A body field that is a JSON list of strings, () where the body
        leaves it out; anything else is a malformed request."""
        return checked_strings(self.body.get(name, []), name)

    def query_flag(self, name: str, default: bool) -> bool:
        """A query parameter that is `true` or `false`; `default` where
        the query leaves it out, a malformed request for any other
        word."""
        raw = self.query.get(name)
        if raw is None:
            return default
        if raw not in ("true", "false"):
            raise ValueError(f"{name} is not true or false")
        return raw == "true"

    def query_id(self, name: str) -> int | None:
        """A query parameter that is a row id; None where the query
        leaves it out, a malformed request where it is no number."""
        raw = self.query.get(name)
        if raw is None:
            return None
        if not raw.isdigit():
            raise ValueError(f"{name} is not an id")
        return int(raw)


@dataclass(frozen=True)
class Created:
    """A write that MADE something: its answer, and where the thing now
    lives. The server answers `201` and names it in `Location`."""

    answer: Any
    location: str


@dataclass(frozen=True)
class Accepted:
    """A write that STARTED work that goes on after it is answered — a
    forced extraction pass: its answer, and where the work is polled.
    The server answers `202` and names it in `Location`."""

    answer: Any
    location: str


@dataclass(frozen=True)
class Tagged:
    """An answer about one story, and the head the story stands at once
    it is given: the server says it as the `ETag` (`etag`), the tag a
    write that moves the head names in `If-Match`. `answer` is anything
    else a row returns."""

    answer: Any
    head: int | None


@dataclass(frozen=True)
class Blob:
    """A read that answers with BYTES rather than JSON — a picture, its
    thumbnail — and the media type to say. A file never changes once
    written, so the server lets the browser that asked keep it — that
    browser alone, as it is behind the sign-in: the one exception beside
    the fonts to "nothing is cached"."""

    data: bytes
    media_type: str


class PreconditionRequired(Exception):  # noqa: N818 — a 428 is the client's fault, not ours
    """A write that moves a story's head, asked without `If-Match`: the
    client owed the head it last read. A fault — the server answers
    `428` — not a refusal: the page that sent it is wrong, not the
    reader."""


def checked_integer(value: object, name: str) -> int:
    """`value`, a JSON integer the body names `name` — at its top or
    inside an object; a boolean, a string or a null is a malformed
    request."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} is not an integer")
    return value


def checked_strings(value: object, name: str) -> tuple[str, ...]:
    """`value`, a JSON list of strings the body names `name`; anything
    else is a malformed request."""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"{name} is not a list of strings")
    return tuple(value)


def etag(head: int | None) -> str:
    """The entity tag of a story standing at `head`: its newest turn's
    id, quoted — `"0"` while it has none."""
    return f'"{head or 0}"'


def landed_story(story_id: int) -> str:
    """Where a story made, copied or imported now lives — the `Location`
    a `Created` names."""
    return f"/api/stories/{story_id}"


def plain(value: object) -> Any:
    """A representation as JSON writes it: a dataclass as an object of
    its fields, an enum by its value, a sequence as an array (a set's
    members sorted), a mapping as an object keyed by its keys' plain
    form, a datetime in ISO 8601 — and two values whose JSON says more
    than their fields: a position by its value and the text every
    frontend names it by, an amount of money by its figure (a string: a
    decimal must not become a float), its currency and its text."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, enum.Enum):
        return plain(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, InjectionPosition):
        return {"value": value.value, "text": value.text}
    if isinstance(value, Money):
        return {"amount": str(value.amount), "currency": value.currency, "text": str(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return {each.name: plain(getattr(value, each.name)) for each in fields(value)}
    if isinstance(value, Mapping):
        return {str(plain(key)): plain(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        return sorted(plain(item) for item in value)
    if isinstance(value, list | tuple):
        return [plain(item) for item in value]
    raise TypeError(f"no JSON for {type(value).__name__}")


def changed(answer: Changed[Any]) -> dict[str, Any]:
    """A write's answer as JSON: the thing as it now stands (`value`,
    null where nothing stands — a delete, a send) and the sentence that
    says what was done (`notice`)."""
    return {"value": plain(answer.value), "notice": answer.notice}


# The two shapes a table's row takes. A route's work: the interface, and
# the request taken apart — what it returns is what the page gets: a
# payload, or one of the answers above. A stream's: the same two, and
# the reply's events, validated before the first one is asked for.
Route = Callable[[Api, Ask], Any]
Stream = Callable[[Api, Ask], Iterator[PlayEvent]]
