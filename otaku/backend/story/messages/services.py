"""A story's messages: its chain listed (`list_messages`), or every
message it holds in the order its list reads them (`list_messages_full`),
one read (`get_message`) and corrected (`update_message`); a line played
on it (`submit_line`, the play engine's, `play`, with the events it
tells); and a stored message's rules, as a client is answered them — its
body split for a client that parses nothing (`segments`), how it joins
the message before it on the canvas (`join_type`), what a tool's message
is called (`tool_label`), where a line typed now would stand
(`next_join`), and the question a story stands on (`posed_question`)."""

from dataclasses import dataclass, replace

from otaku.backend.app.core.services import App
from otaku.backend.errors import NO_SUCH_MESSAGE, NO_SUCH_STORY, Invalid, NotFound
from otaku.backend.story.chain import reply_start_index
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


@dataclass(frozen=True)
class ListedMessage:
    """A message of a story where a list reads it, with what its own
    branch says of it: the row before it there (None for a first), the
    id of the first row of the reply it stands in, and whether it is off
    the current chain."""

    row: Message
    previous: Message | None
    reply_start_id: int
    discarded: bool


def list_messages(app: App, story_id: int) -> list[ListedMessage]:
    """A story's current chain, root → head. Raises NotFound for a story
    that is not there."""
    return _listed(_chain(app, story_id))


def list_messages_full(app: App, story_id: int) -> list[ListedMessage]:
    """Every message a story holds, as its list reads them: the current
    chain, root → head, and under each of its messages the discarded
    branches that leave from it — those leaving from none on top — oldest
    first, each read through in order before the next. Raises NotFound
    for a story that is not there."""
    chain = _chain(app, story_id)
    children: dict[int | None, list[Message]] = {}
    for parent, row in app._store.stories.get_discarded(story_id):
        children.setdefault(parent, []).append(row)
    out = _branches(children, None, [])
    for index, listed in enumerate(_listed(chain)):
        out.append(listed)
        if listed.row.id in children:
            out += _branches(children, listed.row.id, chain[: index + 1])
    return out


def _chain(app: App, story_id: int) -> list[Message]:
    """A story's current chain as stored, root → head. Raises NotFound
    for a story that is not there."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    return app._store.stories.get_messages(story_id)


def _listed(chain: list[Message]) -> list[ListedMessage]:
    """A chain's rows, each with the row before it and where its reply
    starts."""
    return [
        ListedMessage(
            row,
            chain[index - 1] if index else None,
            chain[reply_start_index(chain, index + 1)].id,
            False,
        )
        for index, row in enumerate(chain)
    ]


def _branches(
    children: dict[int | None, list[Message]], parent: int | None, path: list[Message]
) -> list[ListedMessage]:
    """The discarded branches leaving from `parent`, depth first — `path`
    the rows from the root to it — each row's chapter counted along its
    own branch. A loop, never recursion: a branch may be as long as the
    story."""
    out: list[ListedMessage] = []
    path = list(path)
    stack = [(row, len(path)) for row in reversed(children.get(parent, []))]
    while stack:
        row, depth = stack.pop()
        del path[depth:]
        before = (path[-1].chapter or 0) if path else 0
        row = replace(row, chapter=before + (row.kind == "chapter"))
        path.append(row)
        reply_start_id = path[reply_start_index(path, len(path))].id
        out.append(ListedMessage(row, path[-2] if depth else None, reply_start_id, True))
        stack += [(child, depth + 1) for child in reversed(children.get(row.id, []))]
    return out


def get_message(app: App, story_id: int, message_id: int) -> Message:
    """One message of a story, on its chain or discarded. Raises NotFound
    for a story that is not there, and a message that is none of its."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    found = app._store.messages.get(message_id)
    if found is None or app._store.messages.get_story_id(message_id) != story_id:
        raise NotFound(NO_SUCH_MESSAGE)
    return found


def update_message(app: App, story_id: int, message_id: int, *, body: str) -> str:
    """The author's correction of one message of a story, and the
    sentence. The message must be that story's, on its chain or discarded
    — a correction landing on another story's would rewrite a story
    nobody was looking at. The whitespace at the message's edges stays the
    stored one, whatever the edit's: a break there is how the row joins
    its neighbours (`assembler.get_join_type`), which an editor does not
    show — a correction changes the words, never the join. Raises Invalid
    on an empty body, NotFound for a message that is none of the
    story's."""
    if not body.strip():
        raise Invalid("A message cannot be emptied — undo the exchange instead.")
    stored = get_message(app, story_id, message_id)
    app._store.messages.update(message_id, _with_edges_of(stored.body, body))
    app._search_index = None
    return "Message edited."


def set_pinned(app: App, story_id: int, message_id: int, pinned: bool) -> str:
    """Pin one message of a story, or unpin it, and the sentence: a
    pinned message is sent whole even where its scene is summarized,
    quoted after the scene's summary — once on the chain, for a discarded
    one. The message must be that story's, as an edit's must. Any message
    may be unpinned. Raises Invalid for a pin on a row that is never
    pinned (`can_pin`), NotFound for a message that is none of the
    story's."""
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
