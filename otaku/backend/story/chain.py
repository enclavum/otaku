"""A story's chain, as the backend's own modules operate it: a message
appended, the trailing exchange or reply popped — a card's unplayed
character with it — the head moved — each from the head the caller saw,
by the store's compare-and-set (HeadMoved where it stands elsewhere:
nothing written), the stories' search index reset with every move. And
the discards a revert puts back: each head an undo or a regenerate
left, held in memory until anything else moves the story's head."""

from collections.abc import Sequence
from dataclasses import replace

from otaku.backend.app.core.services import App, Discard
from otaku.backend.errors import HeadMoved
from otaku.store.ops.stories import StaleHeadError
from otaku.store.schema import Message

# ---------- the chain ----------


def append_message(app: App, story_id: int, message: Message, *, after: int | None) -> Message:
    """Append one turn to a story after `after` — the head the caller
    read, None for an empty story — and hand it back with its assigned
    id and the chapter it stands in. Raises HeadMoved where the head
    stands elsewhere: nothing is written."""
    try:
        message_id = app._store.stories.append(story_id, message, after=after)
    except StaleHeadError as e:
        raise HeadMoved(e.head) from e
    app._search_index = None
    return replace(message, id=message_id, chapter=app._store.messages.get_chapter(message_id))


def pop_exchange(app: App, story_id: int, *, expected_head: int | None) -> list[Message]:
    """Take back a story's trailing exchange: the reply (if any) with its
    tool chain (`take_reply`) plus the ONE user row that prompted
    it — every submission is a single row (a /me or /you direction rides
    its row's template), and an imported backlog of consecutive user
    rows is story, not one submission; a continue's reply, standing
    after a reply, is an exchange of its own and goes alone. Nothing is
    deleted: the head moves back and the messages taken stay in the tree as
    siblings. Returns the popped messages. Raises HeadMoved where the
    head no longer stands at `expected_head`."""
    chain = app._store.stories.get_messages(story_id)
    check_head(chain, expected_head)
    popped = take_reply(chain)
    if chain and chain[-1].role == "user":
        popped.append(chain.pop())
    if popped:
        move_head(app, story_id, chain, expected_head=popped[0].id)
    return popped


def pop_reply(
    app: App, story_id: int, *, expected_head: int | None
) -> tuple[Message | None, list[Message]]:
    """Take back a story's trailing reply with its tool chain
    (regenerate's first half, `take_reply`), so the take re-runs
    from what stands before it — the reader's line, or the reply a
    continue went on from: the head moves back; the discarded messages stay
    in the tree as siblings. Returns the first of the replies dropped
    (None where the story does not end on one) and the chain as it now
    stands. Raises HeadMoved where the head no longer stands at
    `expected_head`."""
    chain = app._store.stories.get_messages(story_id)
    check_head(chain, expected_head)
    popped = take_reply(chain)
    if not popped:
        return None, chain
    move_head(app, story_id, chain, expected_head=popped[0].id)
    first = next((row for row in reversed(popped) if row.role == "assistant"), popped[-1])
    return first, chain


def take_reply(chain: list[Message]) -> list[Message]:
    """Take a chain's trailing reply off it whole: the reply, and where a
    tool message stands right before it, that turn and the reply holding
    its call, again and again. It ends at a reply with no tool turn
    before it, so two replies side by side — a continue's after the one
    it went on from — are two replies. Reads roles and their order alone,
    never the mode. Returns the rows taken, newest first; [] where the
    chain does not end on a reply."""
    popped: list[Message] = []
    while chain and chain[-1].role in ("assistant", "tool"):
        row = chain.pop()
        popped.append(row)
        if row.role == "assistant" and not (chain and chain[-1].role == "tool"):
            break
    return popped


def reply_start_index(rows: Sequence[Message], end: int) -> int:
    """Where in `rows` the reply ending at `rows[end - 1]` begins, as
    `take_reply` takes it: with, where a tool turn stands right before
    it, that turn and the reply holding its call, again and again. A row
    that is no reply begins its own."""
    at = end - 1
    while at > 0 and (rows[at].role == "tool" or rows[at - 1].role == "tool"):
        at -= 1
    return at


def check_head(chain: Sequence[Message], expected_head: int | None) -> None:
    """Raise HeadMoved where a chain does not end at `expected_head` —
    before anything is touched."""
    head = chain[-1].id if chain else None
    if head != expected_head:
        raise HeadMoved(head)


def move_head(
    app: App, story_id: int, chain: Sequence[Message], *, expected_head: int | None
) -> None:
    """Point a story at a chain's last message (None when empty), moved on
    from `expected_head` — HeadMoved where it stands elsewhere."""
    try:
        app._store.stories.set_head(
            story_id, chain[-1].id if chain else None, expected_head=expected_head
        )
    except StaleHeadError as e:
        raise HeadMoved(e.head) from e
    app._search_index = None


def drop_unplayed_card(app: App, story_id: int, popped: list[Message]) -> bool:
    """Undoing a card import takes the character with it — while they are
    UNPLAYED: the import wrote the row, the undo unwrites it, and the
    duplicate refusal stops blocking a retry (the reason imports get
    undone at all). A journal marks them played — extraction wove them
    into the story — and then the cast row stays: a character with
    history is story, not import residue. Whether one was dropped."""
    dropped = False
    for row in popped:
        if row.kind != "card" or row.speaker_id is None:
            continue
        journaled = any(
            j.character_id == row.speaker_id for j in app._store.journals.list(story_id)
        )
        if not journaled:
            app._store.characters.delete(row.speaker_id)
            dropped = True
    return dropped


# ---------- the discards ----------


def record_discard(
    app: App, story_id: int, *, head_before: int | None, head_after: int | None
) -> None:
    """Note a discard on the story's history, newest last."""
    app._discards.setdefault(story_id, []).append(Discard(head_before, head_after))


def forget_discards(app: App, story_id: int) -> None:
    """Something other than an undo or a regenerate moved the story's
    head — or an undo took a card's character with it, which no revert
    brings back — or the story is gone: nothing it discarded before is a
    revert away any more."""
    app._discards.pop(story_id, None)
