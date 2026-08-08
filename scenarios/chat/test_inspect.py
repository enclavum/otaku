"""Inspecting the session: /context previews the next request, /usage
counts the tokens spent, /info dumps what otaku knows."""

import re

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
            assert "8,192 window" in out
            assert "You're late, mapmaker." in out
        finally:
            app.close()

    def test_the_preview_shows_the_recap_where_the_middle_was(
        self, server, tmp_path, capsys
    ) -> None:
        set_config(tmp_path / "state", head_messages=1, tail_messages=1)
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


class TestUsage:
    def test_usage_groups_by_purpose_and_model(self, app: App, capsys) -> None:
        for i in range(3):
            app.play(f"Turn number {i}.")
        app.play("/extract")
        capsys.readouterr()
        app.play("/usage")
        out = capsys.readouterr().out
        assert "chat" in out
        assert "lore" in out
        assert "test-model" in out

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
            assert "openrouter" in out
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
    def test_without_a_model_the_session_half_still_reports(self, server, tmp_path, capsys) -> None:
        # A model is one of the things /info reports, not its precondition:
        # the story, its premise and the parameters are the session's own.
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
            assert "You are the narrator." in out
        finally:
            app.close()


def numbers(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)]
