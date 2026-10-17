"""A character card into a story, always through the codex: a card FILE's
bytes go into the codex first (`domain.codex.cards` — a file it holds
already makes nothing) and the codex card is brought in; a codex card is
brought in as it stands (an invite). Read and vetted for the story,
nothing stored (`check_card`), then brought in (`import_card`), or
several at a story's start (`bring_in_cards`): per card the typed /card
row, the story's character — a COPY of the card's profile as its own,
linked to the codex card — and the greeting turn; NO model call.

The `/card` line lands as a `kind='card'` user turn, stored AS TYPED like
every direction — the wire composes the card block from the profile at
request time, found through the row's speaker link, so a lore correction
to the TOML reaches every future request. Asked to, the row carries the
card's portrait, its first picture, by the file's name. The greeting
lands as an AUTHORED assistant turn, speaker set, provider `card` and the
card file's name for a model — the source, spelled the way generated
rows spell theirs: the card introduces the character and the character
answers, so the model continues the voice."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace

from otaku.backend.domain.app import App
from otaku.backend.domain.codex import cards as codex_cards
from otaku.backend.domain.story import _chain
from otaku.backend.domain.story.settings import settings_of
from otaku.backend.errors import NO_SUCH_STORY, Conflict, HeadMoved, Invalid, NotFound
from otaku.context.assembler import estimate_tokens
from otaku.context.cards import CARD_PROVIDER, CharacterProfile, bind, card_to_wire
from otaku.store.schema import Card, Message, StoryMode

# A card block above roughly this many tokens gets a warning: it rides
# every request verbatim and never leaves the context, so on a small
# local window it crowds out the story itself.
_LARGE_CARD_TOKENS = 4000


@dataclass(frozen=True)
class CheckedCard:
    """A card read and vetted for a story: the profile it would be brought
    in with, what reading it noted, the composed block's cost estimate and
    whether it is worth warning about, the persona the story remembers
    ("" for none), whether a portrait could ride its row, and — for a
    file — the codex card it is already (None for none) and the codex
    cards of the same name the reader may say it is."""

    profile: CharacterProfile
    notes: tuple[str, ...]
    block_tokens: int
    large: bool
    persona: str
    has_picture: bool
    same_card: Card | None
    similar_cards: tuple[Card, ...]


@dataclass(frozen=True)
class BroughtIn:
    """A card brought into a story: the recorded /card row, its
    greeting turn (None where the card has none or another card greets),
    the story's character and the codex card it is linked to."""

    line: Message
    greeting: Message | None
    character_id: int
    card_id: int


def check_card(
    app: App,
    story_id: int,
    *,
    data: bytes | None = None,
    file_name: str = "",
    card_id: int | None = None,
    rename: str = "",
) -> CheckedCard:
    """The card a file holds — or the codex card `card_id` — vetted for the
    story and nothing stored; `rename` overrides its name. Raises Refused
    — not a card, no such codex card, a cast name already taken, the card
    in the story already."""
    if data is not None:
        checked = codex_cards.check_card_file(app, data, file_name)
        same = checked.same_card
        profile = codex_cards.card_profile(same) if same is not None else checked.profile
        notes, has_picture, similar = checked.notes, checked.has_picture, checked.similar_cards
    elif card_id is not None:
        same = codex_cards.get_card(app, card_id)
        profile, notes, has_picture, similar = (
            codex_cards.card_profile(same),
            (),
            bool(same.pictures),
            (),
        )
    else:
        raise Invalid("A card is brought in from a file or from the codex.")
    profile = replace(profile, name=rename.strip() or profile.name)
    _ensure_free(app, story_id, profile.name, same)
    remembered = _remembered_persona(app, story_id)
    tokens = _block_tokens(app, profile, remembered)
    return CheckedCard(
        profile=profile,
        notes=tuple(notes),
        block_tokens=tokens,
        large=tokens > _LARGE_CARD_TOKENS,
        persona=remembered,
        has_picture=has_picture or (same is not None and bool(same.pictures)),
        same_card=same,
        similar_cards=similar,
    )


