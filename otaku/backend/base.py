"""What every resource and every schema is built on, and what a write
takes and answers. A frontend reaches these names through `backend.api`,
which re-exports them; backend modules import them from here.

A `Resource` holds the App and nothing else — no story, no cache — and its
methods are the five verbs; a sub-resource is an attribute of its parent. A
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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar, dataclass_transform

if TYPE_CHECKING:
    from otaku.backend.app.core.services import App

T = TypeVar("T")


class Resource:
    """A path's resource: the App, and the verbs a subclass defines on it."""

    def __init__(self, app: App) -> None:
        self._app = app


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
