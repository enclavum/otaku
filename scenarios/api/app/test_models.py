"""A model's own settings and its load: /set think and /set parameter
follow the MODEL they were set on, saved under its name, the values
offered the ones its provider reads; the picker loads and unloads a
managed engine's models.
"""

import contextlib
import threading
import time
import tomllib

import pytest

from otaku.backend.api import NotFound, Refused
from otaku.backend.app.models.schemas import ParameterSetting
from otaku.backend.app.models.services import PARAMETERS, THINK_MENU
from otaku.backend.app.paths import Paths
from otaku.terminal.api import models as api_models
from otaku.terminal.screens import models as screen_models
from scenarios.api.app.test_app import (
    _DOWN,
    _extraction,
    _in_use,
    _model,
    _params,
    _pinned,
    _window,
)
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config_provider
from scenarios.support.screens import ENTER, ESC, run_screen
from scenarios.support.server import ModelServer


class TestThink:
    def test_a_level_is_set_and_remembered(self, app: App, capsys) -> None:
        app.play("/set think high")
        assert "high" in capsys.readouterr().out
        assert _think(app) == "high"
        relaunched = launch(app.paths.root, app.server)
        assert _think(relaunched) == "high"
        relaunched.close()

    def test_the_switch_words_are_refused_on_a_graded_model(self, app: App) -> None:
        # The levels exist, and a model with rungs — or one nobody has
        # described, which is offered every rung — takes neither.
        app.play("/set think high")
        for word in ("on", "off"):
            with pytest.raises(Refused, match=f"does not take {word}"):
                _set_think(app, word)
        assert _think(app) == "high"

    def test_default_means_the_model_decides(self, app: App) -> None:
        app.play("/set think unset")
        assert _think(app) == "unset"
        relaunched = launch(app.paths.root, app.server)
        assert _think(relaunched) == "unset"
        relaunched.close()

    def test_the_level_follows_the_model(self, app: App) -> None:
        # Saved per model beside its parameters: a model nobody described
        # runs unset with no row, and "unset" set by hand is written like
        # a level, "none" included, so it is remembered.
        app.play("/set think high")
        app.play("/model generic/other-model")
        assert _think(app) == "unset"
        app.play("/set think unset")
        app.play("/model generic/test-model")
        assert _think(app) == "high"
        saved = tomllib.loads(app.paths.models_file.read_text())
        assert saved["test-model"]["think"] == "high"
        assert saved["other-model"]["think"] == "unset"
        app.play("/set think none")
        assert tomllib.loads(app.paths.models_file.read_text())["test-model"]["think"] == "none"

    def test_the_level_an_older_state_held_moves_to_its_model(self, server, tmp_path) -> None:
        # Before 0.5.0 the level was one for every model and lived in
        # state.toml. The first launch moves it to the remembered model's
        # entry verbatim — state.toml names "provider/model", models.toml
        # the bare name; "none", what every untouched install held, moves
        # like any other — and drops the key, so the next launch has
        # nothing to move.
        root = tmp_path / "state"
        set_config_provider(root, server)
        paths = Paths.resolve(root)
        paths.state_file.write_text('model = "generic/test-model"\nstory = 0\nthink = "none"\n')
        app = launch(root, server)
        try:
            assert _think(app) == "none"
            saved = tomllib.loads(paths.models_file.read_text())
            assert saved["test-model"]["think"] == "none"
            assert "think" not in tomllib.loads(paths.state_file.read_text())
        finally:
            app.close()

    def test_the_retired_spelling_of_unset_leaves_no_row(self, server, tmp_path) -> None:
        # Releases before 0.5.0 wrote "default" for send-nothing; that is
        # unset now, which is the absence of a row — so nothing is written
        # and the key is simply dropped.
        root = tmp_path / "state"
        set_config_provider(root, server)
        paths = Paths.resolve(root)
        paths.state_file.write_text('model = "generic/test-model"\nthink = "default"\n')
        app = launch(root, server)
        try:
            assert _think(app) == "unset"
            assert "think" not in tomllib.loads(paths.state_file.read_text())
            saved = (
                tomllib.loads(paths.models_file.read_text()) if paths.models_file.exists() else {}
            )
            assert "think" not in saved.get("test-model", {})
        finally:
            app.close()

    def test_a_level_already_moved_stays_and_an_unremembered_one_has_no_home(
        self, server, tmp_path
    ) -> None:
        # Convergence: a launch that wrote models.toml and could not
        # rewrite state.toml is finished, not redone — the entry's level
        # wins; and with no model remembered the level goes nowhere.
        root = tmp_path / "state"
        set_config_provider(root, server)
        paths = Paths.resolve(root)
        paths.models_file.write_text('[test-model]\nthink = "low"\n')
        paths.state_file.write_text('model = "generic/test-model"\nthink = "high"\n')
        app = launch(root, server)
        try:
            assert _think(app) == "low"
            assert "think" not in tomllib.loads(paths.state_file.read_text())
        finally:
            app.close()
        paths.state_file.write_text('model = ""\nthink = "high"\n')
        app = launch(root, server)
        try:
            assert "think" not in tomllib.loads(paths.state_file.read_text())
            assert tomllib.loads(paths.models_file.read_text()) == {"test-model": {"think": "low"}}
        finally:
            app.close()

    def test_only_the_levels_the_model_takes_are_offered(self, tmp_path) -> None:
        # Ollama's card says which models think: a thinking one is on or
        # off, its engine grading nothing, a plain one takes only unset.
        # The card is read as the model becomes current — at launch, on
        # a switch — so the menu is right from the first keystroke; an
        # engine that did not answer loses nothing until it does. The
        # bare report lists the levels, and one the model does not take
        # is refused by name.
        server = scripted.ModelServer(models=("test-model", "plain-model"), managed=True)
        server.capabilities = {
            "test-model": ["completion", "thinking"],
            "plain-model": ["completion"],
        }
        try:
            set_config_provider(tmp_path / "state", server, name="ollama")
            server.ps_status = 500  # the engine does not answer at launch
            app = launch(tmp_path / "state", server, spec="ollama/test-model")
            try:
                assert app.model_settings().choices.levels == THINK_MENU
                server.ps_status = None
                # the status row's ask, before a prompt, reads the card
                with contextlib.suppress(NotFound):
                    app.api.stories.context.fill.get(app.ensure_story())
                levels = ("unset", "off", "on")
                assert app.model_settings().choices.levels == levels
                assert not app.model_settings().choices.budget
                assert api_models.set_think(app.api, "").endswith(", ".join(levels) + ".")
                with pytest.raises(Refused, match="does not take high"):
                    _set_think(app, "high")
                with pytest.raises(Refused, match="does not take a thinking budget"):
                    _set_think(app, "2000")
                assert _think(app) == "unset"  # still unset: the launch could not ask
                app.play("/model ollama/plain-model")  # the switch reads the card
                assert app.model_settings().choices.levels == ("unset",)
                with pytest.raises(Refused, match="does not take none"):
                    _set_think(app, "none")
                with pytest.raises(Refused):
                    _set_think(app, "loud")
                _set_think(app, "unset")
                assert _think(app) == "unset"
            finally:
                app.close()
        finally:
            server.close()

    def test_a_model_the_engine_says_thinks_starts_off(self, tmp_path) -> None:
        # A model made current with no think row starts with its thinking
        # off where the provider says it thinks: the off word of its shape,
        # written to its entry as /set think off would write it, on the
        # wire from the first turn. "unset" is written too, so it is
        # remembered across a relaunch; a model that does not think gets
        # no row and sends nothing, a parameter saved for it inventing none.
        server = scripted.ModelServer(models=("test-model", "plain-model"), managed=True)
        server.capabilities = {
            "test-model": ["completion", "thinking"],
            "plain-model": ["completion"],
        }
        try:
            set_config_provider(tmp_path / "state", server, name="ollama")
            app = launch(tmp_path / "state", server, spec="ollama/test-model")
            try:
                assert _think(app) == "off"
                assert (
                    tomllib.loads(app.paths.models_file.read_text())["test-model"]["think"] == "off"
                )
                app.play("Hello.")
                assert app.server.requests[-1]["reasoning_effort"] == "none"
                app.play("/set think unset")
                assert _think(app) == "unset"
                app.play("Again.")
                assert "reasoning_effort" not in app.server.requests[-1]
                saved = tomllib.loads(app.paths.models_file.read_text())
                assert saved["test-model"]["think"] == "unset"
                app.play("/model ollama/plain-model")
                assert _think(app) == "unset"
                app.play("/set parameter temperature 0.5")
                saved = tomllib.loads(app.paths.models_file.read_text())
                assert "think" not in saved["plain-model"]
                app.play("Hello.")
                assert "reasoning_effort" not in app.server.requests[-1]
            finally:
                app.close()
            relaunched = launch(tmp_path / "state", server)
            try:
                assert _model(relaunched) == "plain-model"
                app_play = relaunched.play
                app_play("/model ollama/test-model")
                assert _think(relaunched) == "unset"  # unset, remembered
            finally:
                relaunched.close()
        finally:
            server.close()

    def test_a_hosted_catalog_is_read_behind_the_launch(self, tmp_path) -> None:
        # A catalog's row lives across the internet, so neither the
        # launch nor a switch waits for it: it is read on a thread of
        # its own, and the menus — the rungs, the parameters that reach
        # — follow it moments later. Nothing is played or billed for it.
        server = scripted.ModelServer(models=("graded", "plain"))
        server.extras = {
            "graded": {
                "supported_parameters": ["temperature", "max_completion_tokens", "reasoning"],
                "reasoning": {"supported_efforts": ["low", "high"]},
            },
            "plain": {"supported_parameters": ["temperature", "top_p"]},
        }
        root = tmp_path / "state"
        try:
            set_config_provider(root, server, name="openrouter")
            app = launch(root, server, spec=None)
            try:
                app.play("/model openrouter/graded")  # typed: nothing listed first
                names = lambda: _parameter_names(app)  # noqa: E731
                _settled(lambda: names() == ("temperature", "max_tokens"))
                assert names() == ("temperature", "max_tokens")
                choices = app.model_settings().choices
                assert set(choices.levels) == {"unset", "none", "low", "high"}
                assert choices.budget
                assert app.server.requests == []  # a read, never a turn
            finally:
                app.close()
            # Remembered, the model is read the same way as the session opens.
            app = launch(root, server, spec=None)
            try:
                names = lambda: _parameter_names(app)  # noqa: E731
                _settled(lambda: names() == ("temperature", "max_tokens"))
                assert names() == ("temperature", "max_tokens")
                app.play("/model openrouter/plain")
                _settled(lambda: names() == ("temperature", "top_p"))
                assert names() == ("temperature", "top_p")
                assert app.model_settings().choices.levels == ("unset",)
            finally:
                app.close()
        finally:
            server.close()

    def test_a_switch_takes_off_and_on_and_a_budget_takes_a_number(self, tmp_path) -> None:
        # omlx's status says the template has the toggle: on or off, no
        # rung, under a budget its own sampler holds — so a number of
        # tokens is taken too, 0 = off, saved as the level is, and each
        # is spelled on the wire: off as none, on as the flag alone, a
        # budget as the number with the flag on.
        server = scripted.ModelServer(models=("test-model",))
        server.status = True
        server.loaded = {"test-model"}
        server.thinking = {"test-model": True}
        try:
            set_config_provider(tmp_path / "state", server, name="omlx")
            app = launch(tmp_path / "state", server, spec="omlx/test-model")
            try:
                with contextlib.suppress(NotFound):  # the status row's ask
                    app.api.stories.context.fill.get(app.ensure_story())
                choices = app.model_settings().choices
                assert (choices.levels, choices.budget) == (("unset", "off", "on"), True)
                with pytest.raises(Refused, match="does not take low"):
                    _set_think(app, "low")
                with pytest.raises(Refused):
                    _set_think(app, "1.5")
                app.play("/set think 2000")
                assert _think(app) == "2000"
                app.play("I enter the hall.")
                body = app.server.requests[-1]
                assert body["chat_template_kwargs"] == {"enable_thinking": True}
                assert body["thinking_budget"] == 2000
                app.play("/set think off")
                app.play("I listen.")
                body = app.server.requests[-1]
                assert body["chat_template_kwargs"] == {
                    "enable_thinking": False,
                    "reasoning_effort": "none",
                }
                assert body["thinking_budget"] == 0
                app.play("/set think on")
                app.play("I look around.")
                body = app.server.requests[-1]
                assert body["chat_template_kwargs"] == {"enable_thinking": True}
                assert "thinking_budget" not in body
                app.play("/set think 0")
                assert _think(app) == "0"
                saved = tomllib.loads(app.paths.models_file.read_text())
                assert saved["test-model"]["think"] == "0"
            finally:
                app.close()
            relaunched = launch(tmp_path / "state", server)
            try:
                assert _think(relaunched) == "0"
            finally:
                relaunched.close()
        finally:
            server.close()

    def test_the_level_rides_the_wire_and_default_sends_nothing(self, app: App) -> None:
        # The scripted server is a generic provider, whose url could name
        # a local engine as well as a catalog: every knob goes out.
        app.play("/set think low")
        app.play("I enter the hall.")
        body = app.server.requests[-1]
        assert body["reasoning_effort"] == "low"
        assert body["chat_template_kwargs"] == {"enable_thinking": True, "reasoning_effort": "low"}
        assert "thinking_budget_tokens" not in body and "thinking_budget" not in body
        app.play("/set think none")
        app.play("I listen.")
        body = app.server.requests[-1]
        assert body["reasoning_effort"] == "none"
        assert body["chat_template_kwargs"] == {
            "enable_thinking": False,
            "reasoning_effort": "none",
        }
        assert (body["thinking_budget_tokens"], body["thinking_budget"]) == (0, 0)
        app.play("/set think unset")
        app.play("I look around.")
        body = app.server.requests[-1]
        assert "reasoning_effort" not in body and "chat_template_kwargs" not in body
        assert "thinking_budget_tokens" not in body and "thinking_budget" not in body

    def test_a_400_to_the_knob_retries_once_without_it(self, app: App) -> None:
        # Providers differ on the knob: a reasoning-mandatory model refuses
        # "none", some providers reject the field outright — either way a
        # 400 before any content. The same request goes again with no
        # thinking field, and the provider's own default answers.
        app.play("/set think low")
        app.server.refuse = lambda body: 400 if "reasoning_effort" in body else None
        app.server.refusal = "unknown field: reasoning_effort"
        app.play("I enter the hall.")
        assert app.messages[-1].role == "assistant"
        knobbed, knobless = app.server.requests[-2:]
        assert knobbed["reasoning_effort"] == "low"
        assert "reasoning_effort" not in knobless

    def test_thinking_streams_but_is_never_saved(self, app: App, capsys) -> None:
        app.play("/set think high")
        app.server.script = lambda body: ("Let me consider the hall.", "The door creaks open.")
        app.play("I enter the hall.")
        # Streamed as a dim block behind the bar, apart from the reply.
        shown = capsys.readouterr().out
        assert "\x1b[2m│ Let me consider the hall.\x1b[22m\n\nThe door creaks open." in shown
        # Only the reply became part of the story...
        assert [m.body for m in app.messages] == [
            "I enter the hall.",
            "The door creaks open.",
        ]
        # ...so the next turn's context carries no thinking either.
        app.play("I look around.")
        assert "consider" not in str(app.server.requests[-1]["messages"])

    def test_llamacpp_gets_both_knobs_and_none_turns_the_flag_off(self, server, tmp_path) -> None:
        # llama.cpp's server reads no reasoning_effort of its own and hands
        # chat_template_kwargs to the template: the flag gates thinking,
        # and a level rides beside it as the variable a template that
        # grades its thinking reads. Its template allows thinking by
        # default, so with nothing sent the model thinks when it likes.
        set_config_provider(tmp_path / "state", server, name="llamacpp")
        app = launch(tmp_path / "state", server, spec="llamacpp/test-model")
        try:
            app.play("/set think none")
            app.play("I enter the hall.")
            body = app.server.requests[-1]
            assert "reasoning_effort" not in body
            # The flag, the word for a template that switches off on it,
            # and the budget the server's own sampler enforces on any
            # template — the one off switch that holds everywhere.
            assert body["chat_template_kwargs"] == {
                "enable_thinking": False,
                "reasoning_effort": "none",
            }
            assert body["thinking_budget_tokens"] == 0
            app.play("/set think high")
            app.play("I look around.")
            body = app.server.requests[-1]
            assert "reasoning_effort" not in body
            assert body["chat_template_kwargs"] == {
                "enable_thinking": True,
                "reasoning_effort": "high",
            }
            assert "thinking_budget_tokens" not in body  # a level leaves the budget the engine's
        finally:
            app.close()

    def test_omlx_gets_the_template_its_flag_and_its_effort(self, server, tmp_path) -> None:
        # omlx forwards chat_template_kwargs into the template and drops
        # a request-level reasoning_effort unread: the flag gates thinking
        # — a level enables, off disables, default sends nothing — and the
        # effort rides beside it for the templates that grade theirs.
        set_config_provider(tmp_path / "state", server, name="omlx")
        app = launch(tmp_path / "state", server, spec="omlx/test-model")
        try:
            app.play("/set think high")
            app.play("I enter the hall.")
            body = app.server.requests[-1]
            template = {"enable_thinking": True, "reasoning_effort": "high"}
            assert body["chat_template_kwargs"] == template
            assert "reasoning_effort" not in body
            assert "thinking_budget" not in body
            app.play("/set think none")
            app.play("I look around.")
            body = app.server.requests[-1]
            assert body["chat_template_kwargs"] == {
                "enable_thinking": False,
                "reasoning_effort": "none",
            }
            assert body["thinking_budget"] == 0  # omlx's own sampler, whatever the template reads
            app.play("/set think unset")
            app.play("We walk on.")
            assert "chat_template_kwargs" not in app.server.requests[-1]
            assert "thinking_budget" not in app.server.requests[-1]
        finally:
            app.close()

    def test_koboldcpp_gets_both_knobs(self, server, tmp_path) -> None:
        # KoboldCpp reads the effort as a thinking budget on every launch
        # and the template's flag under --jinja: both go out.
        set_config_provider(tmp_path / "state", server, name="koboldcpp")
        app = launch(tmp_path / "state", server, spec="koboldcpp/test-model")
        try:
            app.play("/set think low")
            app.play("I enter the hall.")
            body = app.server.requests[-1]
            assert body["reasoning_effort"] == "low"
            assert body["chat_template_kwargs"] == {"enable_thinking": True}
        finally:
            app.close()

    def test_ollama_gets_the_effort_alone(self, server, tmp_path) -> None:
        # Ollama reads reasoning_effort and nothing of the template: its
        # card's "thinking" is a switch, and off goes out as none.
        managed = ModelServer(managed=True)
        managed.capabilities = {"test-model": ["completion", "thinking"]}
        try:
            set_config_provider(tmp_path / "state", managed, name="ollama")
            app = launch(tmp_path / "state", managed, spec="ollama/test-model")
            try:
                app.play("/set think off")
                app.play("I enter the hall.")
                body = managed.requests[-1]
                assert body["reasoning_effort"] == "none"
                assert "chat_template_kwargs" not in body
            finally:
                app.close()
        finally:
            managed.close()

    def test_lmstudio_takes_the_level_and_sends_the_effort_alone(self, server, tmp_path) -> None:
        # LM Studio reads `reasoning_effort` and no template knob: the
        # level goes out by name and nothing else rides with it.
        set_config_provider(tmp_path / "state", server, name="lmstudio")
        plain = launch(tmp_path / "state", server, spec="lmstudio/test-model")
        try:
            plain.play("/set think high")
            assert _think(plain) == "high"
            plain.play("I enter the hall.")
            body = plain.server.requests[-1]
            assert body["reasoning_effort"] == "high"
            assert "chat_template_kwargs" not in body
        finally:
            plain.close()


