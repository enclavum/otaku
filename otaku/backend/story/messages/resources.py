"""A story's messages as a frontend reaches them: listed, read and
corrected (`MessagesResource`), and played — a line sent, the reply that
streams back. A fresh take on the last reply and the trailing exchange
taken back are actions on the story (`api.story.stories`: the regens,
the undos); the regens stream the events of this module.

The stream is the frontend's to drive: iterate to render, close to
cancel. Its events are this module's own, in the order play happens: a
row that landed is a `*Message` — `UserMessage`, `AssistantMessage`,
`ToolMessage` — named by the row's role and carrying the row as a
client draws it; a piece of the reply as it streams is `AssistantText`,
`AssistantReasoning` or `AssistantToolCall`; and exactly one of
`Declined`, `Failed`, `EmptyReply` and `Done` ends the stream. The engine is the
domain's (`story.messages.services`), with events of its own that this
module tells in these (`as_events`). Closing mid-stream keeps and
records what arrived — Ctrl+C and Ctrl+R are the frontend closing the
generator; a Ctrl+R then simply asks the regens for the fresh take.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from otaku.backend.app.pictures.services import RawFile
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged, streams
from otaku.backend.errors import Invalid
from otaku.backend.story.messages import services as domain_messages
from otaku.backend.story.messages.schemas import Message, PlayEvent, _as_message, as_events


class MessagesResource(Resource):
    """/stories/{story}/messages — a story's messages, its current chain
    or all of them; a line sent plays the story."""

    def list(
        self, story_id: int, *, last: int | None = None, with_discarded: bool = False
    ) -> list[Message]:
        """A story's current chain, root → head, as messages — its `last`
        messages alone where a count is given, each joined to the one before
        it all the same. `with_discarded` lists every message the story
        holds, as its list reads them: under each message of the chain the
        discarded branches leaving from it, oldest first. Raises NotFound
        for a story that is not there."""
        listing = (
            domain_messages.list_messages_full if with_discarded else domain_messages.list_messages
        )
        drawn = [
            _as_message(
                listed.row, listed.previous, listed.reply_start_id, discarded=listed.discarded
            )
            for listed in listing(self._app, story_id)
        ]
        return drawn[-last:] if last else drawn

    def get(self, story_id: int, message_id: int) -> Message:
        """One message of a story, on its chain or discarded. Raises
        NotFound for one that is none of its."""
        domain_messages.get_message(self._app, story_id, message_id)  # the story's, or NotFound
        listed = self.list(story_id, with_discarded=True)
        return next(found for found in listed if found.id == message_id)

    def update(
        self,
        story_id: int,
        message_id: int,
        *,
        body: str | Unchanged = UNCHANGED,
        pinned: bool | Unchanged = UNCHANGED,
    ) -> Changed[Message]:
        """The author's correction of one message of a story, on its chain
        or discarded — the whitespace at its edges stays the stored one: a
        correction changes the words, never the join — and its pin: a
        pinned message is sent whole even where its scene is summarized.
        Raises Invalid for nothing to change, an empty body and a pin on a
        row that is never pinned, NotFound for a message that is none of
        the story's."""
        said = []
        if not isinstance(body, Unchanged):
            said.append(domain_messages.update_message(self._app, story_id, message_id, body=body))
        if not isinstance(pinned, Unchanged):
            said.append(domain_messages.set_pinned(self._app, story_id, message_id, pinned))
        if not said:
            raise Invalid("Nothing to change.")
        return Changed(self.get(story_id, message_id), " ".join(said))

    @streams
    def create(
        self,
        story_id: int,
        *,
        line: str,
        files: Sequence[RawFile] = (),
        expected_head: int | None,
    ) -> Iterator[PlayEvent]:
        """One line played on a story (prose or a direction — never a
        command; the frontend routed those already), with `files` the
        pictures the reader attached to it, played on from
        `expected_head`, the head the caller read — in the canvas an empty
        line is the continue. Validation is EAGER: invalid syntax, a
        picture refused, a story that is not there and a head standing
        elsewhere raise Refused before the iterator is returned and
        before anything is recorded. Then the line records and is told
        (UserMessage), and the reply streams as AssistantReasoning /
        AssistantText / AssistantToolCall pieces and lands as
        AssistantMessage, Failed on a stream error, EmptyReply where it
        brought no text, Done at the end. With
        no model selected the line is still recorded — it is story — and
        Declined ends the stream after UserMessage. Closing the generator
        mid-stream records the partial reply and its usage."""
        return as_events(
            domain_messages.submit_line(
                self._app, story_id, line, files, expected_head=expected_head
            )
        )
