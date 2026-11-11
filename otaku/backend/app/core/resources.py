"""The installation as a frontend reaches it — the app's own
resources, over the App (`app.core.services`): the running otaku
(`AppResource`), its version, what its launch had to say, and the
model each role asks, the writing one — the model in use — read with
what it can do; the background worker's one line (`StatusResource`);
and what is said, written down by the model saved for dictation
(`DictationResource`). The reads ask nothing across the wire; how a
model is switched and pinned is the domain's (`app.providers.services`)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, overload

from otaku import __version__
from otaku.backend.app.core import services as domain_core
from otaku.backend.app.core.schemas import (
    AppInfo,
    Dictation,
    DictationCheck,
    LaunchFacts,
    ListedModel,
    ModelInUse,
)
from otaku.backend.app.core.services import App
from otaku.backend.app.machine.resources import MachineResource
from otaku.backend.app.models import services as domain_models
from otaku.backend.app.providers import services as domain_providers
from otaku.backend.base import UNCHANGED, Changed, Resource, Unchanged, any_thread
from otaku.backend.errors import Invalid
from otaku.backend.story.settings.services import StorySettingLayout
from otaku.settings.state import ModelRef, ModelRole


class AppResource(Resource):
    """/app — otaku as it runs: the model it plays with, the one each
    role asks, and what its launch had to say."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.status = StatusResource(app)
        self.machine = MachineResource(app)
        self.dictation = DictationResource(app)

    def get(self) -> AppInfo:
        """otaku as it stands — read off what is cached, nothing asked of
        an engine — and what the launch that opened it had to say."""
        app = self._app
        model_facts = domain_models.model_facts(app)
        launch_facts = domain_core.launch_facts(app)
        models = {role: domain_models.model_for(app, role) for role in ModelRole}
        row = model_facts.row
        return AppInfo(
            version=__version__,
            launch_facts=LaunchFacts(
                notices=launch_facts.notices,
                hint=launch_facts.hint,
                newer_version=launch_facts.newer_version,
            ),
            model=(
                ModelInUse(
                    provider=model_facts.model.provider,
                    name=model_facts.model.name,
                    listed_name=domain_models.listed_name(app, model_facts.model),
                    context_window=row.context_window if row is not None else None,
                    capabilities=row.capabilities if row is not None else None,
                    can_complete_text=model_facts.can_complete_text,
                    can_attach=model_facts.can_attach(StorySettingLayout.default_layout),
                )
                if model_facts.model is not None
                else None
            ),
            models={
                role: _listed_model(app, model)
                for role, model in models.items()
                if model is not None
            },
            on_cloud=model_facts.on_cloud,
        )

    def update(
        self, *, models: Mapping[ModelRole, ModelRef | None] | Unchanged = UNCHANGED
    ) -> Changed[AppInfo]:
        """A model pinned for each role `models` names, in the roles'
        order — a refusal stops there, the role before it written — and
        what each write says. The writing one is played on from now on, its
        saved settings following it, and is never unpinned; None unpins
        another — extraction follows the writing one again, dictation has
        none. Raises Invalid with nothing to change, and whatever each
        write refuses."""
        app = self._app
        said: list[str] = []
        named = {} if isinstance(models, Unchanged) else models
        for role in (role for role in ModelRole if role in named):
            model = named[role]
            said.append(
                domain_providers.pin_model(app, role, model.provider, model.name)
                if model is not None
                else domain_providers.unpin_model(app, role)
            )
        if not said:
            raise Invalid("Nothing to change.")
        return Changed(self.get(), " ".join(said))


class DictationResource(Resource):
    """/app/dictation — what is said, written down by the model saved
    for dictation."""

    @overload
    def create(self, *, audio: bytes, audio_format: str) -> Changed[Dictation]: ...

    @overload
    def create(self, *, validate_only: Literal[True]) -> DictationCheck: ...

    @any_thread
    def create(
        self, *, audio: bytes = b"", audio_format: str = "", validate_only: bool = False
    ) -> Changed[Dictation] | DictationCheck:
        """`audio`, a recording in `audio_format` ("wav"), written down —
        the text, no sentence beside it — or, with `validate_only`, what
        would write it down and the recordings it takes. Safe from any
        thread, and slow on purpose: it reads the files and asks the
        provider, never the store, so no other call waits on a
        recording. Raises Conflict while no dictation model is saved,
        Unavailable for what the engine refuses."""
        app = self._app
        if validate_only:
            model, audio_formats = domain_core.dictation_check(app)
            return DictationCheck(model=_listed_model(app, model), audio_formats=audio_formats)
        return Changed(Dictation(text=domain_core.dictate(app, audio, audio_format)), "")


class StatusResource(Resource):
    """/app/status — the background worker's one line."""

    @any_thread
    def get(self) -> str:
        """The worker's one-line account; "" when idle. Safe from any
        thread."""
        return domain_core.get_status(self._app)


def _listed_model(app: App, model: ModelRef) -> ListedModel:
    """`model` as the app names it."""
    return ListedModel(
        provider=model.provider, name=model.name, listed_name=domain_models.listed_name(app, model)
    )
