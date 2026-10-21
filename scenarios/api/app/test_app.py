"""The app itself: the model it plays with — /model, switched and
remembered, written down as its provider lists it — and the one lore
extraction asks, pinned for it or following the model in use.
"""

import pytest

from otaku.backend.api import ModelRef, Refused
from otaku.backend.app.core.schemas import AppInfo, ModelInUse
from otaku.backend.app.paths import Paths
from otaku.terminal.screens import models as screen_models
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config, set_config_provider
from scenarios.support.server import ModelServer

# Key escape sequences, for pipe input.
_DOWN = "\x1b[B"


class TestModel:
    def test_a_direct_switch_keeps_the_context_and_changes_the_wire(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.play("/model generic/other-model")
        app.play("I look around.")
        assert app.server.requests[-1]["model"] == "other-model"
        # The story context traveled with the switch.
        assert [m.body for m in app.messages][:2] == [
            "I enter the hall.",
            scripted.CHAT_REPLY,
        ]

    def test_the_switch_is_remembered_as_last_used(self, app: App) -> None:
        app.play("/model generic/other-model")
        relaunched = launch(app.paths.root, app.server)
        assert _model(relaunched) == "other-model"
        relaunched.close()

    def test_a_model_name_with_a_quote_survives_the_state_file(self, app: App) -> None:
        app.play('/model generic/oddly"named')
        relaunched = launch(app.paths.root, app.server)
        assert _model(relaunched) == 'oddly"named'
        relaunched.close()

    def test_a_saved_parameter_the_model_rejects_is_said_at_the_switch(
        self, app: App, capsys
    ) -> None:
        # A hand-edited models.toml is the only way in. The launch says
        # such a thing before the banner; a switch has to say it where it
        # happens, or the session drops a parameter in silence.
        app.paths.models_file.write_text("[other-model]\nbogus = 1\n")
        capsys.readouterr()
        app.play("/model generic/other-model")
        assert "bogus" in capsys.readouterr().out
        assert "bogus" not in _params(app)

    def test_cancelling_the_picker_keeps_the_model(self, app: App, monkeypatch) -> None:
        monkeypatch.setattr(screen_models, "pick", lambda *args, **kwargs: None)
        app.play("/model")
        assert _model(app) == "test-model"

    def test_a_model_chosen_is_written_down_as_its_provider_lists_it(self, tmp_path) -> None:
        # Ollama lists a bare name under its ":latest" tag: a model chosen
        # without the tag is remembered as listed, so its own settings are
        # saved under the name the engine serves — and the spelling given
        # stands where nothing lists the model.
        server = scripted.ModelServer(models=("test-model:latest",), managed=True)
        try:
            set_config_provider(tmp_path / "state", server, name="ollama")
            app = launch(tmp_path / "state", server, spec="ollama/test-model")
            try:
                assert _spec(app) == _listed(app) == "ollama/test-model:latest"
                app.play("/model ollama/absent")
                assert _spec(app) == _listed(app) == "ollama/absent"
            finally:
                app.close()
        finally:
            server.close()

    def test_a_model_remembered_otherwise_is_rowed_as_listed(self, tmp_path) -> None:
        # A state.toml written before the rule names the bare spelling: the
        # model in use stays as remembered, and both pickers still find it
        # where its provider rows it (`listed_name`).
        server = scripted.ModelServer(models=("test-model:latest",), managed=True)
        try:
            root = tmp_path / "state"
            set_config_provider(root, server, name="ollama")
            Paths.resolve(root).state_file.write_text('model = "ollama/test-model"\n')
            app = launch(root, server, spec=None)
            try:
                assert _spec(app) == "ollama/test-model"
                assert _listed(app) == "ollama/test-model:latest"
            finally:
                app.close()
        finally:
            server.close()

    def test_a_model_ollama_serves_from_the_cloud_is_on_the_cloud(self, tmp_path) -> None:
        # The session reads where the MODEL runs: the prompt's cloud
        # marker says so, and the banner's context ask is not sent
        # across the internet — while a model the same Ollama runs here
        # stays local.
        server = scripted.ModelServer(models=("local-model", "cloud"), managed=True)
        server.remote = {"cloud"}
        server.loaded = {"local-model"}
        try:
            set_config_provider(tmp_path / "state", server, name="ollama")
            app = launch(tmp_path / "state", server, spec="ollama/cloud")
            try:
                assert _info(app).on_cloud
                assert _window(app) is None
                app.play("/model ollama/local-model")
                assert not _info(app).on_cloud
                assert _window(app) == 8192
            finally:
                app.close()
        finally:
            server.close()


class TestExtractionModel:
    """Lore extraction asks the story's model unless one is saved for
    it: saved from either picker, remembered, kept across a switch of
    the story's model, and unset back to it."""

    def test_a_saved_model_is_remembered_and_kept_across_a_switch(self, app: App) -> None:
        app.api.app.update(extraction_model=ModelRef("generic", "extract-model"))
        app.play("/model generic/other-model")
        assert _extraction(app) == "generic/extract-model"
        relaunched = launch(app.paths.root, app.server)
        try:
            assert _pinned(relaunched) == "generic/extract-model"
            assert _model(relaunched) == "other-model"
        finally:
            relaunched.close()

    def test_unset_returns_extraction_to_the_story_model(self, app: App) -> None:
        app.api.app.update(extraction_model=ModelRef("generic", "extract-model"))
        app.api.app.update(extraction_model=None)
        assert _pinned(app) == ""
        assert _extraction(app) == "generic/test-model"
        app.play("/model generic/other-model")
        assert _extraction(app) == "generic/other-model"

    def test_the_saved_model_again_or_an_unset_of_none_changes_nothing(self, app: App) -> None:
        # Idempotent: what already stands answers success and writes
        # nothing; a provider that is not configured is refused.
        app.api.app.update(extraction_model=None)
        assert _pinned(app) == ""
        app.api.app.update(extraction_model=ModelRef("generic", "extract-model"))
        app.api.app.update(extraction_model=ModelRef("generic", "extract-model"))
        with pytest.raises(Refused):
            app.api.app.update(extraction_model=ModelRef("nowhere", "extract-model"))
        assert _extraction(app) == "generic/extract-model"

    def test_the_pass_asks_the_saved_model_and_the_turns_the_story_model(self, app: App) -> None:
        app.api.app.update(extraction_model=ModelRef("generic", "extract-model"))
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        chats = [r for r in app.server.requests if "messages" in r]
        analyst = [
            r
            for r in chats
            if "You are a story analyst" in scripted.content_text(r["messages"][-1])
        ]
        assert {r["model"] for r in analyst} == {"extract-model"}
        assert {r["model"] for r in chats if r not in analyst} == {"test-model"}
        story_id = app.story_id
        made = {(s.provider, s.model) for s in app.store.scenes.list(story_id)}
        assert made == {("generic", "extract-model")}

    def test_a_hosted_extraction_model_lights_the_cloud_marker(
        self, server: ModelServer, tmp_path
    ) -> None:
        # What is typed leaves the machine on the next idle pass: the
        # marker says so while idle extraction runs, and nothing is known
        # to leave while it is off.
        for enabled in (True, False):
            root = tmp_path / str(enabled) / "state"
            set_config(root, lore_enabled=enabled)
            set_config_provider(root, server, name="llamacpp")
            set_config_provider(root, server, name="openrouter")
            app = launch(root, server, spec="llamacpp/test-model")
            try:
                assert not _info(app).on_cloud
                app.api.app.update(extraction_model=ModelRef("openrouter", "cloud-model"))
                assert _info(app).on_cloud is enabled
            finally:
                app.close()

    def test_the_picker_marks_only_a_saved_model(self, app: App) -> None:
        assert _pinned(app) == ""
        app.api.app.update(extraction_model=ModelRef("generic", "test-model"))
        assert _pinned(app) == "generic/test-model"


def _info(app: App) -> AppInfo:
    return app.api.app.get()


def _in_use(app: App) -> ModelInUse:
    model = _info(app).model
    assert model is not None, "no model in use"
    return model


def _spec(app: App) -> str:
    """The model in use as state.toml names it, "provider/name"."""
    return f"{_in_use(app).provider}/{_in_use(app).name}"


def _listed(app: App) -> str:
    """The model in use as its provider lists it — where a picker rows it."""
    return f"{_in_use(app).provider}/{_in_use(app).listed_name}"


def _extraction(app: App) -> str:
    """The model lore extraction asks, "provider/name"; "" for none."""
    model = _info(app).extraction_model
    return f"{model.provider}/{model.name}" if model is not None else ""


def _pinned(app: App) -> str:
    """The model pinned for lore extraction, "provider/name"; "" while
    extraction follows the model in use."""
    model = _info(app).extraction_model
    return f"{model.provider}/{model.name}" if model is not None and model.pinned else ""


def _window(app: App) -> int | None:
    """The context window of the model in use; None while not known."""
    model = _info(app).model
    return model.context_window if model is not None else None


def _model(app: App) -> str:
    """The bare name of the model in use; "" for none."""
    model = _info(app).model
    return model.name if model is not None else ""


def _params(app: App) -> dict[str, object]:
    """The parameters saved for the model in use, each its kind's own
    value."""
    return {p.name: p.value for p in app.model_settings().parameters if p.value is not None}