def import_card(
    app: App,
    story_id: int,
    *,
    data: bytes | None = None,
    file_name: str = "",
    card_id: int | None = None,
    rename: str = "",
    reuse: int | None = None,
    persona: str,
    with_pictures: bool = False,
    line: str = "",
    expected_head: int | None,
) -> tuple[BroughtIn, str]:
    """A card brought into the story, played on from `expected_head`, the
    head the caller read (HeadMoved where the head stands elsewhere,
    nothing written — the codex included): a card FILE goes into the
    codex first (`reuse` the codex card the reader says it is), a codex
    card `card_id` as it stands. Then the typed row (speaker-linked to
    the new cast row, carrying the card's portrait where `with_pictures` asks
    and it has one), the character with a copy of the card's profile,
    linked to the codex card, and the greeting turn — answered with the
    one-line report. `/undo` takes the exchange whole — the unplayed
    character with it; the codex card stays. `line` is what the story
    RECORDS; the default names the file, or the codex card."""
    _check_head(app, story_id, expected_head)
    if data is not None:
        checked = codex_cards.check_card_file(app, data, file_name)
        target = checked.same_card or (
            codex_cards.get_card(app, reuse) if reuse is not None else None
        )
        name = rename.strip() or (target.name if target is not None else checked.profile.name)
        _ensure_free(app, story_id, name, target)
        card_id, made, _ = codex_cards.import_card_file(
            app, data, file_name, rename=rename, reuse=reuse
        )
        notes = [*checked.notes, "added to the codex" if made else "the codex's card"]
        default_line = f"/card {file_name}" + (f" {rename.strip()}" if rename.strip() else "")
    elif card_id is not None:
        target = codex_cards.get_card(app, card_id)
        name = rename.strip() or target.name
        _ensure_free(app, story_id, name, target)
        notes, default_line = [], f"/card {name}"
    else:
        raise Invalid("A card is brought in from a file or from the codex.")
    card = codex_cards.get_card(app, card_id)
    mode = settings_of(app, story_id).layout.mode
    brought, profile = _bring_in(
        app,
        story_id,
        card,
        name=name,
        persona=persona,
        with_pictures=with_pictures,
        line=line or default_line,
        mode=mode,
        after=expected_head,
    )
    greeting = _greet(
        app, story_id, brought.character_id, profile, persona, mode, after=brought.line.id
    )
    tokens = _block_tokens(app, profile, persona)
    verb = "imported" if data is not None else "invited"
    report = [f"{verb} {name} (card ≈ {tokens} tokens)", *notes]
    if tokens > _LARGE_CARD_TOKENS:
        report.append("a large card — it rides every request and never leaves the context")
    return replace(brought, greeting=greeting), " · ".join(report)


def check_cards(app: App, card_ids: Sequence[int]) -> tuple[Card, ...]:
    """The codex cards a story is started with, in pick order — refused
    for a card the codex does not hold, one picked twice, and two of one
    name, which one story's cast cannot hold."""
    if len(set(card_ids)) != len(card_ids):
        raise Invalid("A card is picked twice.")
    picked = tuple(codex_cards.get_card(app, card_id) for card_id in card_ids)
    seen: set[str] = set()
    for card in picked:
        if card.name.casefold() in seen:
            raise Conflict(f"Two of the cards are called {card.name} — rename one first.")
        seen.add(card.name.casefold())
    return picked


def bring_in_cards(
    app: App,
    story_id: int,
    cards: Sequence[Card],
    *,
    card_pictures: Collection[int],
    persona: str,
) -> list[BroughtIn]:
    """Codex cards brought into a story just made, in pick order: a card
    row and a character each — the row carrying the card's portrait for
    a card in `card_pictures` — then the greeting of the first card that has
    one: one greeting, so the story opens on one voice."""
    mode = settings_of(app, story_id).layout.mode
    after: int | None = None
    brought: list[BroughtIn] = []
    greeter: tuple[int, CharacterProfile] | None = None
    for card in cards:
        one, profile = _bring_in(
            app,
            story_id,
            card,
            name=card.name,
            persona=persona,
            with_pictures=card.id in card_pictures,
            line=f"/card {card.name}",
            mode=mode,
            after=after,
        )
        brought.append(one)
        after = one.line.id
        if greeter is None and profile.greeting:
            greeter = (len(brought) - 1, profile)
    if greeter is not None and after is not None:
        index, profile = greeter
        greeting = _greet(
            app, story_id, brought[index].character_id, profile, persona, mode, after=after
        )
        brought[index] = replace(brought[index], greeting=greeting)
    return brought


