"""A provider's models and a model's own settings as a frontend reaches
them: the models a provider serves and the ones it loads
(`ModelsResource`), and a model's thinking level and parameters, saved
under its name (`ModelSettingsResource`). The work is the domain's
(`app.models.services`)."""

from __future__ import annotations

from collections.abc import Mapping

from otaku.backend.app.core.services import App
from otaku.backend.app.models import services as domain_models
from otaku.backend.app.models.schemas import (
    Model,
    ModelSettings,
    ParameterSetting,
    ThinkChoices,
    as_model,
)
from otaku.backend.app.models.services import parameter_text
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged
from otaku.backend.errors import Invalid


class ModelsResource(Resource):
    """/providers/{provider}/models — the models a provider serves, and
    the ones it loads."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
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


class ModelSettingsResource(Resource):
    """/providers/{provider}/models/{model}/settings — a model's own
    settings, saved under its name: its thinking level and its
    parameters."""

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