class TestParameters:
    def test_a_set_parameter_reaches_the_wire(self, app: App) -> None:
        app.play("/set parameter temperature 0.7")
        app.play("I enter the hall.")
        assert app.server.requests[-1]["temperature"] == 0.7

    def test_the_parameter_is_remembered_for_the_model(self, app: App) -> None:
        app.play("/set parameter temperature 0.7")
        relaunched = launch(app.paths.root, app.server)
        relaunched.play("I enter the hall.")
        assert relaunched.server.requests[-1]["temperature"] == 0.7
        relaunched.close()

    def test_a_bare_name_shows_the_value_and_changes_nothing(self, app: App, capsys) -> None:
        # Asking is not setting: the bare name prints where the parameter
        # stands; only the literal `reset` resets.
        app.play("/set parameter temperature 0.7")
        capsys.readouterr()
        app.play("/set parameter temperature")
        assert "temperature = 0.7" in capsys.readouterr().out
        app.play("I enter the hall.")
        assert app.server.requests[-1]["temperature"] == 0.7
        app.play("/set parameter top_p")
        assert "default" in capsys.readouterr().out  # unset: named as such

    def test_reset_returns_the_parameter_to_the_default(self, app: App) -> None:
        app.play("/set parameter temperature 0.7")
        app.play("/set parameter temperature reset")
        app.play("I enter the hall.")
        assert "temperature" not in app.server.requests[-1]

    def test_parameters_follow_their_model_across_a_switch(self, app: App) -> None:
        # Set on one model, switch away: the other model plays with ITS
        # saved parameters, not the first one's.
        app.play("/set parameter temperature 0.7")
        app.play("/model generic/other-model")
        app.play("/set parameter top_p 0.5")
        app.play("I enter the hall.")
        body = app.server.requests[-1]
        assert body["top_p"] == 0.5
        assert "temperature" not in body
        # And switching back restores the first model's own parameters.
        app.play("/model generic/test-model")
        app.play("I look around.")
        body = app.server.requests[-1]
        assert body["temperature"] == 0.7
        assert "top_p" not in body

    def test_only_the_parameters_the_provider_reads_are_offered(self, tmp_path) -> None:
        # LM Studio's wire reads top_k and repetition_penalty beyond the
        # protocol's, not min_p: the menu names what reaches the wire, in
        # the vocabulary's order. A list, not a gate — one model is served
        # by more than one provider — so min_p is still set and saved,
        # and it is the wire that leaves it out.
        server = scripted.ModelServer(managed=True)
        try:
            set_config_provider(tmp_path / "state", server, name="lmstudio")
            app = launch(tmp_path / "state", server, spec="lmstudio/test-model")
            try:
                names = _parameter_names(app)
                assert "top_k" in names and "min_p" not in names
                assert list(names) == [n for n in PARAMETERS if n in names]
                app.play("/set parameter top_k 40")
                app.play("/set parameter min_p 0.1")
                assert _params(app)["min_p"] == 0.1
                app.play("I enter the hall.")
                body = app.server.requests[-1]
                assert body["top_k"] == 40 and "min_p" not in body
            finally:
                app.close()
        finally:
            server.close()

    def test_a_value_outside_the_range_is_refused_and_the_bounds_stand(self, app: App) -> None:
        # The engines' own bounds, checked once for both frontends: out
        # of range is refused by name and nothing is saved; the bounds
        # themselves are taken.
        with pytest.raises(Refused, match="temperature must be between 0 and 2"):
            _typed_parameter(app, "temperature 3")
        with pytest.raises(Refused, match="top_k must be at least 0"):
            _set_parameter(app, "top_k", "-1")
        assert "temperature" not in _params(app) and "top_k" not in _params(app)
        _typed_parameter(app, "temperature 2")
        _typed_parameter(app, "top_p 0")
        _typed_parameter(app, "seed -7")  # unbounded: any integer
        assert (_params(app)["temperature"], _params(app)["top_p"]) == (2.0, 0.0)
        assert _params(app)["seed"] == -7

    def test_the_bounds_are_the_engines_own(self, tmp_path) -> None:
        # A local engine takes any temperature; KoboldCpp floors the
        # repetition penalty at 1; NanoGPT refuses a top_k under 1. The
        # bounds the setter holds to are the ones the page publishes.
        server = scripted.ModelServer()
        try:
            set_config_provider(tmp_path / "state", server, name="llamacpp")
            local = launch(tmp_path / "state", server, spec="llamacpp/test-model")
            try:
                _typed_parameter(local, "temperature 3.5")
                _typed_parameter(local, "presence_penalty -3")
                assert _params(local)["temperature"] == 3.5
                assert _bounds(local, "temperature") == (0, None)
                assert _bounds(local, "top_p") == (0, 1)
            finally:
                local.close()
            set_config_provider(tmp_path / "kobold", server, name="koboldcpp")
            kobold = launch(tmp_path / "kobold", server, spec="koboldcpp/test-model")
            try:
                with pytest.raises(Refused, match="repetition_penalty must be at least 1"):
                    _typed_parameter(kobold, "repetition_penalty 0.9")
            finally:
                kobold.close()
            set_config_provider(tmp_path / "nano", server, name="nanogpt", api_key="k")
            nano = launch(tmp_path / "nano", server, spec="nanogpt/test-model")
            try:
                with pytest.raises(Refused, match="top_k must be at least 1"):
                    _typed_parameter(nano, "top_k 0")
                with pytest.raises(Refused, match="temperature must be between 0 and 2"):
                    _typed_parameter(nano, "temperature 3")
            finally:
                nano.close()
        finally:
            server.close()

    def test_stop_holds_several_strings_with_their_whitespace(self, app: App) -> None:
        # Typed as JSON strings, so a stop may hold a newline or a space;
        # kept as a list, saved as one, sent as one, read back on a
        # relaunch — and a bare text is one stop as it is.
        app.play('/set parameter stop "\\nUser:" "The End"')
        assert _params(app)["stop"] == ["\nUser:", "The End"]
        app.play("I enter the hall.")
        assert app.server.requests[-1]["stop"] == ["\nUser:", "The End"]
        saved = tomllib.loads(app.paths.models_file.read_text())
        assert saved["test-model"]["stop"] == ["\nUser:", "The End"]
        relaunched = launch(app.paths.root, app.server)
        try:
            assert _params(relaunched)["stop"] == ["\nUser:", "The End"]
        finally:
            relaunched.close()
        app.play("/set parameter stop END")
        assert _params(app)["stop"] == ["END"]
        with pytest.raises(Refused, match="Could not parse"):
            _typed_parameter(app, 'stop "unclosed')

    def test_a_parameter_the_provider_drops_is_kept_and_said_so(self, tmp_path) -> None:
        # Ollama's /v1 reads no top_k: the value is set and saved for the
        # model all the same — another provider may read it — and the
        # setter and the report both say the provider in use does not.
        server = scripted.ModelServer(managed=True)
        try:
            set_config_provider(tmp_path / "state", server, name="ollama")
            app = launch(tmp_path / "state", server, spec="ollama/test-model")
            try:
                assert _parameter_read(app, "temperature")
                assert not _parameter_read(app, "top_k")
                _typed_parameter(app, "top_k 40")
                assert _params(app)["top_k"] == 40
                app.play("I enter the hall.")
                assert "top_k" not in app.server.requests[-1]
            finally:
                app.close()
        finally:
            server.close()

    def test_an_invalid_value_is_refused(self, app: App, capsys) -> None:
        app.play("/set parameter temperature warm")
        assert "warm" in capsys.readouterr().out
        app.play("I enter the hall.")
        assert "temperature" not in app.server.requests[-1]

    def test_an_unknown_parameter_is_refused(self, app: App, capsys) -> None:
        app.play("/set parameter charisma 18")
        assert "charisma" in capsys.readouterr().out
        app.play("I enter the hall.")
        assert "charisma" not in app.server.requests[-1]


