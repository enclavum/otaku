"""The story's language as the terminal applies it — the twin of
`backend.api.story.language`: everything it answers, re-exported — its
rows, and the rules a client applies to a line not stored yet
(`opener`, `spans`, `is_option`)."""

from otaku.backend.api.story.language import INLINERS as INLINERS
from otaku.backend.api.story.language import OPENERS as OPENERS
from otaku.backend.api.story.language import PROSE_DESCRIPTION as PROSE_DESCRIPTION
from otaku.backend.api.story.language import Language as Language
from otaku.backend.api.story.language import LanguageRow as LanguageRow
from otaku.backend.api.story.language import is_option as is_option
from otaku.backend.api.story.language import opener as opener
from otaku.backend.api.story.language import spans as spans
