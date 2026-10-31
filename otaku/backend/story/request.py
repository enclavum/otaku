"""A story's next request, as the backend's own modules build it: the
one binding of its stored premise, settings and chain to the assembler
— what its settings and its cards inject among it — and the worker's
job over the same snapshot, so the warm-up rebuilds the prefix the turn
sends."""

import tomllib
from collections.abc import Sequence

from otaku.agents import Job
from otaku.agents.extraction import ExtractionSettings
from otaku.backend.app.core.services import App
from otaku.backend.story.settings.services import StorySettings, settings_of
from otaku.context import assembler
from otaku.context.assembler import ContextShape
from otaku.context.cards import card_injections
from otaku.context.injections import Injection
from otaku.context.prompt import AssembledPrompt
from otaku.providers import PicturesRide
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
    (`as_text`): held, and counted as held. The injections are
    `_all_injections`', the warm-up's job's too, so it rebuilds the same
    prefix. Raises `ContextOverflowError` when the story cannot fit the
    limit even fully degraded."""
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
        injections=_all_injections(app, story_id, settings, chain),
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


def build_job(app: App, story_id: int, *, force: bool = False) -> Job:
    """The worker job for a story as it stands: the story, the model
    extraction asks, the extraction settings, and the snapshot the
    warm-up rebuilds the next request from, with the model it goes to.
    The one composer, so play's idle arming and the forced close can
    never disagree on the snapshot."""
    config, prompts = app._config(), app._prompts()
    settings = settings_of(app, story_id)
    chain = app._store.stories.get_messages(story_id)
    extraction, model = app._extraction_model(), app._model()
    return Job(
        extraction_provider=extraction.provider if extraction is not None else "",
        extraction_model=extraction.name if extraction is not None else "",
        provider=model.provider if model is not None else "",
        model=model.name if model is not None else "",
        story_id=story_id,
        system=app._store.stories.get_system(story_id),
        messages=chain,
        injections=_all_injections(app, story_id, settings, chain),
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


def _all_injections(
    app: App, story_id: int, settings: StorySettings, chain: Sequence[Message]
) -> tuple[Injection, ...]:
    """What a request over `chain` carries besides the story: the story's
    settings' injections (the canvas's standing instruction among them,
    gated on the layout), then what each card in the chain injects
    (`context.cards.card_injections`) — its system prompt after the
    premise, its reminder at its depth — read from its character's
    profile at request time, so a correction reaches the next request
    and an undone import takes them with it. A profile that is no TOML
    asks nothing; one card brought in twice asks once."""
    carded = {c.id: c for c in app._store.characters.list(story_id) if c.profile}
    asked: list[Injection] = []
    seen: set[int] = set()
    for message in chain:
        who = carded.get(message.speaker_id or 0) if message.kind == "card" else None
        if who is None or not who.profile or message.speaker_id in seen:
            continue
        seen.add(message.speaker_id or 0)
        try:
            asked += card_injections(who.profile, char=who.name)
        except tomllib.TOMLDecodeError:
            continue
    return settings.injections + tuple(asked)