class TestManagedPicker:
    """The picker over a managed backend (a scripted ollama): load state
    on screen, l/u with a confirm, Enter loading before picking."""

    def launch_managed(self, tmp_path) -> tuple[App, ModelServer]:
        server = ModelServer(models=("alpha", "beta"), managed=True)
        set_config_provider(tmp_path / "state", server, name="ollama", keep_alive="24h")
        app = launch(tmp_path / "state", server, spec="ollama/alpha")
        return app, server

    def settled_pick(self, app: App) -> str | None:
        """`pick`, but not run until its rows are in. The picker lists
        every provider in the background now, and `run_screen` queues its
        keys before the app starts — so a key would otherwise act on an
        empty list. Built HERE, inside the app session, because
        prompt_toolkit binds an Application's input when it is built; the
        queued keys wait in the pipe meanwhile."""
        providers = list(app.api.providers.list(probe=False).providers)
        picker = screen_models.ModelPicker(
            app.api, providers, [], initial_spec="ollama/alpha", fetch=["ollama"]
        )
        deadline = time.monotonic() + 5
        while picker.pending and time.monotonic() < deadline:
            time.sleep(0.02)
        return picker.run()

    def test_a_turn_on_a_cold_model_loads_it_first(self, tmp_path) -> None:
        # The turn's request would load the model anyway; loading first
        # means the prompt is cut to the runner's window, not the
        # assembler's substitute — which the preview, that loads
        # nothing, says it is until then.
        app, server = self.launch_managed(tmp_path)
        server.contexts["alpha"] = 32768
        try:
            assert _window(app) is None
            assert app.api.stories.context.get(app.ensure_story()).note
            app.play("I enter the hall.")
            assert "/api/generate" in server.posts
            assert server.posts.index("/api/generate") < server.posts.index("/v1/chat/completions")
            assert _window(app) == 32768
            assert app.api.stories.context.get(app.story_id).note == ""
        finally:
            app.close()

    def test_a_read_is_answered_while_a_turn_loads_a_cold_model(self, tmp_path) -> None:
        # The app's one thread spends a cold model's load answering the
        # reads queued meanwhile, as it does the reply after it: a screen
        # opened mid-load opens at once, not when the model is up.
        app, server = self.launch_managed(tmp_path)
        server.load_delay = 1.0
        story = app.ensure_story()
        playing = threading.Thread(target=app.play, args=("I enter the hall.",))
        try:
            playing.start()
            time.sleep(0.3)  # into the load
            app.api.stories.get(story)
            assert playing.is_alive()  # answered while the turn still loads
            playing.join()
            assert "/api/generate" in server.posts
        finally:
            playing.join()
            app.close()

    def test_l_loads_the_model_after_a_confirm(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        try:
            with contextlib.suppress(EOFError):
                run_screen("ly" + ESC, lambda: self.settled_pick(app))
            assert server.loaded == {"alpha"}
            # The load request carried the provider's keep_alive.
            load = next(r for r in server.requests if r.get("prompt") == "")
            assert load["keep_alive"] == "24h"
        finally:
            app.close()
            server.close()

    def test_x_saves_the_model_for_extraction_and_again_unsets_it(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        try:
            with contextlib.suppress(EOFError):
                run_screen(_DOWN + "x" + ESC, lambda: self.settled_pick(app))
            assert _extraction(app) == "ollama/beta"
            with contextlib.suppress(EOFError):
                run_screen(_DOWN + "x" + ESC, lambda: self.settled_pick(app))
            assert _pinned(app) == ""
            assert _model(app) == "alpha"  # the story's model never moved
        finally:
            app.close()
            server.close()

    def test_the_inventory_reports_live_windows_only(self, tmp_path) -> None:
        # The picker's context column reads these rows: a loaded model's
        # window from /api/ps, and NOTHING for an unloaded one — Ollama
        # sizes a window at load time from a server-wide default nothing
        # exposes, so the card's figure would be a ceiling, not the window.
        app, server = self.launch_managed(tmp_path)
        server.loaded = {"alpha"}
        server.contexts["alpha"] = 32768
        # The launch read the model in use once (its card among the
        # reads); the inventory is what is counted here.
        server.gets.clear()
        server.requests.clear()
        try:
            rows = app.api.providers.list().providers
            ollama = next(r for r in rows if r.id == "ollama")
            by_name = {m.name: m for m in ollama.models}
            assert by_name["alpha"].context_loaded == 32768
            assert by_name["beta"].context_loaded is None
            assert by_name["alpha"].size == 1_000_000
            # One pass over each native endpoint, however long the list,
            # and no per-model card lookups (the harness's generic
            # provider shares the port; its /v1/models is not ollama's).
            assert [p for p in server.gets if "/api/" in p] == ["/api/tags", "/api/ps"]
            assert all("messages" in r or "prompt" in r for r in server.requests)
        finally:
            app.close()
            server.close()

    def test_an_unloaded_models_window_is_unknown_until_it_loads(self, tmp_path) -> None:
        # The status row asks the instance, live: unknown before the load,
        # the served window after, unknown again once unloaded — nothing
        # is pinned on a model that loads a moment later.
        app, server = self.launch_managed(tmp_path)
        try:
            story = app.ensure_story()
            with pytest.raises(NotFound):
                app.api.stories.context.fill.get(story)
            server.loaded = {"alpha"}
            server.contexts["alpha"] = 4096
            assert app.api.stories.context.fill.get(story).context_in_force == 4096
            server.loaded = set()
            with pytest.raises(NotFound):
                app.api.stories.context.fill.get(story)
        finally:
            app.close()
            server.close()

    def test_u_unloads_after_a_confirm(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        server.loaded = {"alpha"}
        try:
            with contextlib.suppress(EOFError):
                run_screen("uy" + ESC, lambda: self.settled_pick(app))
            assert server.loaded == set()
            unload = next(r for r in server.requests if r.get("keep_alive") == 0)
            assert unload["model"] == "alpha"
        finally:
            app.close()
            server.close()

    def test_rows_landing_above_the_cursor_leave_it_on_the_model(self, tmp_path) -> None:
        # Rows land in PROVIDER order, not arrival order: llama.cpp is
        # before Ollama in the panel and here the last to answer, so its
        # rows land ABOVE the remembered model's — a model that is not
        # its provider's first row would otherwise lose the cursor to
        # whatever slid under it. The cursor follows the model.
        server = ModelServer(models=("alpha", "beta"), managed=True)
        server.window = 4096  # llama.cpp's listing reads /props; served with a window
        server.list_delay = 0.5  # /v1/models, llama.cpp's listing, answers after /api/tags
        root = tmp_path / "state"
        set_config_provider(root, server, name="ollama", keep_alive="24h")
        set_config_provider(root, server, name="llamacpp")
        app = launch(root, server, spec="ollama/beta")

        def settled() -> str | None:
            providers = list(app.api.providers.list(probe=False).providers)
            picker = screen_models.ModelPicker(
                app.api, providers, [], initial_spec="ollama/beta", fetch=["ollama", "llamacpp"]
            )
            deadline = time.monotonic() + 5
            while picker.pending and time.monotonic() < deadline:
                time.sleep(0.02)
            assert [e.full_spec for e in picker.filtered] == [
                "llamacpp/alpha",
                "llamacpp/beta",
                "ollama/alpha",
                "ollama/beta",
            ]
            assert picker.filtered[picker.cursor].full_spec == "ollama/beta"
            return picker.run()

        try:
            notice = run_screen(ENTER, settled)
            assert notice is not None and "ollama/beta" in notice
        finally:
            app.close()
            server.close()

    def test_enter_on_a_not_loaded_model_loads_it_first(self, tmp_path) -> None:
        app, server = self.launch_managed(tmp_path)
        try:
            notice = run_screen(ENTER, lambda: self.settled_pick(app))
            # The pick EXECUTES the switch and answers with its notice —
            # here a no-op notice, since the launch already stood on it.
            assert notice is not None and "ollama/alpha" in notice
            assert "alpha" in server.loaded  # switched only after the load
        finally:
            app.close()
            server.close()


def _settled(done, deadline: float = 5.0) -> None:
    """Poll until `done()` or the deadline — background picker work."""
    end = time.monotonic() + deadline
    while not done() and time.monotonic() < end:
        time.sleep(0.02)


def _think(app: App) -> str:
    """The thinking level saved for the model in use — "unset" where
    nothing is sent."""
    return app.model_settings().think


def _set_think(app: App, level: str) -> str:
    model = _in_use(app)
    return app.api.providers.models.settings.update(model.provider, model.name, think=level).notice


def _set_parameter(app: App, name: str, value: object) -> str:
    model = _in_use(app)
    return app.api.providers.models.settings.update(
        model.provider, model.name, parameters={name: value}
    ).notice


def _typed_parameter(app: App, typed: str) -> str:
    """`NAME VALUE` as a reader types it, the value read as typed."""
    name, _, value = typed.partition(" ")
    return _set_parameter(app, name, value)


def _parameter(app: App, name: str) -> ParameterSetting:
    return next(p for p in app.model_settings().parameters if p.name == name)


def _parameter_names(app: App) -> tuple[str, ...]:
    """The parameters that reach the model in use, in the vocabulary's
    order."""
    return tuple(p.name for p in app.model_settings().parameters if p.supported)


def _parameter_read(app: App, name: str) -> bool:
    return _parameter(app, name).supported


def _bounds(app: App, name: str) -> tuple[float | None, float | None]:
    parameter = _parameter(app, name)
    return parameter.low, parameter.high
