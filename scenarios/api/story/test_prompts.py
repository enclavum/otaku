"""What a story's settings send: each tool's prompt and every other a
setting names, edited in prompts.toml from the app — the one key
rewritten, the pre-edit file kept, the next request carrying what was
saved.
"""

import pytest

from otaku.backend.api import Refused
from otaku.settings.prompts import Prompts
from otaku.terminal.api.story.prompts import write_prompt
from scenarios.api.story.test_stories import HOW_TO_ASK, messages, prompt_of, with_prompt
from scenarios.support.harness import App


class TestSettingPrompts:
    """An injection setting's prompt is prompts.toml's, edited in place
    from the app by the setting's name: the one key rewritten, the
    pre-edit file kept as a dated backup named after the file, and the
    next request carrying what was saved. A setting that sends no prompt
    of the file's — a reminder, the layout — has none to edit."""

    def test_a_saved_prompt_lands_in_the_file_and_on_the_wire(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        try:
            app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
            said = set_prompt_of(app, "allow_questions", "ASK ANEW")
            assert said
            text = app.paths.prompts_file.read_text()
            assert text.count("tool_questions_prompt = ") == 1 and "ASK ANEW" in text
            assert HOW_TO_ASK not in text
            kept = list(app.paths.config_backups_dir.iterdir())  # the pre-edit file waits
            assert [path.name[: len("prompts-")] for path in kept] == ["prompts-"]
            assert HOW_TO_ASK in kept[0].read_text()
            assert prompt_of(app, "allow_questions") == "ASK ANEW"
            app.play("I push the door.")
            assert messages(app, "I push the door.") == [
                ("user", "((OOC: ASK ANEW))\n\nI push the door.")
            ]
        finally:
            app.close()

    def test_an_emptied_prompt_restores_the_built_in(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        try:
            set_prompt_of(app, "allow_questions", "  ")
            assert "tool_questions_prompt" not in app.paths.prompts_file.read_text()
            shipped = Prompts().tool_questions_prompt
            assert prompt_of(app, "allow_questions") == shipped
        finally:
            app.close()

    def test_a_name_no_tool_answers_to_and_a_key_no_setting_sends_are_refused(
        self, app: App
    ) -> None:
        # a tool's prompt by the tool's name — a setting's is no tool's
        for tool in ("plan", "allow_questions"):
            with pytest.raises(Refused):
                app.api.tools.prompt.get(tool)
            with pytest.raises(Refused):
                app.api.tools.prompt.update(tool, text="x")
        # any other by its key: a reminder's text is the reader's own, the
        # layout sends none, and a tool's prompt is its tool's to answer
        for key in ("plan", "use_story_reminder", "layout", "tool_questions_prompt"):
            with pytest.raises(Refused):
                app.api.prompts.get(key)
            with pytest.raises(Refused):
                app.api.prompts.update(key, text="x")


def set_prompt_of(app: App, name: str, text: str) -> str:
    """A setting's prompt written where `prompt_of` reads it — and the
    sentence."""
    return write_prompt(app.api, app.api.stories.settings.get(app.ensure_story(), name), text)
