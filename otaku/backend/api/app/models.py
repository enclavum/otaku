"""A provider's models and a model's own settings as a frontend reaches
them: the models a provider serves and the ones it loads
(`ModelsResource`), and a model's thinking level and parameters, saved
under its name (`ModelSettingsResource`). The work is the domain's
(`domain.app.models`)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from otaku.backend.api._writes import UNCHANGED, Changed, Unchanged
from otaku.backend.domain.app import App
from otaku.backend.domain.app import models as domain_models
from otaku.backend.domain.app.models import parameter_text
from otaku.backend.errors import Invalid
from otaku.providers import Locality, ModelCapabilities, ModelInfo, ModelState


class ModelsResource:
    """/providers/{provider}/models — the models a provider serves, and
    the ones it loads."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.settings = ModelSettingsResource(app)

    def list(self, provider: str) -> list[Model]:
        """A provider's models as its server lists them right now — the
        picker's read-back after a load or unload. Raises Refused when it
        cannot be reached: a refresh that failed quietly would leave the
        panel claiming the opposite of what just happened."""
        return [as_model(self._app, row) for row in domain_models.list_models(self._app, provider)]

    def update(self, provider: str, model: str, *, loaded: bool) -> Changed[Model]:
        """A model loaded or unloaded on a provider that manages its
        models; blocks until the server answers, and answers the model as
        it now stands. Every failure raises Refused with the curated
        sentence (not managed, the provider unreachable, the server's own
        error text)."""
        app = self._app
        said = (
            domain_models.load_model(app, provider, model)
            if loaded
            else domain_models.unload_model(app, provider, model)
        )
        return Changed(as_model(app, domain_models.get_model(app, provider, model)), said)


class ModelSettingsResource:
    """/providers/{provider}/models/{model}/settings — a model's own
    settings, saved under its name: its thinking level and its
    parameters."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self, provider: str, model: str) -> ModelSettings:
        """`model`'s settings on `provider`, as models.toml and the
        provider's description of the model stand: what it takes where
        the provider has described it, everything where it cannot say or
        has not listed the model yet. Provider "" (no model selected)
        answers what any model would take. Raises NotFound for a provider
        that is not configured."""
        facts = domain_models.get_model_settings(self._app, provider, model)
        return ModelSettings(
            think=facts.think,
            think_text=domain_models.think_word(facts.think),
            choices=ThinkChoices(facts.levels, facts.budget),
            parameters=tuple(
                ParameterSetting(
                    name=parameter.name,
                    value=parameter.value,
                    text=parameter_text(parameter.value) if parameter.value is not None else "",
                    kind=parameter.kind,
                    supported=parameter.supported,
                    low=parameter.low,
                    high=parameter.high,
                )
                for parameter in facts.parameters
            ),
            params={name: parameter_text(value) for name, value in facts.saved.items()},
            text_template=facts.text_template,
        )

    def update(
        self,
        provider: str,
        model: str,
        *,
        think: str | Unchanged = UNCHANGED,
        parameters: Mapping[str, object | None] | None = None,
    ) -> Changed[ModelSettings]:
        """The model's thinking level — a rung, off or on, a budget in
        tokens, or "unset", saved too: nothing is sent — and its
        parameters, each a value (the kind's own, or as a reader types
        it, held to the parameter's bounds) or None, which unsets it: the
        engine's own default applies again. Written in that order, a
        refusal stopping there. Raises Invalid with nothing to change,
        and Refused without a model, for a provider not configured, a
        level the model does not take, an unknown parameter, a value it
        cannot read, and one out of bounds."""
        app = self._app
        said: list[str] = []
        if not isinstance(think, Unchanged):
            said.append(domain_models.set_think(app, provider, model, think))
        for name, value in (parameters or {}).items():
            said.append(
                domain_models.delete_parameter(app, provider, model, name)
                if value is None
                else domain_models.set_parameter(app, provider, model, name, value)
            )
        if not said:
            raise Invalid("Nothing to change.")
        return Changed(self.get(provider, model), " ".join(said))


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


@dataclass(frozen=True)
class Model:
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


@dataclass(frozen=True)
class ModelSettings:
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


@dataclass(frozen=True)
class ThinkChoices:
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


@dataclass(frozen=True)
class ParameterSetting:
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
