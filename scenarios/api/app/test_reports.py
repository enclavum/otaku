"""The installation's reports: /usage counts the tokens spent, /balance
what each account has left, /info what otaku knows of this run.
"""

import re

from otaku.backend.api.app.reports import InfoReport
from otaku.formatting import format_context
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config, set_config_provider


class TestUsage:
    def test_usage_groups_by_purpose_and_model(self, app: App) -> None:
        # Asserted on the report's FACTS, not on what it prints: the
        # promise is that spend is kept apart by what it was spent on
        # and on which model, and the captions those purposes are drawn
        # with are copy (`backend.api.app.reports.USAGE_PURPOSES`).
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        report = app.api.usage.get(story_id=app.story_id)
        assert {row.purpose for row in report.rows} == {"chat", "lore"}
        assert {row.model for row in report.rows} == {"test-model"}
        assert report.requests == sum(row.requests for row in report.rows)

    def test_cached_tokens_are_recorded_and_reported(self, app: App, capsys) -> None:
        # The provider says how much of the prompt its cache served; the
        # store keeps it per request and /usage sums it into its column.
        app.server.cached_tokens = 3
        app.play("I enter the hall.")
        app.play("I listen.")
        (total,) = app.store.usage.get_totals(app.story_id)
        assert total.cached_tokens == 6
        capsys.readouterr()
        app.play("/usage")
        out = capsys.readouterr().out
        assert "CACHED" in out
        assert "6" in out

    def test_usage_covers_the_current_story_and_all_widens(self, app: App, capsys) -> None:
        app.play("The first story begins.")
        app.play("/new")
        app.play("The second story begins.")
        capsys.readouterr()
        app.play("/usage")
        story_only = capsys.readouterr().out
        app.play("/usage all")
        everything = capsys.readouterr().out
        assert sum(numbers(everything)) > sum(numbers(story_only))


class TestBalance:
    def test_openrouter_credits_print_in_dollars(self, server, tmp_path, capsys) -> None:
        server.credits = (20.0, 7.66)
        set_config_provider(tmp_path / "state", server, name="openrouter")
        app = launch(tmp_path / "state", server, spec="openrouter/test-model")
        try:
            app.play("/balance")
            out = capsys.readouterr().out
            # The report names a provider by its caption, not its section key.
            assert "OpenRouter" in out
            assert "$12.34" in out  # purchased minus spent
        finally:
            app.close()

    def test_nanogpt_reports_dollars_rounded_and_no_crypto(self, server, tmp_path, capsys) -> None:
        server.balances = {"usd_balance": "5.1043327", "nano_balance": "0.42"}
        set_config_provider(tmp_path / "state", server, name="nanogpt")
        app = launch(tmp_path / "state", server, spec="nanogpt/test-model")
        try:
            app.play("/balance")
            out = capsys.readouterr().out
            assert "$5.10" in out
            assert "0.42" not in out  # the crypto balance stays out of it
        finally:
            app.close()


