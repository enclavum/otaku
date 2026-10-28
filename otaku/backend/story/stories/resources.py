"""The stories as a frontend reaches them — the collection and each
story (`StoriesResource`), and the actions on one: a copy continued
(`ForksResource`), a fresh take on its last reply (`RegensResource`),
its trailing exchange taken back (`UndosResource`), what its newest
discard took put back (`RevertsResource`) — with every resource
inside a story attached under it: its settings, its messages and the
play, its memory, its cards, its export, its next request. Every call
names the story it acts on. The work is the domain's
(`story.stories.services`); this answers what stands after it.
"""

from __future__ import annotations

from collections.abc import Iterator

from otaku.backend.app.core.services import App
from otaku.backend.app.models import services as domain_models
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged, streams
from otaku.backend.errors import Invalid
from otaku.backend.story.cards.resources import StoryCardsResource
from otaku.backend.story.context.resources import ContextResource
from otaku.backend.story.lore.resources import (
    CharactersResource,
    ExtractionResource,
    JournalsResource,
    LoreResource,
    ScenesResource,
)
from otaku.backend.story.messages import services as domain_messages
from otaku.backend.story.messages.resources import MessagesResource
from otaku.backend.story.messages.schemas import PlayEvent, as_events
from otaku.backend.story.settings.resources import StorySettingsResource
from otaku.backend.story.stories import services as domain_stories
from otaku.backend.story.stories.schemas import Question, Story, StorySummary
from otaku.backend.story.stories.services import (
    StoryListing,
    StoryRecord,
)
from otaku.backend.story.transfer.resources import ExportResource
from otaku.formatting import flatten


class StoriesResource(Resource):
    """/stories — every story, and each one by its id."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.forks = ForksResource(app)
        self.settings = StorySettingsResource(app)
        self.messages = MessagesResource(app)
        self.regens = RegensResource(app)
        self.undos = UndosResource(app)
        self.reverts = RevertsResource(app)
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
        is taken verbatim, and "" CLEARS it. The head moves to one of the
        story's messages — back along its chain, or onto a discarded one —
        from `expected_head`, the head the caller read, which a head move
        names; the messages it leaves stay in the tree as siblings:
        nothing is deleted, and no revert takes the move back. The landing
        line says where it stands. Raises NotFound for a story that is not
        there or a head that is none of its messages, HeadMoved where the
        head stands elsewhere than expected, Invalid for an emptied title
        or nothing to change."""
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
            verb = domain_stories.set_head(app, story_id, head, expected_head=expected_head)
            said.append(self.get(story_id).landed_line(verb))
        if not said:
            raise Invalid("Nothing to change.")
        return Changed(self.get(story_id), " ".join(said))

    def delete(self, story_id: int) -> Changed[None]:
        """Drop a story and everything it owns — the one destructive act,
        and the user's. Raises NotFound for a story that is not there."""
        return Changed(None, domain_stories.delete_story(self._app, story_id))


class ForksResource(Resource):
    """/stories/{story}/forks — a story continued in a copy, and the
    stories forks join it to."""

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


class RegensResource(Resource):
    """/stories/{story}/regens — a fresh take on the last prompt."""

    @streams
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


class UndosResource(Resource):
    """/stories/{story}/undos — the trailing exchange taken back."""

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


class RevertsResource(Resource):
    """/stories/{story}/reverts — what the newest discard took, put back."""

    def create(self, story_id: int, *, expected_head: int | None) -> Changed[Story]:
        """Put back what a story's newest discard took — an undo's
        exchange or a regenerate's earlier take — moving the head back
        from `expected_head`, the head the caller read: what the discard
        landed on stays in the tree as siblings. The discards are the
        undos and regenerates since anything else moved the story's head,
        held while the app runs (`Story.can_revert`). Answered
        as the story now stands. Raises NotFound for a story that is not
        there, HeadMoved where its head stands elsewhere, Conflict with
        nothing to revert."""
        said = domain_stories.revert_discard(self._app, story_id, expected_head=expected_head)
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
        message_count=row.message_count,
        word_count=row.word_count,
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
        message_count=record.listing.message_count,
        word_count=record.listing.word_count,
        updated_at=record.listing.updated_at,
        layout=layout,
        mode=layout.mode,
        next_join=domain_messages.next_join(record.newest, layout),
        completion_type=facts.completion_type(layout),
        can_attach=facts.can_attach(layout),
        question=Question(asked.text, asked.question, asked.options) if asked else None,
        shown_tools=record.shown_tools,
        folded_tools=record.folded_tools,
        can_revert=record.can_revert,
    )
