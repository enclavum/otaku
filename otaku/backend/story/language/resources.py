"""The story's language, as a client applies it to text not stored yet —
a line being typed, a reply still arriving: the one exception to the
interface's rule that a client calls the resources and nothing else,
since no schema can carry text that has not reached the backend. The
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

from otaku.backend.base import Resource
from otaku.backend.story.language.schemas import INLINERS, OPENERS, Language
from otaku.backend.story.language.services import PROSE_DESCRIPTION


class LanguageResource(Resource):
    """/language — the language's rows, for a client that cannot import
    them."""

    def get(self) -> Language:
        """The story's typed LANGUAGE — commands are no part of it: what a
        line with no framing does, the openers a line may start with and
        the inliners it may carry, tokens and argument shapes alone. What
        each one MEANS in a menu is the frontend's own caption."""
        return Language(prose=PROSE_DESCRIPTION, openers=OPENERS, inliners=INLINERS)
