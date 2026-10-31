"""What every resource and every schema is built on, and what a write
takes and answers. A frontend reaches these names through `backend.api`,
which re-exports them; backend modules import them from here.

A `Resource` holds the App and nothing else — no story, no cache — and its
methods are the five verbs; a sub-resource is an attribute of its parent.
Every verb runs on the app's own thread whichever thread calls it
(`backend.app.thread`): `get` and `list` on the read lane, the others on the
write lane, a verb marked `streams` handing its events across, and one
marked `any_thread` on its caller's thread, never queued. A
`Schema` is what a resource answers: frozen data, every derived fact a
field; a `Report` is a schema with a text form, `text()`, which a terminal
prints and a page does not need. `UNCHANGED` is the default of every field
an update may leave alone, so that None can mean what the field says it
means — no pin, no story to resume. `Changed` is a write's answer: the thing
as it now stands (None where nothing remains of it, a deletion) and the
sentence that says what was done."""

from __future__ import annotations

import abc
import enum
import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar, dataclass_transform

if TYPE_CHECKING:
    from otaku.backend.app.core.services import App

T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

# ---------- the resources ----------

_VERBS = ("list", "get", "create", "update", "delete")
_READS = frozenset({"list", "get"})
# The verbs marked below, by their functions.
_ANY_THREAD_VERBS: set[Callable[..., Any]] = set()
_STREAM_VERBS: set[Callable[..., Any]] = set()


class Resource:
    """A path's resource: the App, and the verbs a subclass defines on it,
    each made to run on the app's thread when the subclass is defined."""

    def __init__(self, app: App) -> None:
        self._app = app

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for name in _VERBS:
            method = cls.__dict__.get(name)
            if method is None or method in _ANY_THREAD_VERBS:
                continue
            if method in _STREAM_VERBS:
                setattr(cls, name, _streamed(method))
            else:
                setattr(cls, name, _queued(method, reading=name in _READS))


def any_thread(method: F) -> F:
    """Marks a verb safe from any thread: it runs on its caller's, never
    queued — a read that touches no store, or one slow on purpose."""
    _ANY_THREAD_VERBS.add(method)
    return method


def streams(method: F) -> F:
    """Marks a verb whose answer is a reply's events: the reply runs on
    the app's thread and its events are handed across as they come."""
    _STREAM_VERBS.add(method)
    return method


def _queued(method: Callable[..., Any], *, reading: bool) -> Callable[..., Any]:
    @functools.wraps(method)
    def call(self: Resource, *args: Any, **kwargs: Any) -> Any:
        return self._app.run(lambda: method(self, *args, **kwargs), reading=reading)

    return call


def _streamed(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    def call(self: Resource, *args: Any, **kwargs: Any) -> Any:
        return self._app.stream(lambda: method(self, *args, **kwargs))

    return call


# ---------- what they answer ----------


# Every subclass is made a frozen dataclass here, when it is defined, so a
# schema carries no decorator of its own; `dataclass_transform` tells the
# type checkers the same, fields, constructor and immutability alike.
@dataclass_transform(frozen_default=True)
class Schema:
    """What a resource answers."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        dataclass(frozen=True)(cls)


class Report(Schema, abc.ABC):
    """A schema with a text form, the one a terminal prints."""

    __slots__ = ()

    @abc.abstractmethod
    def text(self) -> str:
        """The report as lines of text."""


class Unchanged(enum.Enum):
    """The type of `UNCHANGED`, for a field's annotation."""

    UNCHANGED = "unchanged"


# The default of a field an update leaves as it stands.
UNCHANGED = Unchanged.UNCHANGED


class Changed(Schema, Generic[T]):
    """A write's answer: `value`, the thing as it now stands (None where
    nothing remains of it, a deletion), and `notice`, the sentence to
    show."""

    value: T
    notice: str
