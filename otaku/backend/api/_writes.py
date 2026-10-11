"""What a write takes and answers. `UNCHANGED` is the default of every
field an update may leave alone, so that None can mean what the field
says it means — no pin, no story to resume. `Changed` is a write's
answer: the thing as it now stands (None where nothing remains of it, a
deletion) and the sentence that says what was done. A leaf: the
resource modules import it, and the root re-exports it."""

import enum
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


class Unchanged(enum.Enum):
    """The type of `UNCHANGED`, for a field's annotation."""

    UNCHANGED = "unchanged"


# The default of a field an update leaves as it stands.
UNCHANGED = Unchanged.UNCHANGED


@dataclass(frozen=True)
class Changed(Generic[T]):
    """A write's answer: `value`, the thing as it now stands (None where
    nothing remains of it, a deletion), and `notice`, the sentence to
    show."""

    value: T
    notice: str
