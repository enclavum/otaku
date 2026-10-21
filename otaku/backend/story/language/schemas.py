"""What `story.language.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.base import Schema
from otaku.backend.story.language import services as domain_language

# Re-exported: a line's story and its words with the model, and the
# sentence for a line none of the language frames.
from otaku.backend.story.language.services import PROSE_DESCRIPTION as PROSE_DESCRIPTION
from otaku.backend.story.language.services import spans as spans

__all__ = [
    "INLINERS",
    "OPENERS",
    "PROSE_DESCRIPTION",
    "Language",
    "LanguageRow",
    "is_option",
    "opener",
    "spans",
]


class Language(Schema):
    """The story's typed language: what a line with no framing does, the
    openers, the inliners."""

    prose: str
    openers: tuple[LanguageRow, ...]
    inliners: tuple[LanguageRow, ...]


class LanguageRow(Schema):
    """One word of the language and the shape of its argument as a help
    page spells it ("NAME: PROMPT", "TEXT"); a bracketed shape is
    optional."""

    token: str
    args: str


# The directions a line may open with, in the order a menu offers them.
OPENERS: tuple[LanguageRow, ...] = tuple(
    LanguageRow(token, args) for token, args in domain_language.OPENERS
)


# The inliners a line may carry after whitespace, each behind a `…`.
INLINERS: tuple[LanguageRow, ...] = tuple(
    LanguageRow(token, args) for token, args in domain_language.INLINERS
)


def opener(line: str) -> str:
    """The direction a typed line opens with — its token, "" for a line
    none opens."""
    return domain_language.opener(line)


def is_option(line: str) -> bool:
    """Whether a line of a question call is an answer to pick from — the
    rule the question's own reading parts it by, for a client drawing the
    call while it streams."""
    return domain_language.is_option(line)
