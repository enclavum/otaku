"""A story's next request: /context previews it, with the injections it
carries, and how full it is — the figure the status row shows.
"""

import pytest

from otaku.backend.api import InjectionPosition, Layout, NotFound, Refused
from otaku.backend.app.paths import Paths
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config, set_config_provider


class TestContext:
    def test_the_preview_shows_the_parts_to_be_sent(self, app: App, capsys) -> None:
        app.play("/system You are the narrator.")
        app.play("I enter the hall.")
        capsys.readouterr()
        app.play("/context")
        out = capsys.readouterr().out
        assert "You are the narrator." in out
        assert "I enter the hall." in out
        assert scripted.CHAT_REPLY in out
        assert "tok" in out  # the per-part estimates

    def test_the_preview_filters_stored_control_bytes(self, app: App, capsys) -> None:
        # The story keeps every byte; the preview is a display path, so a
        # hostile reply stored earlier can never replay its escapes here.
        app.server.script = lambda body: "safe\x1b[2Atext\x07"
        app.play("I enter the hall.")
        capsys.readouterr()
        app.play("/context")
        out = capsys.readouterr().out
        assert "safe" in out
        assert "\x1b[2A" not in out
        assert "\x07" not in out

    def test_the_preview_matches_the_next_request(self, app: App, capsys) -> None:
        app.play("/system You are the narrator.")
        app.play("I enter the hall.")
        capsys.readouterr()
        app.play("/context")
        preview = capsys.readouterr().out
        app.play("We continue.")
        sent = app.server.requests[-1]["messages"]
        # Everything but the line typed after the preview was in the preview.
        for message in sent[:-1]:
            assert str(message["content"]) in preview

    def test_the_preview_needs_no_model(self, server, tmp_path, capsys) -> None:
        # Without a model the preview still stands, over the default window.
        set_config(tmp_path / "state", seed_sample=True)
        app = launch(tmp_path / "state", server, spec="")
        try:
            capsys.readouterr()
            app.play("/context")
            out = capsys.readouterr().out
            assert "of the" in out and "limit" in out  # measured against the default window
            assert "You're late, mapmaker." in out
        finally:
            app.close()

    def test_the_preview_shows_the_recap_where_the_middle_was(
        self, server, tmp_path, capsys
    ) -> None:
        set_config(tmp_path / "state", head_messages=1, min_tail_messages=1)
        app = launch(tmp_path / "state", server)
        try:
            for i in range(6):
                app.play(f"Turn number {i}.")
            app.play("/extract")
            # One more exchange: a scene ending inside the tail window stays
            # verbatim, so the story must move past it for the recap to
            # stand in.
            app.play("We continue.")
            capsys.readouterr()
            app.play("/context")
            out = capsys.readouterr().out
            assert "[The story so far — the scenes between these moments:]" in out
            assert "A guest came in and met the Keeper." in out
        finally:
            app.close()

    def test_a_pinned_message_is_quoted_after_its_scenes_summary(self, server, tmp_path) -> None:
        # Under prompts.toml's header — the file's, so the load path is
        # what is asserted — which the preview reads as one with the pin.
        root = tmp_path / "state"
        set_config(root, head_messages=1, min_tail_messages=1)
        Paths.resolve(root).prompts_file.write_text(
            'pinned_header = "<<kept>>"\n', encoding="utf-8"
        )
        app = launch(root, server)
        try:
            for i in range(6):
                app.play(f"Turn number {i}.")
            line = next(m for m in app.messages if m.body == "Turn number 2.")
            app.api.stories.messages.update(app.story_id, line.id, pinned=True)
            app.play("/extract")
            app.play("We continue.")  # past the scene, for the recap to stand in
            parts = app.api.stories.context.get(app.story_id).parts
            laid = [
                part.provenance.value if hasattr(part, "provenance") else "separator"
                for part in parts
            ]
            at = laid.index("pinned_header")
            assert laid[at - 2 : at + 3] == [
                "scene",
                "separator",
                "pinned_header",
                "pinned_message",
                "separator",
            ]
            assert (parts[at].body, parts[at + 1].body) == ("<<kept>>", "Turn number 2.")
            app.play("And on.")
            sent = app.server.requests[-1]["messages"][0]["content"]
            assert "met the Keeper.\n\n<<kept>>\n\nTurn number 2." in sent
        finally:
            app.close()


