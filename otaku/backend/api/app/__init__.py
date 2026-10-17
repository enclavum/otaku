"""The installation as a frontend reaches it — the twin of
`domain.app`, each module of this package the twin of the domain's of
its name. Here the running otaku (`AppResource`): its version, what its
launch had to say, the model it plays with and the one lore extraction
asks — each read with what the model in use can do, and written here —
and the background worker's one line (`StatusResource`). The reads ask
nothing across the wire; how a switch and a pin are made is the
domain's (`domain.app.providers`)."""

from __future__ import annotations

from dataclasses import dataclass

from otaku import __version__
from otaku.backend.api._writes import UNCHANGED, Changed, Unchanged
from otaku.backend.api.app.machine import MachineResource
from otaku.backend.domain import app as domain_app
from otaku.backend.domain.app import App
from otaku.backend.domain.app import models as domain_models
from otaku.backend.domain.app import providers as domain_providers
from otaku.backend.domain.story.settings import StorySettingLayout
from otaku.backend.errors import Invalid
from otaku.providers import ModelCapabilities
from otaku.settings.state import ModelRef


class AppResource:
    """/app — otaku as it runs: the model it plays with, the one lore
    extraction asks, and what its launch had to say."""

    def __init__(self, app: App) -> None:
        self._app = app
        self.status = StatusResource(app)
        self.machine = MachineResource(app)

    def get(self) -> AppInfo:
        """otaku as it stands — read off what is cached, nothing asked of
        an engine — and what the launch that opened it had to say."""
        app = self._app
        facts = domain_models.model_facts(app)
        launch = domain_app.launch_facts(app)
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


class StatusResource:
    """/app/status — the background worker's one line."""

    def __init__(self, app: App) -> None:
        self._app = app

    def get(self) -> str:
        """The worker's one-line account; "" when idle. Safe from any
        thread."""
        return domain_app.get_status(self._app)


@dataclass(frozen=True)
class AppInfo:
    """otaku as it runs: its version, what the launch had to say, the
    model in use (None for none), the model lore extraction asks (None
    with neither a pin nor a model in use), and whether what is typed
    leaves this machine — the model in use, or idle extraction's, on a
    hosted catalog."""

    version: str
    launch: Launch
    model: ModelInUse | None
    extraction_model: ExtractionModel | None
    on_cloud: bool


@dataclass(frozen=True)
class Launch:
    """What the launch that opened the app had to say: its notices, and
    the hint over the resumed scene."""

    notices: tuple[str, ...]
    hint: str


@dataclass(frozen=True)
class ModelInUse:
    """The model in use: its provider and its name as state.toml names
    it — what its own settings are saved under — and as its provider
    lists it (`listed_name`, where a picker rows it), its context window
    as last listed (None where nothing is known), what it can do as its
    provider states it (None where it says nothing of the model — a flag
    unknown reads as no), whether the novel is offered on it — its
    engine completes raw text and the template in force is `plain` — and
    whether a picture may ride the first line of a story not made yet,
    read in the layout a story starts in (`Story.can_attach` once there
    is one)."""

    provider: str
    name: str
    listed_name: str
    context_window: int | None
    capabilities: ModelCapabilities | None
    can_complete_text: bool
    can_attach: bool


@dataclass(frozen=True)
class ExtractionModel:
    """The model lore extraction asks: its provider and name, its name as
    its provider lists it (`listed_name`, where a picker rows it), and
    whether it is pinned for extraction — else it is the model in use,
    followed as that changes."""

    provider: str
    name: str
    listed_name: str
    pinned: bool
