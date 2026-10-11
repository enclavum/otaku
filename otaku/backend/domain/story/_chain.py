"""A story's chain, as the backend's own modules operate it: a message
appended, the trailing exchange or reply popped — a card's unplayed
character with it — the head moved — each from the head the caller saw,
by the store's compare-and-set (HeadMoved where it stands elsewhere:
nothing written), the stories' search index reset with every move — the
story's settings as they stand, its next request assembled from all of
it, and the worker's job over it."""

from collections.abc import Sequence
from dataclasses import replace

from otaku.agents import Job
from otaku.agents.extraction import ExtractionSettings
from otaku.backend.domain.app import App
from otaku.backend.domain.story.settings import settings_of
from otaku.backend.errors import HeadMoved
from otaku.context import assembler
from otaku.context.assembler import AssembledPrompt, ContextShape
from otaku.providers import PicturesRide
from otaku.store.ops.stories import StaleHeadError
from otaku.store.schema import CompletionType, Message


def assemble(
    app: App, story_id: int, context_window: int | None, messages: Sequence[Message] | None = None
) -> AssembledPrompt:
    """A story's next request — the one binding of its stored premise,
    settings and chain to `assembler.assemble_story`, so the turn, the
    preview and every other call site can never disagree on what is
    sent; `window` the model's, `messages` its chain where the caller
    holds it already. The pictures ride only while the model can see
    them, and only the latest turn's where the engine would gather every
    turn's onto it — and never over the text wire, which is one text
    (`as_text`): held, and counted as held. Every injection is a
    setting's, the canvas's standing instruction included
    (`StorySettingEmulateTextCompletion`, gated on the layout), so the
    warm-up's job rebuilds the same prefix. Raises `ContextOverflowError`
    when the story cannot fit the limit even fully degraded."""
    facts = app._model_facts()
    settings = settings_of(app, story_id)
    config = app._config()
    shape = ContextShape(
        head_messages=config.head_messages,
        min_tail_messages=config.min_tail_messages,
        context_in_force=assembler.context_in_force(context_window, config.context_limit),
    )
    chain = app._store.stories.get_messages(story_id) if messages is None else messages
    return assembler.assemble_story(
        app._store,
        story_id,
        system=app._store.stories.get_system(story_id),
        messages=list(chain),
        injections=settings.injections,
        tool_set=settings.tool_set,
        prompts=app._prompts(),
        shape=shape,
        pictures_ride=(
            facts.client.pictures_ride(facts.model.name)
            if facts.client is not None
            and facts.model is not None
            and facts.completion_type(settings.layout) is CompletionType.CHAT
            else PicturesRide.NONE
        ),
    )


def append_message(app: App, story_id: int, message: Message, *, after: int | None) -> Message:
    """Append one turn to a story after `after` — the head the caller
    read, None for an empty story — and hand it back with its assigned
    id. Raises HeadMoved where the head stands elsewhere: nothing is
    written."""
    try:
        message_id = app._store.stories.append(story_id, message, after=after)
    except StaleHeadError as e:
        raise HeadMoved(e.head) from e
    app._search_index = None
    return replace(message, id=message_id)


def pop_exchange(app: App, story_id: int, *, expected_head: int | None) -> list[Message]:
    """Take back a story's trailing exchange: the reply (if any) with its
    tool chain (`take_reply_unit`) plus the ONE user row that prompted
    it — every submission is a single row (a /me or /you direction rides
    its row's template), and an imported backlog of consecutive user
    rows is story, not one submission; a continue's reply, standing
    after a reply, is an exchange of its own and goes alone. Nothing is
    deleted: the head moves back and the messages taken stay in the tree as
    siblings. Returns the popped messages. Raises HeadMoved where the
    head no longer stands at `expected_head`."""
    chain = app._store.stories.get_messages(story_id)
    check_head(chain, expected_head)
    popped = take_reply_unit(chain)
    if chain and chain[-1].role == "user":
        popped.append(chain.pop())
    if popped:
        move_head(app, story_id, chain, expected_head=popped[0].id)
    return popped