class TestContextInjections:
    """What the request carries besides the story is reported as facts —
    each switched-on setting, where it rides, what it costs — and said
    as a bullet each in the terminal's summary."""

    def test_the_report_names_each_injection_its_place_and_its_cost(self, app: App) -> None:
        app.play("I enter the hall.")
        assert app.api.stories.context.get(app.story_id).injections == ()
        app.api.stories.settings.update(
            app.ensure_story(), "allow_questions", enabled=True, position=InjectionPosition()
        )
        app.api.stories.settings.update(
            app.ensure_story(), "use_story_reminder", enabled=True, reminder_text="Rain all night."
        )
        report = app.api.stories.context.get(app.story_id)
        facts = [(i.label, i.position, i.position_text) for i in report.injections]
        # named as the setting names what it injects, not as the switch —
        # and in the request's order: the system message first, then the
        # deepest first, not in the order sent (the reminder rides first)
        assert facts == [
            ("questions", InjectionPosition(), "system"),
            ("story reminder", InjectionPosition(2), "3rd last"),
        ]
        assert all(i.tokens > 0 for i in report.injections)
        # a system injection's tokens are the system message's
        assert report.figures.system_tokens >= report.injections[0].tokens

    def test_the_emulation_is_listed_on_the_canvas_over_the_chat_wire(self, app: App) -> None:
        # switched on, and gated: nothing in the manuscript, the system
        # message's text on the canvas, named as the setting names it
        app.play("I enter the hall.")
        app.api.stories.settings.update(app.ensure_story(), "emulate_text_completion", enabled=True)
        assert app.api.stories.context.get(app.story_id).injections == ()
        app.api.stories.settings.update(app.ensure_story(), "layout", layout=Layout.CANVAS)
        (found,) = app.api.stories.context.get(app.story_id).injections
        assert (found.label, found.position, found.position_text) == (
            "text completion emulation",
            InjectionPosition(),
            "system",
        )
        assert found.tokens > 0

    def test_the_terminal_lists_them_under_the_summary(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
        capsys.readouterr()
        app.play("/context")
        out = capsys.readouterr().out
        assert "  injected:\n  - questions (6th last, ~" in out


class TestContextUse:
    """How full the next request is: the preview's own figure, against
    the context the story is cut to, read after every turn — and nothing
    while the model's window is not known."""

    def test_the_figure_is_the_preview_s_own(self, tmp_path) -> None:
        app, server = _loaded_ollama(tmp_path)
        try:
            for i in range(3):
                app.play(f"Turn number {i}. " + "x" * 400)
            use = app.api.stories.context.fill.get(app.story_id)
            assert use.percent == app.api.stories.context.get(app.story_id).figures.used > 0
            assert use.context_in_force == 32768
        finally:
            app.close()
            server.close()

    def test_a_story_that_is_not_there_is_refused(self, tmp_path) -> None:
        # the window known, so the refusal is the story's own
        app, server = _loaded_ollama(tmp_path)
        try:
            app.play("I knock at the gate.")
            assert app.api.stories.context.fill.get(app.story_id).context_in_force == 32768
            with pytest.raises(NotFound):
                app.api.stories.context.fill.get(app.story_id + 999)
        finally:
            app.close()
            server.close()

    def test_the_cap_is_the_context_it_is_of(self, tmp_path) -> None:
        app, server = _loaded_ollama(tmp_path, cap=16384)
        try:
            app.play("I knock at the gate.")
            use = app.api.stories.context.fill.get(app.story_id)
            assert use.context_in_force == 16384
        finally:
            app.close()
            server.close()

    def test_a_story_that_cannot_fit_reads_over_a_hundred(self, tmp_path) -> None:
        # The preview refuses; the figure says by how much it does not fit.
        app, server = _loaded_ollama(tmp_path, cap=2048)
        try:
            app.play("I knock at the gate.")
            app.api.stories.update(app.story_id, premise="The valley. " * 1200)
            use = app.api.stories.context.fill.get(app.story_id)
            assert use.percent > 100 and use.context_in_force == 2048
            with pytest.raises(Refused):
                app.api.stories.context.get(app.story_id)
        finally:
            app.close()
            server.close()

    def test_nothing_while_the_model_is_not_loaded(self, tmp_path) -> None:
        # Ollama states the window a model runs under only while it runs;
        # a figure against the assembler's stand-in would mislead.
        app, server = _loaded_ollama(tmp_path)
        server.loaded.clear()
        try:
            with pytest.raises(NotFound):
                app.api.stories.context.fill.get(app.story_id)
        finally:
            app.close()
            server.close()


def _loaded_ollama(tmp_path, *, cap: int = 0) -> tuple[App, scripted.ModelServer]:
    """A session on a scripted Ollama whose model is loaded with a 32K
    window — the engine states its window only while it runs — and the
    configured cap, 0 for none."""
    server = scripted.ModelServer(managed=True)
    server.contexts["test-model"] = 32768
    server.loaded.add("test-model")
    root = tmp_path / "state"
    set_config(root, context_limit=cap)
    set_config_provider(root, server, name="ollama")
    return launch(root, server, spec="ollama/test-model"), server
