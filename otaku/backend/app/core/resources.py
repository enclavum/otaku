"""The installation as a frontend reaches it — the app's own
resources, over the App (`app.core.services`): the running otaku
(`AppResource`), its version, what its launch had to say, the model it
plays with and the one lore extraction asks — each read with what the
model in use can do, and written here — and the background worker's one
line (`StatusResource`). The reads ask
nothing across the wire; how a switch and a pin are made is the
domain's (`app.providers.services`)."""

from __future__ import annotations

from otaku import __version__
from otaku.backend.app.core import services as domain_core
from otaku.backend.app.core.schemas import AppInfo, ExtractionModel, Launch, ModelInUse
from otaku.backend.app.core.services import App
from otaku.backend.app.machine.resources import MachineResource
from otaku.backend.app.models import services as domain_models
from otaku.backend.app.providers import services as domain_providers
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged
from otaku.backend.errors import Invalid
from otaku.backend.story.settings.services import StorySettingLayout
from otaku.settings.state import ModelRef


class AppResource(Resource):
    """/app — otaku as it runs: the model it plays with, the one lore
    extraction asks, and what its launch had to say."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.status = StatusResource(app)
        self.machine = MachineResource(app)

    def get(self) -> AppInfo:
        """otaku as it stands — read off what is cached, nothing asked of
        an engine — and what the launch that opened it had to say."""
        app = self._app
        facts = domain_models.model_facts(app)
        launch = domain_core.launch_facts(app)
        extracting = domain_models.extraction_model(app)
        row = facts.row
        return AppInfo(
            version=__version__,
            launch=Launch(notices=launch.notices, hint=launch.hint),
            model=(
                ModelInUse(
                    provider=facts.model.provider,
                    name=facts.model.name,
                    listed_name=domain_models.listed_name(app, facts.model),
                    context_window=row.context_window if row is not None else None,
                    capabilities=row.capabilities if row is not None else None,
                    can_complete_text=facts.can_complete_text,
                    can_attach=facts.can_attach(StorySettingLayout.default_layout),
                )
                if facts.model is not None
                else None
            ),
            extraction_model=(
                ExtractionModel(
                    provider=extracting.provider,
                    name=extracting.name,
                    listed_name=domain_models.listed_name(app, extracting),
                    pinned=domain_models.extraction_pin(app) is not None,
                )
                if extracting is not None
                else None
            ),
            on_cloud=facts.on_cloud,
        )

    def update(
        self,
        *,
        model: ModelRef | Unchanged = UNCHANGED,
        extraction_model: ModelRef | Unchanged | None = UNCHANGED,
    ) -> Changed[AppInfo]:
        """The fields given, in this order — a refusal stops there, the
        field before it written — and what each write says. `model` is
        played on from now on, its saved settings following it;
        `extraction_model` pins a model for lore extraction, None
        following the model in use again. Raises Invalid with nothing to
        change, and whatever each write refuses."""
        app = self._app
        said: list[str] = []
        if not isinstance(model, Unchanged):
            said.append(domain_providers.switch_model(app, model.provider, model.name))
        if not isinstance(extraction_model, Unchanged):
            said.append(
                domain_providers.pin_extraction_model(
                    app, extraction_model.provider, extraction_model.name
                )
                if extraction_model is not None
                else domain_providers.unpin_extraction_model(app)
            )
        if not said:
            raise Invalid("Nothing to change.")
        return Changed(self.get(), " ".join(said))


class StatusResource(Resource):
    """/app/status — the background worker's one line."""

    def get(self) -> str:
        """The worker's one-line account; "" when idle. Safe from any
        thread."""
        return domain_core.get_status(self._app)
