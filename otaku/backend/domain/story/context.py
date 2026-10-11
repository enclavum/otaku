"""A story's next request, gathered: what it is made of, exactly as the
assembler would send it (`preview`), and how full it is (`context_fill`)
— the facts the context report is made of. The report itself, and its
text, is the interface's (`api.stories.context`)."""

from dataclasses import dataclass

from otaku.backend.domain.app import App
from otaku.backend.domain.story import _chain
from otaku.backend.domain.story.settings import InjectingSetting, settings_of
from otaku.backend.errors import NO_SUCH_STORY, Conflict, NotFound
from otaku.context.assembler import (
    AssembledPrompt,
    ContextOverflowError,
    context_in_force,
    estimate_tokens,
)
from otaku.formatting import format_context, printable
from otaku.providers import Locality, ModelInfo, ModelState
from otaku.store.schema import CompletionType, InjectionPosition


@dataclass(frozen=True)
class Injected:
    """One text the request carries besides the story: named as its
    setting names what it injects, where it rides, what it costs."""

    label: str
    position: InjectionPosition
    tokens: int


@dataclass(frozen=True)
class Part:
    """One message of the request as the wire will carry it, and how
    many pictures ride it."""

    role: str
    body: str
    pictures: int = 0


@dataclass(frozen=True)
class Preview:
    """A story's next request EXACTLY as it will be sent: the assembled
    prompt, the configured tail the rung is compared with, one part per
    message — or, over the text wire, the one text (`wire`) and the
    template it is rendered in — the injections in the request's order,
    and what the preview could not know (`note`, "" when it knew
    everything)."""

    prompt: AssembledPrompt
    tail_setting: int
    parts: tuple[Part, ...]
    injections: tuple[Injected, ...]
    note: str
    wire: str
    template: str


def get_context(app: App, story_id: int) -> Preview:
    """Preview a story's next request: what it is made of, said in words,
    and one part per message it will carry. No model: the preview still
    stands, over the assembler's default window — what WOULD be sent is
    a question that needs no server. A model not loaded is not loaded
    for a preview, so its window is the substitute too, and the report
    says so. Raises NotFound for a story that is not there."""
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
    """How full a story's next request is — `preview`'s own figure, read
    after every turn: its percent of the prompt's limit, and the context
    in force — what the story is cut to. None with no story, and while the window is not
    known (no model, a model
    not loaded, a catalog that has not answered): a figure against the
    assembler's stand-in would mislead. Never waits on the internet — a
    local engine is asked, as the header asks it, and anything else
    answers from what is cached."""
    model = app._model()
    client = app._client(model)
    if model is None or client is None:
        return None
    if story_id is None or not app._store.stories.exists(story_id):
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
        report = _preview(app, story_id, found)
    except ContextOverflowError as e:
        return (round(100 * e.tokens / e.limit) if e.limit else 0), e.context_in_force
    return used(report.prompt, report.wire), context_in_force(window, app._config().context_limit)


def _preview(app: App, story_id: int, found: ModelInfo | None) -> Preview:
    """A story's next request over the model's row as the caller found
    it — the one computation both reports read their figure from.
    Raises `ContextOverflowError` for a story that cannot fit."""
    context_window = found.context_window if found else None
    prompt = _chain.assemble(app, story_id, context_window)
    cold = (
        found is not None
        and context_window is None
        and found.state in (ModelState.UNLOADED, ModelState.LOADING)
    )
    # An injection names its owner, a setting; the report names the text
    # as the setting names what it injects. One this build has no
    # setting for keeps its owner's name. Listed in the request's own
    # order — the system message first, then the deepest first, the one
    # before the newest message last; the costlier first where two share
    # a place — whatever order they were sent in.
    settings = settings_of(app, story_id)
    injections = [
        Injected(
            label=(
                setting.injection_label
                if isinstance(setting := settings.get(sent.owner), InjectingSetting)
                else sent.owner
            ),
            position=sent.position,
            tokens=sent.tokens,
        )
        for sent in prompt.injections
    ]
    injections.sort(key=lambda each: (-(each.position.depth or 99), -each.tokens))
    # Over the text wire the request is one text: the preview is that
    # text, as sent, in one part — its paragraph breaks kept, being its seams.
    facts = app._model_facts()
    text_wire = facts.completion_type(settings.layout) is CompletionType.TEXT
    template = facts.text_template if text_wire else None
    wire = prompt.as_text(template) if template is not None else ""
    parts = (
        (Part("prompt", "\n".join(_preview_body(printable(wire), keep_breaks=True))),)
        if template is not None
        else tuple(
            Part(
                turn.role,
                "\n".join(
                    _preview_body(
                        printable(turn.body),
                        keep_breaks=bool(prompt.recap and prompt.recap in turn.body),
                    )
                ),
                pictures=len(turn.images),
            )
            for turn in prompt.messages
        )
    )
    return Preview(
        prompt=prompt,
        tail_setting=app._config().min_tail_messages,
        injections=tuple(injections),
        parts=parts,
        wire=wire,
        template=template.name if template is not None else "",
        note=(
            f"The model is not loaded: the window is {format_context(prompt.limit)} of the "
            "assembler's own until the next turn loads it."
            if cold
            else ""
        ),
    )


def total_tokens(prompt: AssembledPrompt, wire: str) -> int:
    """What a request costs, at the assembler's estimate: the messages',
    or the text's where the text is the wire — its markers and framing
    counted."""
    return estimate_tokens(wire) if wire else prompt.total_tokens


def used(prompt: AssembledPrompt, wire: str) -> int:
    """A request's percent of its limit — over 100 where it cannot fit."""
    limit = prompt.limit
    return round(100 * total_tokens(prompt, wire) / limit) if limit else 0


def _preview_body(text: str, *, keep_breaks: bool) -> list[str]:
    """Content lines for the preview. Blank lines are dropped to keep it
    tight, EXCEPT where `keep_breaks`: in the turn carrying the recap,
    whose paragraph breaks separate one scene summary from the next (and
    the last summary from any message text merged in after it), and in
    the text wire's one part, whose breaks are the seams between turns —
    runs of them collapsed to one."""
    if keep_breaks:
        out: list[str] = []
        for line in text.splitlines():
            if line.strip():
                out.append(line)
            elif out and out[-1] != "":
                out.append("")  # collapse runs, keep one
        return out
    return [line for line in text.splitlines() if line.strip()]