def pop_reply(
    app: App, story_id: int, *, expected_head: int | None
) -> tuple[Message | None, list[Message]]:
    """Take back a story's trailing reply with its tool chain
    (regenerate's first half, `take_reply_unit`), so the take re-runs
    from what stands before it — the reader's line, or the reply a
    continue went on from: the head moves back; the discarded messages stay
    in the tree as siblings. Returns the first of the replies dropped
    (None where the story does not end on one) and the chain as it now
    stands. Raises HeadMoved where the head no longer stands at
    `expected_head`."""
    chain = app._store.stories.get_messages(story_id)
    check_head(chain, expected_head)
    popped = take_reply_unit(chain)
    if not popped:
        return None, chain
    move_head(app, story_id, chain, expected_head=popped[0].id)
    first = next((row for row in reversed(popped) if row.role == "assistant"), popped[-1])
    return first, chain


def take_reply_unit(chain: list[Message]) -> list[Message]:
    """Take a chain's trailing reply off it as ONE unit: the reply, and
    where a tool message stands right before it, that turn and the reply
    holding its call, again and again. The unit ends at a reply with no
    tool turn before it, so two replies side by side — a continue's
    after the one it went on from — are two units. Reads roles and their
    order alone, never the mode. Returns the rows taken, newest first;
    [] where the chain does not end on a reply."""
    popped: list[Message] = []
    while chain and chain[-1].role in ("assistant", "tool"):
        row = chain.pop()
        popped.append(row)
        if row.role == "assistant" and not (chain and chain[-1].role == "tool"):
            break
    return popped


def unit_start(rows: Sequence[Message], end: int) -> int:
    """Where the reply ending at `rows[end - 1]` begins as ONE unit — the
    unit `take_reply_unit` takes: the reply, and where a tool turn stands
    right before it, that turn and the reply holding its call, again and
    again. A row that is no reply begins its own."""
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


def build_job(app: App, story_id: int, *, force: bool = False) -> Job:
    """The worker job for a story as it stands: the story, the model
    extraction asks, the extraction settings, and the snapshot the
    warm-up rebuilds the next request from, with the model it goes to.
    The one composer, so play's idle arming and the forced close can
    never disagree on the snapshot."""
    config, prompts = app._config(), app._prompts()
    settings = settings_of(app, story_id)
    extraction, model = app._extraction_model(), app._model()
    return Job(
        extraction_provider=extraction.provider if extraction is not None else "",
        extraction_model=extraction.name if extraction is not None else "",
        provider=model.provider if model is not None else "",
        model=model.name if model is not None else "",
        story_id=story_id,
        system=app._store.stories.get_system(story_id),
        messages=app._store.stories.get_messages(story_id),
        injections=settings.injections,
        tool_set=settings.tool_set,
        prompts=prompts,
        shape=ContextShape(
            head_messages=config.head_messages,
            min_tail_messages=config.min_tail_messages,
            # the limit alone; the warm-up finishes it with the model's window
            context_in_force=config.context_limit,
        ),
        extraction_settings=ExtractionSettings(
            extract_template=prompts.extract_prompt,
            scene_history_template=prompts.scene_history_prompt,
            journal_history_template=prompts.journal_history_prompt,
            settle=config.settle_messages,
            min_chars=config.scene_min_chars,
            min_messages=config.scene_min_messages,
        ),
        force=force,
    )


def drop_unplayed_card(app: App, story_id: int, popped: list[Message]) -> None:
    """Undoing a card import takes the character with it — while they are
    UNPLAYED: the import wrote the row, the undo unwrites it, and the
    duplicate refusal stops blocking a retry (the reason imports get
    undone at all). A journal marks them played — extraction wove them
    into the story — and then the cast row stays: a character with
    history is story, not import residue."""
    for row in popped:
        if row.kind != "card" or row.speaker_id is None:
            continue
        journaled = any(
            j.character_id == row.speaker_id for j in app._store.journals.list(story_id)
        )
        if not journaled:
            app._store.characters.delete(row.speaker_id)