def _bring_in(
    app: App,
    story_id: int,
    card: Card,
    *,
    name: str,
    persona: str,
    with_pictures: bool,
    line: str,
    mode: StoryMode,
    after: int | None,
) -> tuple[BroughtIn, CharacterProfile]:
    """One codex card's row and character: the typed row (its portrait on
    it where asked and the card has one), the character — the card's
    profile under `name` as its copy, the card's name and aliases its
    aliases, linked to the card — and the row's speaker link. Answered
    with the profile as the story holds it, which its greeting speaks
    from."""
    profile = replace(codex_cards.card_profile(card), name=name, user=persona)
    row = _chain.append_message(
        app,
        story_id,
        Message(
            role="user",
            body=line,
            kind="card",
            mode=mode,
            attachments=card.pictures[:1] if with_pictures else (),
        ),
        after=after,
    )
    aliases = tuple(
        alias
        for alias in dict.fromkeys([card.name, *card.aliases])
        if alias.casefold() != name.casefold()
    )
    # Made by the card, no model — the file it came from is in its profile.
    character_id = app._store.characters.add(
        story_id,
        name,
        aliases=aliases,
        profile=profile.to_toml(),
        provider=CARD_PROVIDER,
        card_id=card.id,
    )
    # The speaker link is the compose key: the row was recorded before
    # the cast row could exist, so the attribution follows it.
    app._store.messages.set_speaker(row.id, character_id, name)
    recorded = replace(row, speaker=name, speaker_id=character_id)
    return BroughtIn(recorded, None, character_id, card.id), profile


def _greet(
    app: App,
    story_id: int,
    character_id: int,
    profile: CharacterProfile,
    persona: str,
    mode: StoryMode,
    *,
    after: int,
) -> Message | None:
    """The profile's greeting as the character's own turn, after `after`;
    None for a card without one."""
    greeting = bind(profile.greeting, char=profile.name, user=persona)
    if not greeting:
        return None
    return _chain.append_message(
        app,
        story_id,
        Message(
            role="assistant",
            body=greeting,
            kind="dialogue",
            speaker=profile.name,
            speaker_id=character_id,
            # The source, spelled the way generated rows spell theirs:
            # no model wrote this — the card's file did.
            provider=CARD_PROVIDER,
            model=profile.file_name or None,
            mode=mode,
        ),
        after=after,
    )


def _check_head(app: App, story_id: int, expected_head: int | None) -> None:
    """The story there, its head where the caller saw it — checked before
    the codex is written, so a refusal leaves both as they were."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    head = app._store.stories.get_head(story_id)
    if head != expected_head:
        raise HeadMoved(head)


def _ensure_free(app: App, story_id: int, name: str, card: Card | None) -> None:
    """Refused where the story's cast already answers to the name —
    bringing the card in over it would double the character — or already
    holds the codex card, under another name."""
    existing = app._store.characters.find(story_id, name)
    if existing is not None:
        source = "imported earlier" if existing.profile else "extracted from play"
        raise Conflict(
            f"{existing.name} already exists in this story ({source}) — "
            f"import under another name: /card FILE NAME."
        )
    if card is not None:
        linked = next(
            (c for c in app._store.characters.list(story_id) if c.card_id == card.id), None
        )
        if linked is not None:
            raise Conflict(f"{card.name} is in this story already, as {linked.name}.")


def _block_tokens(app: App, profile: CharacterProfile, persona: str) -> int:
    """The composed block's cost estimate, over the persona the ask
    defaults to — the answer only swaps a name, never the size class."""
    return estimate_tokens(
        card_to_wire(
            replace(profile, user=persona or "you").to_toml(),
            app._prompts().card_framing,
            char=profile.name,
        )
    )


def _remembered_persona(app: App, story_id: int | None) -> str:
    """A story's memory of who the player is: the `user` binding in the
    latest imported card's profile — so a story asks once and later
    imports default to the same name, with no setting anywhere. "" when
    none, or no story — the frontend asks with its own default then."""
    if story_id is None:
        return ""
    for character in reversed(app._store.characters.list(story_id)):
        profile = codex_cards.story_profile(character)
        if profile is not None and profile.user:
            return profile.user
    return ""
