"""A story's next request, gathered: what it is made of, exactly as the
assembler would send it (`get_context`), and how full it is
(`get_context_fill`) — the facts the context report is made of. The
report itself, and its text, is the interface's (`api.stories.context`)."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from otaku.backend.app.core.services import App
from otaku.backend.errors import NO_SUCH_STORY, Conflict, NotFound
from otaku.backend.story import request
from otaku.backend.story.settings.services import InjectingSetting, StorySettings, settings_of
from otaku.context.assembler import ContextOverflowError, context_in_force
from otaku.context.prompt import AssembledPrompt, PromptPiece, Provenance, estimate_tokens
from otaku.formatting import printable
from otaku.providers import Locality, ModelInfo, ModelState


@dataclass(frozen=True)
class Preview:
    """A story's next request EXACTLY as it will be sent, as gathered: the
    assembled prompt and its pieces as the wire sends them — over the
    text wire the pieces of its one text, rendered in the template named
    ("" over the chat wire) — their text safe to show (`printable`: a
    stored reply's control bytes never replay on a display), what it
    costs and its percent of the limit, each injection's owner by the
    name the report gives it, the configured tail the rung is compared
    with, and whether the window it was cut to is the assembler's
    stand-in for a model not loaded."""

    prompt: AssembledPrompt
    pieces: tuple[PromptPiece, ...]
    template: str
    # The assembler's estimate: the messages', or over the text wire the
    # one text's, its markers and framing counted.
    total_tokens: int
    used: int  # percent of the limit, over 100 where it cannot fit
    labels: Mapping[str, str]  # an injection's owner → as its setting names what it injects
    tail_setting: int
    cold: bool


def get_context(app: App, story_id: int) -> Preview:
    """Preview a story's next request: what it is made of, and its text
    in pieces, each with where it comes from. No model: the preview still
    stands, over the assembler's default window — what WOULD be sent is
    a question that needs no server. A model not loaded is not loaded
    for a preview, so its window is the substitute too, and the preview
    says so (`cold`). Raises NotFound for a story that is not there,
    Conflict for one that cannot fit."""
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    model = app._model()
    client = app._client(model)
    found = client.models.get(model.name) if client is not None and model is not None else None
    try:
        return _preview(app, story_id, found)
    except ContextOverflowError as e:
        # The preview of a request that would not be sent is its refusal.
        raise Conflict(str(e)) from e


def get_context_fill(app: App, story_id: int | None) -> tuple[int, int] | None:
    """How full a story's next request is — the preview's own figure,
    read after every turn: its percent of the prompt's limit, and the
    context in force — what the story is cut to. None with no story, and
    while the window is not known (no model, a model not loaded, a
    catalog that has not answered): a figure against the assembler's
    stand-in would mislead. Never waits on the internet — a local engine
    is asked, as the header asks it, and anything else answers from what
    is cached. Raises NotFound for a story that is not there."""
    if story_id is None:
        return None
    if not app._store.stories.exists(story_id):
        raise NotFound(NO_SUCH_STORY)
    model = app._model()
    client = app._client(model)
    if model is None or client is None:
        return None
    try:
        if client.locality_of(model.name) is Locality.LOCAL:
            found = client.models.get(model.name)
        else:
            found = client.models.cached(model.name)
    except Exception:
        return None
    window = found.context_window if found is not None else None
    if not window:
        return None
    try:
        preview = _preview(app, story_id, found)
    except ContextOverflowError as e:
        return _percent(e.tokens, e.limit), e.context_in_force
    return preview.used, context_in_force(window, app._config().context_limit)


def _preview(app: App, story_id: int, found: ModelInfo | None) -> Preview:
    """A story's next request over the model's row as the caller found
    it — the one computation both reports read their figure from.
    Raises `ContextOverflowError` for a story that cannot fit."""
    context_window = found.context_window if found else None
    prompt = request.assemble(app, story_id, context_window)
    settings = settings_of(app, story_id)
    # Rendered as the turn renders it: over the text wire the request is
    # one text, and its pieces are cut out of it.
    template = app._model_facts().wire_template(settings.layout)
    if template is None:
        pieces, total = prompt.pieces, prompt.total_tokens
    else:
        pieces = tuple(prompt.text_pieces(template))
        total = estimate_tokens(prompt.as_text(template))
    return Preview(
        prompt=prompt,
        pieces=tuple(replace(piece, text=printable(piece.text)) for piece in pieces),
        template=template.name if template is not None else "",
        total_tokens=total,
        used=_percent(total, prompt.limit),
        labels={sent.owner: _label(settings, sent.owner) for sent in prompt.injections},
        tail_setting=app._config().min_tail_messages,
        cold=(
            found is not None
            and context_window is None
            and found.state in (ModelState.UNLOADED, ModelState.LOADING)
        ),
    )


def separate(previous: Provenance, current: Provenance) -> bool:
    """Whether a part of the preview stands apart from the one before it,
    by where each comes from — a separator between them: the one rule
    every frontend draws by."""
    match previous, current:
        # a header — the summary's, the pinned messages' — opens what follows it
        case Provenance.HEADER | Provenance.PINNED_HEADER, _:
            return False
        # a run reads as one: of messages, of the summary's history and
        # scene summaries, of the pinned messages one header heads
        case (
            (Provenance.MESSAGE, Provenance.MESSAGE)
            | (Provenance.HISTORY | Provenance.SCENE, Provenance.SCENE)
            | (Provenance.PINNED_MESSAGE, Provenance.PINNED_MESSAGE)
        ):
            return False
        # every other part stands apart: the system's, a card, an injection
        case _:
            return True


def _percent(tokens: int, limit: int) -> int:
    """What a request costs as a percent of its limit — over 100 where it
    cannot fit."""
    return round(100 * tokens / limit) if limit else 0


def _label(settings: StorySettings, owner: str) -> str:
    """What an injection is named in the report: as its setting names what
    it injects — or, for a card's reminder ("Maren's reminder") and an
    owner this build has no setting for, as its owner is named."""
    setting = settings.get(owner)
    return setting.injection_label if isinstance(setting, InjectingSetting) else owner
