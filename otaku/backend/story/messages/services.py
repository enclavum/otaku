"""A story's messages: its chain read (`list_messages`, `get_message`)
and a message corrected (`update_message`); a line played on it
(`submit_line`, the play engine's, `play`, with the events it tells);
and a stored message's rules, as a client is answered them — its body
split for a client that parses nothing (`segments`), how it joins the
message before it on the canvas (`join_type`), where the reply unit it
stands in starts (`unit_start`), what a tool's message is called
(`tool_label`), where a line typed now would stand (`next_join`), and
the question a story stands on (`posed_question`)."""

from otaku.backend.app.core.services import App
from otaku.backend.errors import NO_SUCH_STORY, OFF_CHAIN, Invalid, NotFound
from otaku.backend.story.chain import unit_start as unit_start
from otaku.backend.story.play import CallPiece as CallPiece
from otaku.backend.story.play import Declined as Declined
from otaku.backend.story.play import Event as Event
from otaku.backend.story.play import Failed as Failed
from otaku.backend.story.play import Finished as Finished
from otaku.backend.story.play import Landed as Landed
from otaku.backend.story.play import ProsePiece as ProsePiece
from otaku.backend.story.play import ReasoningPiece as ReasoningPiece
from otaku.backend.story.play import submit_line as submit_line
from otaku.backend.story.tools import TOOLS, ToolQuestion, get_tool_label, read_call
from otaku.backend.story.tools import Actor as Actor
from otaku.context import syntax, tools
from otaku.context.assembler import get_join_type
from otaku.context.prompt import JoinType
from otaku.context.tools import Prose
from otaku.store.schema import Layout, Message


def list_messages(app: App, story_id: int) -> list[Message]:
    """A story's current chain, root → head. Raises NotFound for a story
    that is not there."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    return app._store.stories.get_messages(story_id)


def get_message(app: App, story_id: int, message_id: int) -> Message:
    """One message of a story's current chain. Raises NotFound for a
    story that is not there, and a message that is not on its chain."""
    found = next((row for row in list_messages(app, story_id) if row.id == message_id), None)
    if found is None:
        raise NotFound(OFF_CHAIN)
    return found


def update_message(app: App, story_id: int, message_id: int, *, body: str) -> str:
    """The author's correction of one message of a story, and the
    sentence. The message must be on that story's current chain — a
    correction landing on another story's would rewrite a story nobody
    was looking at. The whitespace at the message's edges stays the
    stored one, whatever the edit's: a break there is how the row joins
    its neighbours (`assembler.get_join_type`), which an editor does not
    show — a correction changes the words, never the join. Raises Invalid
    on an empty body, NotFound for a message that is not on the story's
    chain."""
    if not body.strip():
        raise Invalid("A message cannot be emptied — undo the exchange instead.")
    stored = get_message(app, story_id, message_id)
    app._store.messages.update(message_id, _with_edges_of(stored.body, body))
    app._search_index = None
    return "Message edited."


def set_pinned(app: App, story_id: int, message_id: int, pinned: bool) -> str:
    """Pin one message of a story, or unpin it, and the sentence: a
    pinned message is sent whole even where its scene is summarized,
    quoted after the scene's summary. The message must be on that story's
    current chain, as an edit's must. Any message may be unpinned. Raises
    Invalid for a pin on a row that is never pinned (`can_pin`), NotFound
    for a message that is not on the story's chain."""
    stored = get_message(app, story_id, message_id)
    if pinned and not can_pin(stored):
        raise Invalid("A card or a tool result can't be pinned.")
    app._store.messages.set_pinned(message_id, pinned)
    return "Message pinned." if pinned else "Message unpinned."


def can_pin(row: Message) -> bool:
    """Whether a row may be pinned: any but a card's, which is always
    sent whole, and a tool's result, which is the model's alone."""
    return row.kind != "card" and row.role != "tool"


def tool_label(row: Message) -> str | None:
    """What a tool's message is called where a frontend names it, for the
    result it holds (`Tool.label_of`); None for every other row."""
    return get_tool_label(row.provider, row.body) if row.role == "tool" else None


def segments(row: Message) -> list[dict[str, object]]:
    """A stored row's body split: prose (`{"kind": "prose", "text"}`) and
    each tool call as its tool's facts (`{"kind": "tool_call", …}` — a
    name no tool owns is a call all the same, with its tool and text
    alone), in order. A user's line splits instead into the runs the
    language shows it as (`syntax.shown`): `{"kind": "prose"}` and
    `{"kind": "ooc"}` — a chapter's, `{"kind": "chapter_number"}` and
    `{"kind": "chapter_title"}` with the join between."""
    if row.role == "user":
        return [
            {"kind": kind, "text": text}
            for text, kind in syntax.shown(row.body, chapter=row.chapter)
        ]
    out: list[dict[str, object]] = []
    for part in tools.parse_reply(row.body):
        if isinstance(part, Prose):
            out.append({"kind": "prose", "text": part.text})
        elif part.name in TOOLS:
            out.append({"kind": "tool_call", **read_call(part.name, part.text).to_json()})
        else:
            out.append({"kind": "tool_call", "tool": part.name, "text": part.text})
    return out


def join_type(previous: Message | None, next: Message) -> JoinType:
    """How the stored turn `next` joins `previous`, the one before it
    (`context.assembler.get_join_type`, the wire's own rule, read off the
    rows' mode and the breaks at their edges)."""
    return get_join_type(previous, next)


def next_join(newest: Message | None, layout: Layout) -> JoinType | None:
    """How a line typed now would join `newest`, the story's newest row:
    the join rule applied to a line opening with no break of its own, in
    `layout`'s mode — None while the story is begun, nothing played."""
    if newest is None:
        return None
    return join_type(newest, Message(role="user", body="-", mode=layout.mode))


def posed_question(newest: Message | None, shown: frozenset[str]) -> ToolQuestion | None:
    """The question a story stands on, read: the questions tool is on
    (`shown`), and the newest message is a reply whose last call is a
    question, with nothing after it but notes and whitespace — prose, or
    a call of a tool this build does not know, means the model went on
    past it; a line played since means it was answered."""
    if newest is None or newest.role != "assistant" or "question" not in shown:
        return None
    for part in reversed(tools.parse_reply(newest.body)):
        if isinstance(part, Prose):
            if part.text.strip():
                return None
            continue
        if part.name == "question":
            return ToolQuestion(part.text)
        if part.name != "note":
            return None
    return None


def _with_edges_of(stored: str, text: str) -> str:
    """`text` with `stored`'s edge whitespace in place of its own."""
    core = stored.strip()
    lead = stored[: stored.index(core)] if core else ""
    trail = stored[len(lead) + len(core) :] if core else ""
    return lead + text.strip() + trail
