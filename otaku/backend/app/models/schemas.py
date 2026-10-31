"""What `app.models.resources` answers: its schemas."""

from __future__ import annotations

from otaku.backend.app.core.services import App
from otaku.backend.app.models import services as domain_models
from otaku.backend.base import Schema
from otaku.providers import Locality, ModelCapabilities, ModelInfo, ModelState


def as_model(app: App, row: ModelInfo) -> Model:
    """A listed model as a picker draws it, its template and its words
    beside the provider's facts."""
    return Model(
        name=row.name,
        size=row.size,
        context_window=row.context_window,
        context_length=row.context_length,
        context_loaded=row.context_loaded,
        max_output_tokens=row.max_output_tokens,
        capabilities=row.capabilities,
        state=row.state,
        locality=row.locality,
        text_template=domain_models.template_of(app, row),
        reasoning_words=domain_models.reasoning_words(row.capabilities),
        capability_words=domain_models.capability_words(row.capabilities),
    )


class Model(Schema):
    """A model as its provider lists it: its name, size on disk (local
    engines only), the window a request gets, the model's own and the
    loaded instance's where they differ, its longest reply, what it can
    do (None where the provider says nothing of it), whether it is
    loaded, where it is served where that differs from its provider's —
    and the text template the text wire would render it with (None where
    none is known) and how the info report words what it can do."""

    name: str
    size: int | None
    context_window: int | None
    context_length: int | None
    context_loaded: int | None
    max_output_tokens: int | None
    capabilities: ModelCapabilities | None
    state: ModelState
    locality: Locality | None
    text_template: str | None
    reasoning_words: str
    capability_words: str


class ModelSettings(Schema):
    """A model's own settings, saved under its name — the one the path
    names: its thinking level ("unset" where nothing is sent), how that
    reads, and what it takes,
    every parameter the vocabulary knows in its order, the ones set
    (`params`, in the order the file holds them, each as a reader reads
    and types it), and the text template the text wire renders it with
    (None where none is known)."""

    think: str
    think_text: str
    choices: ThinkChoices
    parameters: tuple[ParameterSetting, ...]
    params: dict[str, str]
    text_template: str | None


class ThinkChoices(Schema):
    """What a model's thinking takes: the `levels`, "unset" always first
    (no level: nothing sent), then the ladder's rungs the model grades,
    or off and on where it switches; and whether a `budget` — a number of
    tokens, 0 = off — is taken beside them."""

    levels: tuple[str, ...]
    budget: bool

    @property
    def listed(self) -> str:
        """The choices as one sentence's list."""
        return domain_models.levels_text(self.levels, self.budget)


class ParameterSetting(Schema):
    """One parameter as a model holds it: its value saved for the model
    (None while unset) and as a reader reads and types it (`text`, ""
    while unset), its kind's name ("float", "int", or "list" for the stop
    strings), whether it reaches the model on the provider serving it
    (a list, never a gate: one model is served by more than one
    provider), and the bounds a value is held to — the provider's own
    where it states them — None on a side that has none."""

    name: str
    value: object | None
    text: str
    kind: str
    supported: bool
    low: float | None
    high: float | None
