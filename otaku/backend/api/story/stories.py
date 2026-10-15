"""The stories as a frontend reaches them — the collection and each
story (`StoriesResource`), and the actions on one: a copy continued
(`ForksResource`), a fresh take on its last reply (`RegensResource`),
its trailing exchange taken back (`UndosResource`) — with every resource
inside a story attached under it: its settings, its messages and the
play, its memory, its cards, its export, its next request. Every call
names the story it acts on. The work is the domain's
(`domain.story.stories`); this answers what stands after it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from otaku.backend.api._writes import UNCHANGED, Changed, Unchanged
from otaku.backend.api.story.cards import StoryCardsResource
from otaku.backend.api.story.context import ContextResource
from otaku.backend.api.story.lore import (
    CharactersResource,
    ExtractionResource,
    JournalsResource,
    LoreResource,
    ScenesResource,
)
from otaku.backend.api.story.messages import MessagesResource, PlayEvent, as_events
from otaku.backend.api.story.settings import StorySettingsResource
from otaku.backend.api.story.transfer import ExportResource
from otaku.backend.domain.app import App
from otaku.backend.domain.app import models as domain_models
from otaku.backend.domain.story import messages as domain_messages
from otaku.backend.domain.story import stories as domain_stories

# Re-exported: the refusals a frontend says itself where it has no story
# to ask.
from otaku.backend.domain.story.stories import NOTHING_TO_REGENERATE as NOTHING_TO_REGENERATE
from otaku.backend.domain.story.stories import NOTHING_TO_UNDO as NOTHING_TO_UNDO
from otaku.backend.domain.story.stories import StoryListing, StoryRecord
from otaku.backend.errors import Invalid
from otaku.context.assembler import JoinType
from otaku.formatting import LABEL_WIDTH, flatten, truncate_label
from otaku.store.schema import CompletionType, Layout, StoryMode


class StoriesResource:
    """/stories — every story, and each one by its id."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.forks = ForksResource(app)
        self.settings = StorySettingsResource(app)
        self.messages = MessagesResource(app)
        self.regens = RegensResource(app)
        self.undos = UndosResource(app)
        self.lore = LoreResource(app)
        self.scenes = ScenesResource(app)
        self.characters = CharactersResource(app)
        self.journals = JournalsResource(app)
        self.extraction = ExtractionResource(app)
        self.cards = StoryCardsResource(app)
        self.export = ExportResource(app)
        self.context = ContextResource(app)

    def list(self, *, query: str = "") -> list[StorySummary]:
        """Every story, most recently played first — or the ones `query`
        finds, case-insensitive: the ONE filter rule both browsers
        promise, one call per keystroke, on a story's buried content or
        the face its listing row shows."""
        return [_summary(row) for row in domain_stories.list_stories(self._app, query)]

    def get(self, story_id: int) -> Story:
        """One story's facts. Raises NotFound for a story that is not
        there."""
        return _story(self._app, domain_stories.get_story(self._app, story_id))

    def create(
        self,
        *,
        title: str = "",
        premise: str = "",
        card_ids: tuple[int, ...] = (),
        card_pictures: tuple[int, ...] = (),
        persona: str = "",
    ) -> Changed[Story]:
        """A brand-new story, made AT ONCE — it is in the browser and
        carries its title before its first turn, not after. `title` and
        `premise` are optional, the premise taken verbatim. Given codex
        cards (`card_ids`, in pick order), it starts with them: a card row
        and a character each, linked to its card, then the greeting of
        the first card that has one — `card_pictures` the cards whose
        portrait rides their row, `persona` what their `{{user}}` binds
        to; an untitled one is named for them. Raises Refused for a card the
        codex does not hold, one picked twice, and two of one name —
        nothing made."""
        story_id, said = domain_stories.create_story(
            self._app,
            title=title,
            premise=premise,
            card_ids=card_ids,
            card_pictures=card_pictures,
            persona=persona,
        )
        return Changed(self.get(story_id), said)

    def update(
        self,
        story_id: int,
        *,
        title: str | Unchanged = UNCHANGED,
        premise: str | Unchanged = UNCHANGED,
        head: int | Unchanged = UNCHANGED,
        expected_head: int | Unchanged | None = UNCHANGED,
    ) -> Changed[Story]:
        """A story's title and its premise — the system prompt — and its
        head, whichever are given. A title is never emptied; the premise
        is taken verbatim, and "" CLEARS it. The head moves back to one of
        the story's messages — from `expected_head`, the head the caller
        read, which a head move names — and the messages after it stay in
        the tree as siblings: nothing
        is deleted; the landing line says where it stands. Raises
        NotFound for a story that is not there or a head off its chain,
        HeadMoved where the head stands elsewhere than expected, Invalid
        for an emptied title or nothing to change."""
        app = self._app
        said: list[str] = []
        if not isinstance(title, Unchanged) or not isinstance(premise, Unchanged):
            said.append(
                domain_stories.update_story(
                    app,
                    story_id,
                    title=None if isinstance(title, Unchanged) else title,
                    premise=None if isinstance(premise, Unchanged) else premise,
                )
            )
        if not isinstance(head, Unchanged):
            if isinstance(expected_head, Unchanged):
                raise Invalid("A head move names the head it moves from.")
            domain_stories.set_head(app, story_id, head, expected_head=expected_head)
            said.append(self.get(story_id).landed_line("Truncated"))
        if not said:
            raise Invalid("Nothing to change.")
        return Changed(self.get(story_id), " ".join(said))

    def delete(self, story_id: int) -> Changed[None]:
        """Drop a story and everything it owns — the one destructive act,
        and the user's. Raises NotFound for a story that is not there."""
        return Changed(None, domain_stories.delete_story(self._app, story_id))


