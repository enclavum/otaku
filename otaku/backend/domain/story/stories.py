"""The stories, operated: found (the one filter rule both browsers
promise), read off the store, made — empty, or started with codex
cards — renamed, given a premise, deleted,
forked, rewound — and the actions on a story's trailing exchange: a
fresh take on its reply (the play engine's, `_play`), the exchange taken
back. Every write naming the story it acts on, every chain-changing one
dropping the search index.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from otaku.backend.domain.app import App
from otaku.backend.domain.story import cards
from otaku.backend.domain.story._chain import drop_unplayed_card, pop_exchange

# Re-exported: a fresh take on the last reply is an action on the story,
# run by the play engine.
from otaku.backend.domain.story._play import NOTHING_TO_REGENERATE as NOTHING_TO_REGENERATE
from otaku.backend.domain.story._play import regenerate_reply as regenerate_reply
from otaku.backend.domain.story._tools import ToolQuestion
from otaku.backend.domain.story.messages import posed_question
from otaku.backend.domain.story.settings import settings_of
from otaku.backend.errors import NO_SUCH_STORY, OFF_CHAIN, Conflict, HeadMoved, Invalid, NotFound
from otaku.formatting import LABEL_WIDTH, flatten, truncate_label
from otaku.store.ops.stories import StaleHeadError, StoryListing
from otaku.store.schema import Layout, Message, Story

# An undo with no exchange to take back — no story at all included.
NOTHING_TO_UNDO = "Nothing to undo."


@dataclass(frozen=True)
class StoryRecord:
    """One story as the store holds it: its row, its listing (the label a
    reader finds it by, the turns its chain holds, when it was last
    written), the layout it is read in, its newest row (None while
    nothing is played), the tools whose calls a reader is shown and those
    drawn folded, and the question the story stands on (None for none)."""

    story: Story
    listing: StoryListing
    layout: Layout
    newest: Message | None
    shown_tools: frozenset[str]
    folded_tools: frozenset[str]
    question: ToolQuestion | None


# ---------- the stories ----------


def list_stories(app: App, query: str = "") -> list[StoryListing]:
    """Every story, most recently played first — or the ones `query`
    finds, case-insensitive: the ONE filter rule both browsers promise,
    one call per keystroke. A story matches on its buried content (the
    CURRENT CHAIN's text) or on the face its listing row shows (label,
    arc summary, first prompt, model).

    The content half is served from the App-held index
    (`_search_index`), built on the first search and dropped by every
    chain-changing write, so a keystroke never re-decrypts the library.
    Best-effort: a filter is never worth a failure, so a store hiccup on
    the index narrows the match rather than raising."""
    rows = app._store.stories.list()
    needle = query.strip().lower()
    if not needle:
        return rows
    if app._search_index is None:
        try:
            app._search_index = app._store.stories.get_texts()
        except Exception:
            app._search_index = {}
    found = {sid for sid, text in app._search_index.items() if needle in text}
    return [
        row
        for row in rows
        if row.id in found
        or needle in f"{row.label} {row.story_so_far} {row.first_user} {row.model}".lower()
    ]


def exists(app: App, story_id: int) -> bool:
    return app._store.stories.exists(story_id)


def get_story(app: App, story_id: int) -> StoryRecord:
    """One story as the store holds it. Raises NotFound for a story that
    is not there."""
    story = app._store.stories.get(story_id)
    listing = app._store.stories.get_listing(story_id)
    if story is None or listing is None:
        raise NotFound(NO_SUCH_STORY)
    held = settings_of(app, story_id)
    newest = app._store.messages.get(story.head_id) if story.head_id is not None else None
    shown = held.shown_tools
    return StoryRecord(
        story,
        listing,
        held.layout,
        newest,
        shown,
        held.folded_tools,
        posed_question(newest, shown),
    )


def create_story(
    app: App,
    *,
    title: str = "",
    premise: str = "",
    card_ids: Sequence[int] = (),
    card_pictures: Sequence[int] = (),
    persona: str = "",
) -> tuple[int, str]:
    """A brand-new story, made AT ONCE — it is in the browser and carries
    its title before its first turn, not after — and the sentence.
    `title` and `premise` are optional, the premise taken verbatim. Given
    codex cards, it starts with them (`cards.bring_in_cards`),
    `card_pictures` the cards among them whose portrait rides their row,
    `persona` what their `{{user}}` binds to; an untitled one is named
    for them. Raises
    Refused for a card the codex does not hold, one picked twice, and
    two of one name — nothing made."""
    picked = cards.check_cards(app, card_ids)
    title = title.strip() or ", ".join(card.name for card in picked)
    story_id = app._store.stories.add(title=title or None)
    if premise:
        app._store.stories.set_system(story_id, premise)
    if picked:
        cards.bring_in_cards(
            app, story_id, picked, card_pictures=set(card_pictures), persona=persona
        )
    app._search_index = None
    if title:
        return story_id, f'Started a new story: "{truncate_label(title, LABEL_WIDTH)}".'
    return story_id, "Started a new story."


def update_story(
    app: App, story_id: int, *, title: str | None = None, premise: str | None = None
) -> str:
    """A story's title and its premise — the system prompt — whichever
    are given, and the sentence. A title is never emptied: a story with
    none falls back to its own opening text, and clearing a title is a
    different decision from giving one. The premise is taken verbatim,
    and "" CLEARS it. Raises NotFound for a story that is not there,
    Invalid for an emptied title or nothing to change."""
    if title is None and premise is None:
        raise Invalid("Nothing to change.")
    if title is not None and not title.strip():
        raise Invalid("A story needs a title — or leave the one it has.")
    if not exists(app, story_id):
        raise NotFound(NO_SUCH_STORY)
    said: list[str] = []
    if title is not None:
        app._store.stories.set_title(story_id, title.strip())
        said.append(f'Story title set to "{title.strip()}".')
    if premise is not None:
        app._store.stories.set_system(story_id, premise)
        said.append(_premise_notice(premise))
    return " ".join(said)


def delete_story(app: App, story_id: int) -> str:
    """Drop a story and everything it owns — the one destructive act,
    and the user's — and the sentence. Raises NotFound for a story that
    is not there."""
    if not exists(app, story_id):
        raise NotFound(NO_SUCH_STORY)
    app._store.stories.delete(story_id)
    app._store.sweep_files()
    app._search_index = None
    return "Story deleted."


def fork_story(
    app: App, story_id: int, *, from_message_id: int | None = None, title: str = ""
) -> tuple[int, str]:
    """Continue in a copy of a story — from its head, or from one of its
    messages on; the original stays — and the sentence. `title` names the
    copy: "" inherits a numbered one ("<title> - N", or none when the
    story has none). Raises NotFound for a story that is not there or a
    message that is not on its chain, Conflict for a story with nothing
    played."""
    if not exists(app, story_id):
        raise NotFound(NO_SUCH_STORY)
    ids = app._store.stories.get_messages_ids(story_id)
    if not ids:
        raise Conflict("Nothing to fork yet — send a message first.")
    if from_message_id is not None and from_message_id not in ids:
        raise NotFound(OFF_CHAIN)
    forked = app._store.stories.fork(
        story_id, from_message_id=from_message_id, title=title.strip() or None
    )
    app._search_index = None
    copy = get_story(app, forked).listing
    label = truncate_label(flatten(copy.label), LABEL_WIDTH)
    lead = f"Forked to: {label}." if label else "Forked."
    if from_message_id is None:
        return forked, lead
    return forked, f"{lead} Continued from message {copy.num_messages}."


def set_head(app: App, story_id: int, message_id: int, *, expected_head: int | None) -> None:
    """Rewind a story to one of its messages: the head moves there — from
    `expected_head`, the head the caller read — and the turns after it
    stay in the tree as siblings: nothing is deleted. Raises NotFound for
    a story that is not there or a message that is not on its chain,
    HeadMoved where the head stands elsewhere than expected."""
    if not exists(app, story_id):
        raise NotFound(NO_SUCH_STORY)
    ids = app._store.stories.get_messages_ids(story_id)
    if message_id not in ids:
        raise NotFound(OFF_CHAIN)
    try:
        app._store.stories.set_head(story_id, message_id, expected_head=expected_head)
    except StaleHeadError as e:
        raise HeadMoved(e.head) from e
    app._search_index = None


# ---------- the actions on a story's exchange ----------


def undo_exchange(app: App, story_id: int, *, expected_head: int | None) -> str:
    """Take back a story's trailing exchange — and the sentence. Nothing
    is deleted — the head moves back, from `expected_head`, the head the
    caller read. An unplayed imported character is dropped with
    their card exchange, so a retried import is never refused as a
    duplicate. Raises NotFound for a story that is not there, HeadMoved
    where its head stands elsewhere, Conflict with nothing to take
    back."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    app.note_submission()
    popped = pop_exchange(app, story_id, expected_head=expected_head)
    if not popped:
        raise Conflict(NOTHING_TO_UNDO)
    drop_unplayed_card(app, story_id, popped)
    return f"Took back the last exchange ({len(popped)} messages)."


# ---------- wording ----------


def _premise_notice(premise: str) -> str:
    """What a write of the premise answers with."""
    return f"System prompt set ({len(premise)} chars)." if premise else "System prompt cleared."
