"""Inspecting the session: /context previews the next request, /usage
counts the tokens spent, /info dumps what otaku knows."""

import re

from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config


def numbers(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)]


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


class TestInfo:
    def test_info_names_the_active_model(self, app: App, capsys) -> None:
        app.play("I enter the hall.")
        capsys.readouterr()
        app.play("/info")
        assert "test-model" in capsys.readouterr().out