class TestInfo:
    def test_the_models_capabilities_are_rows_where_the_engine_can_say(self, tmp_path) -> None:
        # llama.cpp says what it loaded in /props: a projector means
        # images, a template that reads reasoning_effort means every
        # level; raw completion is its wire's own.
        server = scripted.ModelServer()
        server.window = 8192
        server.props = {
            "modalities": {"vision": True, "audio": False},
            "chat_template_caps": {"supports_reasoning_effort": True},
        }
        try:
            set_config_provider(tmp_path / "state", server, name="llamacpp")
            app = launch(tmp_path / "state", server, spec="llamacpp/test-model")
            try:
                rows = _rows(app.api.info.get(story_id=app.story_id))
                assert rows["Capabilities"] == "vision, text completion, json output"
                # How its template sets the thinking, a build without
                # /apply-template cannot tell.
                assert rows["Reasoning"] == "unknown"
            finally:
                app.close()
        finally:
            server.close()

    def test_the_context_row_says_the_served_size_of_the_models_own_where_they_differ(
        self, tmp_path
    ) -> None:
        # llama.cpp's listing carries both figures: the trained length
        # and the slot's window. The window is a row only when it is not
        # the trained length, and comes after it.
        rows = _llamacpp_rows(tmp_path / "half", trained=8192, window=4096)
        labels = list(rows)
        assert labels.index("Max context") + 1 == labels.index("Reasoning")
        served, own = format_context(4096), format_context(8192)
        assert rows["Max context"] == f"{served} (of {own} native)"
        rows = _llamacpp_rows(tmp_path / "whole", trained=8192, window=8192)
        assert rows["Max context"] == format_context(8192)

    def test_an_engine_that_names_no_efforts_says_so(self, tmp_path) -> None:
        # omlx's status states the thinking toggle; a template without
        # one takes no level at all, which is a known nothing, not an
        # unknown.
        server = scripted.ModelServer()
        server.status = True
        try:
            set_config_provider(tmp_path / "state", server, name="omlx")
            app = launch(tmp_path / "state", server, spec="omlx/test-model")
            try:
                row = _rows(app.api.info.get(story_id=app.story_id))["Reasoning"]
                assert row == "not supported"
            finally:
                app.close()
        finally:
            server.close()

    def test_the_capabilities_row_spells_the_card_in_the_dataclasss_order(self, tmp_path) -> None:
        # Ollama's card names thinking and vision; the row lists what the
        # model has in the dataclass's order — reasoning left to its own
        # row — with constrained decoding, which every Ollama model has,
        # last.
        server = scripted.ModelServer(managed=True)
        server.capabilities["test-model"] = ["completion", "thinking", "vision"]
        try:
            set_config_provider(tmp_path / "state", server, name="ollama")
            app = launch(tmp_path / "state", server, spec="ollama/test-model")
            try:
                rows = _rows(app.api.info.get(story_id=app.story_id))
                assert rows["Capabilities"] == "vision, json output"
                assert rows["Reasoning"] not in ("unknown", "not supported")
            finally:
                app.close()
        finally:
            server.close()

    def test_an_engine_that_cannot_say_how_the_thinking_is_set_reads_unknown(
        self, tmp_path
    ) -> None:
        # LM Studio takes the effort word, but a registry row without
        # the reasoning object does not say what the model takes.
        server = scripted.ModelServer(managed=True)
        try:
            set_config_provider(tmp_path / "state", server, name="lmstudio")
            app = launch(tmp_path / "state", server, spec="lmstudio/test-model")
            try:
                assert _rows(app.api.info.get(story_id=app.story_id))["Reasoning"] == "unknown"
            finally:
                app.close()
        finally:
            server.close()

    def test_what_the_engine_cannot_say_is_unknown(self, app: App) -> None:
        # The generic provider reads nothing: every capability is
        # unknown, which the app offers nothing on.
        rows = _rows(app.api.info.get(story_id=app.story_id))
        assert (rows["Reasoning"], rows["Capabilities"]) == ("unknown", "unknown")

    def test_without_a_model_the_session_half_still_reports(self, server, tmp_path, capsys) -> None:
        # A model is one of the things /info reports, not its
        # precondition: the story and the parameters are the session's
        # own. The premise is not among them — it is a document, and
        # `/system` is what reports it.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec=None)
        try:
            app.play("/system You are the narrator.")
            capsys.readouterr()
            app.play("/info")
            out = capsys.readouterr().out
            assert "No model selected" in out
            assert "State dir:" in out
            assert "Messages:" in out  # the loaded story is still counted
            assert "You are the narrator." not in out
        finally:
            app.close()


def numbers(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)]


def _rows(report: InfoReport) -> dict[str, str]:
    """Every labelled fact of the report, whichever block it is in."""
    return {label: value for section in report.sections for label, value in section.rows}


def _llamacpp_rows(root, *, trained: int, window: int) -> dict[str, str]:
    """`/info`'s rows on a llama.cpp whose model was trained to `trained`
    tokens and loaded with a slot of `window` — one server and one
    state dir each, since a client keeps the window it first read."""
    server = scripted.ModelServer()
    server.contexts["test-model"] = trained
    server.window = window
    try:
        set_config_provider(root, server, name="llamacpp")
        app = launch(root, server, spec="llamacpp/test-model")
        try:
            return _rows(app.api.info.get(story_id=app.story_id))
        finally:
            app.close()
    finally:
        server.close()
