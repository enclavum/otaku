"""End-to-end CLI tests driving the typer app with CliRunner.

Providers/clients are faked so cli.py's orchestration (spec resolution, table
rendering, no-record mode, error exits) is exercised without HTTP, while crypto
and the store run for real against the isolated tmp dir.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from typer.testing import CliRunner

from otaku import cli
from otaku.config import Config, Encryption, Settings
from otaku.pickers import model as model_mod
from tests.support import FakeClient, make_provider

runner = CliRunner()
GB = 1024**3


class CliHarness:
    def __init__(self) -> None:
        self.clients: dict[str, FakeClient] = {}
        self.providers: dict[str, object] = {}
        self.repl_calls: list[tuple[str, bool]] = []
        self.repl_summaries: list[bool] = []  # was a SummaryWorker passed to repl.run?
        self.oneshot_calls: list[tuple[str, str]] = []
        self.picker_result: str | None = None
        self.defaults = Settings()
        self.model_defaults: dict[str, Settings] = {}
        self.no_record_default = False
        self.verbose_default = False
        self.create_summaries_default = True
        self.last_state = None

    def setup(self, clients: dict[str, FakeClient]) -> None:
        self.clients = clients
        self.providers = {name: c.provider for name, c in clients.items()}


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path) -> CliHarness:
    h = CliHarness()

    def fake_load(path=None) -> Config:
        return Config(
            database_url=f"sqlite:///{tmp_path / 'cli.db'}",
            providers=h.providers,  # type: ignore[arg-type]
            encryption=Encryption("disk"),
            defaults=h.defaults,
            model_defaults=h.model_defaults,
            no_record=h.no_record_default,
            verbose=h.verbose_default,
            create_summaries=h.create_summaries_default,
        )

    monkeypatch.setattr(cli.config, "load", fake_load)
    monkeypatch.setattr(cli, "client_for", lambda provider: h.clients[provider.name])

    def fake_repl_run(state, store, summary=None) -> None:
        h.last_state = state
        h.repl_calls.append((state.full_model, store.read_only))
        h.repl_summaries.append(summary is not None)

    def fake_oneshot(state, store, prompt) -> None:
        h.last_state = state
        h.oneshot_calls.append((state.full_model, prompt))

    monkeypatch.setattr(cli.repl, "run", fake_repl_run)
    monkeypatch.setattr(cli, "run_oneshot", fake_oneshot)
    monkeypatch.setattr(
        model_mod, "pick_model", lambda providers, initial_spec=None: h.picker_result
    )
    return h


def _ollama(**kw) -> FakeClient:
    return FakeClient(make_provider("ollama"), kind="ollama", **kw)


class TestVersionHelp:
    def test_version(self) -> None:
        from otaku import __version__

        result = runner.invoke(cli.app, ["--version"])
        assert result.exit_code == 0
        assert f"otaku {__version__}" in result.output

    def test_help_lists_commands(self) -> None:
        result = runner.invoke(cli.app, ["--help"])
        assert result.exit_code == 0
        assert "[MODEL]" in result.output  # bare-model usage line
        # the command listing has only the real subcommands — no run, no hidden chat
        listing = result.output.split("Available Commands:")[1].split("Flags:")[0]
        assert "list" in listing
        assert "stop" in listing
        assert "run" not in listing
        assert "chat" not in listing


class TestSubcommandHelp:
    """The custom CompactHelpCommand must render Arguments/Flags (regressed to
    empty under some typer+click versions when filtering params by isinstance)."""

    def test_list_help_shows_running_flag(self) -> None:
        result = runner.invoke(cli.app, ["list", "--help"])
        assert result.exit_code == 0
        assert "Flags:" in result.output
        assert "--running" in result.output

    def test_stop_help_shows_all_flag_and_argument(self) -> None:
        result = runner.invoke(cli.app, ["stop", "--help"])
        assert result.exit_code == 0
        assert "Arguments:" in result.output
        assert "MODEL_SPEC" in result.output
        assert "--all" in result.output

    def test_chat_help_shows_model_argument(self) -> None:
        result = runner.invoke(cli.app, ["chat", "--help"])
        assert result.exit_code == 0
        assert "MODEL" in result.output
        assert "--no-record" in result.output


class TestList:
    def test_lists_models_with_size_context_and_loaded(self, harness: CliHarness) -> None:
        harness.setup(
            {
                "ollama": _ollama(
                    models=["llama3", "qwen"],
                    loaded={"llama3"},
                    sizes={"llama3": GB},
                    contexts={"llama3": 8192},
                )
            }
        )
        result = runner.invoke(cli.app, ["list"])
        assert result.exit_code == 0
        assert "CONTEXT" in result.output
        assert "LOADED" in result.output
        assert "llama3" in result.output
        assert "qwen" in result.output
        assert "✓" in result.output
        assert "1.0 GB" in result.output
        assert "8K" in result.output  # loaded model's context window

    def test_context_shown_only_for_loaded(self, harness: CliHarness) -> None:
        # qwen is not loaded → no context probe, so its window never appears
        harness.setup({"ollama": _ollama(models=["qwen"], loaded=set(), contexts={"qwen": 8192})})
        result = runner.invoke(cli.app, ["list"])
        assert "qwen" in result.output
        assert "8K" not in result.output

    def test_no_models_reachable_diagnoses_down_provider(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(list_error=RuntimeError("down"))})
        result = runner.invoke(cli.app, ["list"])
        out = result.output
        assert "No models reachable" in out
        assert "ollama" in out and "http://localhost:9999/v1" in out  # what was checked
        assert "not responding" in out  # list_models threw → marked unreachable
        assert "config.toml" in out  # points at the config file to fix

    def test_no_models_reachable_when_provider_empty(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=[])})  # up, but exposes nothing
        result = runner.invoke(cli.app, ["list"])
        assert "No models reachable" in result.output
        assert "responding, but exposes no models" in result.output


class TestListRunning:
    def test_shows_only_loaded_with_context(self, harness: CliHarness) -> None:
        harness.setup(
            {
                "ollama": _ollama(
                    models=["llama3", "qwen"],
                    loaded={"llama3"},
                    sizes={"llama3": GB},
                    contexts={"llama3": 8192},
                )
            }
        )
        result = runner.invoke(cli.app, ["list", "--running"])
        assert result.exit_code == 0
        assert "llama3" in result.output
        assert "qwen" not in result.output  # not loaded → filtered out
        assert "8K" in result.output
        assert "LOADED" not in result.output  # column dropped in running mode

    def test_short_flag_filters_to_loaded(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["alpha", "beta"], loaded={"alpha"})})
        result = runner.invoke(cli.app, ["list", "-r"])
        assert result.exit_code == 0
        assert "alpha" in result.output
        assert "beta" not in result.output

    def test_nothing_running(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["a"], loaded=set())})
        result = runner.invoke(cli.app, ["list", "--running"])
        assert "no running models" in result.output


class TestStop:
    def test_stop_explicit_spec(self, harness: CliHarness) -> None:
        client = _ollama(loaded={"llama3"})
        harness.setup({"ollama": client})
        result = runner.invoke(cli.app, ["stop", "ollama/llama3"])
        assert result.exit_code == 0
        assert "unloaded ollama/llama3" in result.output
        assert client.unloaded == ["llama3"]

    def test_stop_bare_name_resolved(self, harness: CliHarness) -> None:
        client = _ollama(loaded={"llama3"})
        harness.setup({"ollama": client})
        result = runner.invoke(cli.app, ["stop", "llama3"])
        assert result.exit_code == 0
        assert client.unloaded == ["llama3"]

    def test_stop_all(self, harness: CliHarness) -> None:
        a = _ollama(loaded={"llama3"})
        b = FakeClient(make_provider("lmstudio"), loaded={"phi"})
        harness.setup({"ollama": a, "lmstudio": b})
        result = runner.invoke(cli.app, ["stop", "--all"])
        assert result.exit_code == 0
        assert "unloaded ollama/llama3" in result.output
        assert "unloaded lmstudio/phi" in result.output

    def test_stop_all_nothing_loaded(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(loaded=set())})
        result = runner.invoke(cli.app, ["stop", "--all"])
        assert "nothing was loaded" in result.output

    def test_stop_all_with_model_errors(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(loaded={"llama3"})})
        result = runner.invoke(cli.app, ["stop", "--all", "llama3"])
        assert result.exit_code == 2
        assert "doesn't take a model argument" in result.output

    def test_stop_no_args_usage(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama()})
        result = runner.invoke(cli.app, ["stop"])
        assert result.exit_code == 2
        assert "usage:" in result.output

    def test_stop_unknown_model(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(loaded=set())})
        result = runner.invoke(cli.app, ["stop", "ghost"])
        assert result.exit_code == 2
        assert "not loaded" in result.output


class TestChat:
    """`otaku <model>` — a first positional that isn't a known subcommand is
    routed to the hidden `chat` command (no `run` subcommand)."""

    def test_bare_model_enters_repl(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["ollama/llama3"])
        assert result.exit_code == 0
        assert harness.repl_calls == [("ollama/llama3", False)]

    def test_no_record_flag_after_model(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["ollama/llama3", "-nr"])
        assert result.exit_code == 0
        assert harness.repl_calls == [("ollama/llama3", True)]  # read_only store

    def test_global_no_record_flag_before_model(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["-nr", "ollama/llama3"])
        assert result.exit_code == 0
        assert harness.repl_calls == [("ollama/llama3", True)]

    def test_bare_name_resolved(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["llama3"])
        assert result.exit_code == 0
        assert harness.repl_calls == [("ollama/llama3", False)]

    def test_unresolvable_spec_exits(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["does-not-exist"])
        assert result.exit_code == 2
        assert "not available" in result.output

    def test_subcommands_are_not_treated_as_models(self, harness: CliHarness) -> None:
        # `list` must dispatch to the list command, not be resolved as a model
        harness.setup({"ollama": _ollama(models=["llama3"], loaded=set())})
        result = runner.invoke(cli.app, ["list"])
        assert result.exit_code == 0
        assert harness.repl_calls == []  # never entered chat


class TestOneShot:
    """`otaku <model> <prompt>` and piped stdin run a one-shot, not the REPL."""

    def test_prompt_arg_runs_oneshot(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["ollama/llama3", "explain this"])
        assert result.exit_code == 0
        assert harness.oneshot_calls == [("ollama/llama3", "explain this")]
        assert harness.repl_calls == []  # no interactive session

    def test_piped_stdin_runs_oneshot(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["ollama/llama3"], input="log line one\n")
        assert result.exit_code == 0
        assert harness.oneshot_calls == [("ollama/llama3", "log line one")]

    def test_prompt_and_stdin_combined_arg_first(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["ollama/llama3", "explain"], input="line one\nline two\n")
        assert result.exit_code == 0
        # instruction first, piped content second, separated by a blank line
        assert harness.oneshot_calls == [("ollama/llama3", "explain\n\nline one\nline two")]

    def test_no_prompt_no_stdin_enters_repl(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["ollama/llama3"])
        assert result.exit_code == 0
        assert harness.oneshot_calls == []
        assert harness.repl_calls == [("ollama/llama3", False)]

    def test_oneshot_respects_no_record(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        result = runner.invoke(cli.app, ["ollama/llama3", "hi", "-nr"])
        assert result.exit_code == 0
        assert harness.oneshot_calls == [("ollama/llama3", "hi")]


class TestConfigDefaults:
    def test_no_record_by_default(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        harness.no_record_default = True
        result = runner.invoke(cli.app, ["ollama/llama3"])  # no -nr flag
        assert result.exit_code == 0
        assert harness.repl_calls == [("ollama/llama3", True)]  # read-only from config

    def test_global_defaults_applied_at_launch(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        harness.defaults = Settings(
            system="Be brief.", think="high", parameters={"temperature": 0.2}
        )
        runner.invoke(cli.app, ["ollama/llama3"])
        st = harness.last_state
        assert st is not None
        assert st.messages[0].content == "Be brief."
        assert st.think == "high"
        assert st.params == {"temperature": 0.2}

    def test_verbose_default_applied_at_launch(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        harness.verbose_default = True
        runner.invoke(cli.app, ["ollama/llama3"])
        assert harness.last_state is not None
        assert harness.last_state.verbose is True

    def test_per_model_override_wins(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        harness.defaults = Settings(think="high")
        harness.model_defaults = {"llama3": Settings(think="max")}  # keyed bare
        runner.invoke(cli.app, ["ollama/llama3"])
        assert harness.last_state is not None
        assert harness.last_state.think == "max"

    def test_summary_worker_on_by_default(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        runner.invoke(cli.app, ["ollama/llama3"])
        assert harness.repl_summaries == [True]  # a SummaryWorker was wired in

    def test_no_summary_worker_when_disabled(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        harness.create_summaries_default = False
        runner.invoke(cli.app, ["ollama/llama3"])
        assert harness.repl_summaries == [False]

    def test_no_summary_worker_in_no_record(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        runner.invoke(cli.app, ["ollama/llama3", "-nr"])  # read-only → nothing to summarize
        assert harness.repl_summaries == [False]


class TestComposeOneShot:
    def test_prompt_only(self) -> None:
        assert cli._compose_oneshot("explain", "") == "explain"

    def test_stdin_only_is_trimmed(self) -> None:
        assert cli._compose_oneshot("", "some log\n") == "some log"

    def test_both_arg_first_blank_line(self) -> None:
        assert cli._compose_oneshot("explain", "err\n") == "explain\n\nerr"

    def test_neither_is_none(self) -> None:
        assert cli._compose_oneshot("", "") is None

    def test_whitespace_only_is_none(self) -> None:
        assert cli._compose_oneshot("  ", "\n\t ") is None


class TestBareInvocation:
    def test_picker_choice_launches_chat(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        harness.picker_result = "ollama/llama3"
        result = runner.invoke(cli.app, [])
        assert result.exit_code == 0
        assert harness.repl_calls == [("ollama/llama3", False)]

    def test_picker_cancel_does_not_launch(self, harness: CliHarness) -> None:
        harness.setup({"ollama": _ollama(models=["llama3"])})
        harness.picker_result = None
        result = runner.invoke(cli.app, [])
        assert result.exit_code == 0
        assert harness.repl_calls == []

    def test_crypto_unlocked_once_before_picker(
        self, harness: CliHarness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Interactive KEK ceremonies (passphrase, slow `command` providers)
        # must run up front, not after the model is chosen — and only once.
        harness.setup({"ollama": _ollama(models=["llama3"])})
        harness.picker_result = "ollama/llama3"
        order: list[str] = []
        real_unlock = cli.crypto.unlock

        def spy_unlock(enc):
            order.append("unlock")
            return real_unlock(enc)

        def spy_pick(providers, initial_spec=None):
            order.append("picker")
            return harness.picker_result

        monkeypatch.setattr(cli.crypto, "unlock", spy_unlock)
        monkeypatch.setattr(model_mod, "pick_model", spy_pick)
        result = runner.invoke(cli.app, [])
        assert result.exit_code == 0
        assert order == ["unlock", "picker"]


class TestResolveSpecAmbiguity:
    def test_ambiguous_bare_name_requires_disambiguation(self, harness: CliHarness) -> None:
        harness.setup(
            {
                "ollama": _ollama(models=["shared"]),
                "lmstudio": FakeClient(make_provider("lmstudio"), models=["shared"]),
            }
        )
        result = runner.invoke(cli.app, ["shared"])
        assert result.exit_code == 2
        assert "multiple providers" in result.output


def test_harness_type_is_callable() -> None:
    # guard: fixtures wire a Callable[[dict], None] setup
    assert isinstance(CliHarness().setup, Callable)  # type: ignore[arg-type]
