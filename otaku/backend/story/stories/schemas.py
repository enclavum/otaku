"""What `story.stories.resources` answers: its schemas."""

from __future__ import annotations

from datetime import datetime

from otaku.backend.base import Schema

# Re-exported: the refusals a frontend says itself where it has no story
# to ask.
from otaku.backend.story.stories.services import NOTHING_TO_REGENERATE as NOTHING_TO_REGENERATE
from otaku.backend.story.stories.services import NOTHING_TO_REVERT as NOTHING_TO_REVERT
from otaku.backend.story.stories.services import NOTHING_TO_UNDO as NOTHING_TO_UNDO
from otaku.context.prompt import JoinType
from otaku.formatting import LABEL_WIDTH, truncate_label
from otaku.store.schema import CompletionType, Layout, StoryMode


class StorySummary(Schema):
    """One row of the story browser: the story's title and the label a
    reader finds it by (the title, else the newest story-so-far rollup,
    else the first line played; "" when nothing names it), that rollup
    and that line, the model it was last played on, when it was last
    written (for display alone), and how many messages and words its
    chain holds."""

    id: int
    title: str
    label: str
    story_so_far: str
    first_message: str
    model: str
    updated_at: datetime
    message_count: int
    word_count: int


class Story(Schema):
    """One story's facts: its title and the label a reader finds it by
    (flattened to one line and uncut), its premise, the message its chain
    ends at (None while nothing is played), how many messages and words
    that chain holds, when it was last written (for display alone), the
    layout it is read in and the look that draws it (`mode`), how a line
    typed now would join its newest row ("inline" or "paragraph"; None
    while nothing is played — the story to be begun), on the model in
    use the wire its next request goes over and whether a picture may
    ride it, the question the story stands on (None for none: the next
    line played answers it), the tools whose calls a reader is shown,
    those a frontend that can fold draws by their label alone, and
    whether a revert would put something back now (`can_revert`: an
    undo or a regenerate since anything else moved its head)."""

    id: int
    title: str
    label: str
    premise: str
    head: int | None
    message_count: int
    word_count: int
    updated_at: datetime
    layout: Layout
    mode: StoryMode
    next_join: JoinType | None
    completion_type: CompletionType
    can_attach: bool
    question: Question | None
    shown_tools: frozenset[str]
    can_revert: bool

    def landed_line(self, verb: str = "Resumed") -> str:
        """The line a frontend prints when it lands in the story ("Story:
        …. Resumed at message 14."), `verb` naming what the landing
        settled. An unnamed story drops the first half rather than show
        an empty name, and one with nothing played says so rather than
        count to zero."""
        label = truncate_label(self.label, LABEL_WIDTH)
        head = f"Story: {label}. " if label else ""
        if not self.message_count:
            return f"{head}{verb}, nothing played yet."
        return f"{head}{verb} at message {self.message_count}."


class Question(Schema):
    """The question a story stands on: the call's inside as the model
    wrote it, the question alone, and the answers to pick from (none for
    a free answer)."""

    text: str
    question: str
    options: tuple[str, ...]
