"""The story's language, as a client applies it to text not stored yet —
a line being typed, a reply still arriving: the one exception to the
interface's rule that a client calls the resources and nothing else,
since no entity can carry text that has not reached the backend. The
page keeps its own copy, in its own language, of the rows
`LanguageResource` hands it.

The language is the syntax sugar a played line may carry, the same in
both frontends: the directions a line may OPEN with (`OPENERS`) and the
inliners it may carry INSIDE (`INLINERS`), each a token and the shape of
its argument, declared once in `context.syntax`; what a line with none
of them does (`PROSE_DESCRIPTION`); the direction a line opens with
(`opener`); a line's story and its words with the model (`spans`); and
whether a line of a question still streaming is one of its options
(`is_option`). Commands are no part of it: a slash word that opens
nothing is the terminal's to answer (`terminal.commands`), and the page
has buttons.
"""

from __future__ import annotations

from dataclasses import dataclass

from otaku.backend.domain.app import App
from otaku.backend.domain.story import language as domain_language

# Re-exported: a line's story and its words with the model, and the
# sentence for a line none of the language frames.
from otaku.backend.domain.story.language import PROSE_DESCRIPTION as PROSE_DESCRIPTION
from otaku.backend.domain.story.language import spans as spans

__all__ = [
    "INLINERS",
    "OPENERS",
    "PROSE_DESCRIPTION",
    "Language",
    "LanguageResource",
    "LanguageRow",
    "is_option",
    "opener",
    "spans",
]


class LanguageResource:
    """/language — the language's rows, for a client that cannot import
    them."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self) -> Language:
        """The story's typed LANGUAGE — commands are no part of it: what a
        line with no framing does, the openers a line may start with and
        the inliners it may carry, tokens and argument shapes alone. What
        each one MEANS in a menu is the frontend's own caption."""
        return Language(prose=PROSE_DESCRIPTION, openers=OPENERS, inliners=INLINERS)


@dataclass(frozen=True)
class Language:
    """The story's typed language: what a line with no framing does, the
    openers, the inliners."""

    prose: str
    openers: tuple[LanguageRow, ...]
    inliners: tuple[LanguageRow, ...]


@dataclass(frozen=True)
class LanguageRow:
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