class ForksResource:
    """/stories/{story}/forks — a story continued in a copy, and the
    stories forks join it to."""

    def __init__(self, app: App) -> None:
        self._app = app

    def list(self, story_id: int) -> list[StorySummary]:
        """Every other story a chain of forks joins this one to — the one
        it was forked from, its forks, theirs, and their siblings — most
        recently played first. Raises NotFound for a story that is not
        there."""
        return [_summary(row) for row in domain_stories.list_family(self._app, story_id)]

    def create(self, story_id: int, *, at: int | None = None, title: str = "") -> Changed[Story]:
        """A copy of a story — from its head, or from its message `at` on;
        the original stays. `title` names the copy: "" inherits a
        numbered one. Raises NotFound for a story that is not there or a
        message that is not on its chain, Conflict for a story with
        nothing played."""
        forked, said = domain_stories.fork_story(
            self._app, story_id, from_message_id=at, title=title
        )
        return Changed(_story(self._app, domain_stories.get_story(self._app, forked)), said)


class RegensResource:
    """/stories/{story}/regens — a fresh take on the last prompt."""

    def __init__(self, app: App) -> None:
        self._app = app

    def create(self, story_id: int, *, expected_head: int | None) -> Iterator[PlayEvent]:
        """Re-run a story's last prompt: the standing reply becomes a
        sibling and the fresh take streams (the reply's pieces and its
        AssistantMessage, Failed/Done — no UserMessage; the prompt is already on
        screen). Raises Refused when there is no model, no story, nothing
        to regenerate, or a head standing elsewhere than `expected_head`
        — EAGERLY, before the iterator is returned and before anything is
        dropped."""
        return as_events(
            domain_stories.regenerate_reply(self._app, story_id, expected_head=expected_head)
        )


class UndosResource:
    """/stories/{story}/undos — the trailing exchange taken back."""

    def __init__(self, app: App) -> None:
        self._app = app

    def create(self, story_id: int, *, expected_head: int | None) -> Changed[Story]:
        """Take back a story's trailing exchange — the reader's line and
        the reply unit it got, or a continue's reply alone — moving the
        head back from `expected_head`, the head the caller read. Nothing
        is deleted: the messages stay in the tree, and an imported
        character whose card exchange was never played goes with it.
        Answered as the story now stands. Raises NotFound for a story that
        is not there, HeadMoved where its head stands elsewhere, Conflict
        with nothing to take back."""
        said = domain_stories.undo_exchange(self._app, story_id, expected_head=expected_head)
        return Changed(_story(self._app, domain_stories.get_story(self._app, story_id)), said)


def _summary(row: StoryListing) -> StorySummary:
    return StorySummary(
        id=row.id,
        title=row.title,
        label=row.label,
        story_so_far=row.story_so_far,
        first_message=row.first_user,
        model=row.model,
        updated_at=row.updated_at,
        message_count=row.num_messages,
    )


def _story(app: App, record: StoryRecord) -> Story:
    """A story's record as a client draws it — the wire and the
    pictures read off the model in use."""
    facts = domain_models.model_facts(app)
    layout = record.layout
    asked = record.question
    return Story(
        id=record.story.id,
        title=record.story.title,
        label=flatten(record.listing.label),
        premise=record.story.system,
        head=record.story.head_id,
        message_count=record.listing.num_messages,
        updated_at=record.listing.updated_at,
        layout=layout,
        mode=layout.mode,
        next_join=domain_messages.next_join(record.newest, layout),
        completion_type=facts.completion_type(layout),
        can_attach=facts.can_attach(layout),
        question=Question(asked.text, asked.question, asked.options) if asked else None,
        shown_tools=record.shown_tools,
        folded_tools=record.folded_tools,
    )


@dataclass(frozen=True)
class StorySummary:
    """One row of the story browser: the story's title and the label a
    reader finds it by (the title, else the newest story-so-far rollup,
    else the first line played; "" when nothing names it), that rollup
    and that line, the model it was last played on, when it was last
    written (for display alone), and how many messages its chain holds."""

    id: int
    title: str
    label: str
    story_so_far: str
    first_message: str
    model: str
    updated_at: datetime
    message_count: int


@dataclass(frozen=True)
class Story:
    """One story's facts: its title and the label a reader finds it by
    (flattened to one line and uncut), its premise, the message its chain
    ends at (None while nothing is played), how many messages that chain
    holds, when it was last written (for display alone), the layout it
    is read in and the look that draws it (`mode`), how a line typed now
    would join its newest row ("inline" or "paragraph"; None while
    nothing is played — the story to be begun), on the model in use the
    wire its next request goes over and whether a picture may ride it,
    the question the story stands on (None for none: the next line
    played answers it), the tools whose calls a reader is shown, and
    those a frontend that can fold draws by their label alone."""

    id: int
    title: str
    label: str
    premise: str
    head: int | None
    message_count: int
    updated_at: datetime
    layout: Layout
    mode: StoryMode
    next_join: JoinType | None
    completion_type: CompletionType
    can_attach: bool
    question: Question | None
    shown_tools: frozenset[str]
    folded_tools: frozenset[str]

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


@dataclass(frozen=True)
class Question:
    """The question a story stands on: the call's inside as the model
    wrote it, the question alone, and the answers to pick from (none for
    a free answer)."""

    text: str
    question: str
    options: tuple[str, ...]
