"""A story's settings on the wire and in the store: a tool switched on
sends its instruction from prompts.toml where the story says — enclosed
above the newest line, or bare after the premise; a reminder sends the
user's own words from the end — the shared one's, written once, and the
story's own, each a switch; none of it is ever stored as a message; the
switches are the story's — kept across a launch, carried by a fork, off
in a new story — and so is its layout, /set mode in the terminal's two
words.
"""

import contextlib
import tomllib

import pytest

from otaku.backend.api import (
    CompletionType,
    InjectionPosition,
    Layout,
    Refused,
)
from otaku.backend.api.app.integrations import WebSearch
from otaku.backend.api.story.settings import LAYOUT_SETTING, StorySetting
from otaku.backend.domain.app._paths import Paths
from otaku.settings import models as models_file
from otaku.store.schema import StorySettingDB
from otaku.terminal.screens import dossier as screen_dossier
from scenarios.api.app.test_app import _in_use
from scenarios.api.story.test_stories import (
    HOW_TO_ASK,
    messages,
    prompt_of,
    two_stories,
    with_prompt,
)
from scenarios.support import server as scripted
from scenarios.support.harness import App, launch, set_config_provider
from scenarios.support.screens import (
    CTRL_S,
    DELETE,
    DOWN,
    ENTER,
    ESC,
    RIGHT,
    SPACE,
    TAB,
    run_screen,
)

# The ask instruction the settings stories put into prompts.toml.
EMULATION = "emulate_text_completion"


class TestMode:
    """`/set mode`, the terminal's own command: `story` stores the canvas
    layout, `chat` the manuscript — the story's layout setting, so it
    forks and dies with the story; a bare call or a word it does not
    take changes nothing."""

    def test_a_word_stores_its_layout(self, app: App) -> None:
        app.play("/set mode story")
        assert app.layout is Layout.CANVAS
        app.play("/set mode CHAT")
        assert app.layout is Layout.MANUSCRIPT
        # the story's own: it rides the story's first turn into the store
        app.play("/set mode story")
        app.play("I push the door.")
        stored = app.store.stories.get_settings(app.story_id)
        assert [(row.name, row.layout) for row in stored] == [("layout", Layout.CANVAS)]

    def test_a_bare_call_or_a_word_it_does_not_take_changes_nothing(self, app: App) -> None:
        app.play("/set mode story")
        for line in ("/set mode", "/set mode novel", "/set mode scroll"):
            app.play(line)
            assert app.layout is Layout.CANVAS, line

    def test_the_novel_plays_over_the_text_wire_only_on_a_plain_text_model(self, tmp_path) -> None:
        root = tmp_path / "state"
        server = scripted.ModelServer()
        server.status = True
        server.types["test-model"] = "vlm"
        server.config_types["test-model"] = "gemma4"  # an instruct model: a template, not plain
        try:
            set_config_provider(root, server, name="omlx")
            app = launch(root, server, spec="omlx/test-model")
            try:
                app.api.stories.settings.update(app.ensure_story(), "layout", layout=Layout.NOVEL)
                # a novel on a model that cannot continue a bare text is a canvas
                assert not _in_use(app).can_complete_text
                assert app.api.stories.get(app.story_id).completion_type is CompletionType.CHAT
            finally:
                app.close()
            # named a plain-text model by hand, the novel goes as one text
            paths = Paths.resolve(root)
            models_file.save(paths.models_file, "test-model", {models_file.TEMPLATE_KEY: "plain"})
            app = launch(root, server, spec="omlx/test-model")
            try:
                app.api.stories.settings.update(app.ensure_story(), "layout", layout=Layout.NOVEL)
                assert _in_use(app).can_complete_text
                assert app.api.stories.get(app.story_id).completion_type is CompletionType.TEXT
            finally:
                app.close()
        finally:
            server.close()


class TestSettingsOfTools:
    def test_a_tool_switched_on_sends_its_prompt_before_the_newest_line(
        self, server, tmp_path
    ) -> None:
        # The prompt comes FROM prompts.toml, so the file is edited:
        # asserting the built-in wording would pass with the load path gone.
        app = with_prompt(tmp_path / "state", server)
        try:
            app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
            app.play("I push the door.")
            assert messages(app, "I push the door.") == [
                ("user", f"((OOC: {HOW_TO_ASK}))\n\nI push the door.")
            ]
            # Sent, never stored: the story holds the line as typed.
            (line, _reply) = app.store.stories.get_messages(app.story_id)
            assert line.body == "I push the door."
        finally:
            app.close()

    def test_switched_off_it_is_gone(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        try:
            app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
            app.play("I push the door.")
            app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=False)
            app.play("I step inside.")
            sent = messages(app, "I step inside.")
            assert not any(HOW_TO_ASK in body for _, body in sent)
        finally:
            app.close()

    def test_in_the_system_message_it_follows_the_premise_bare(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        try:
            app.play("/system Be terse.")
            app.api.stories.settings.update(
                app.ensure_story(), "allow_questions", enabled=True, position=InjectionPosition()
            )
            app.play("I push the door.")
            assert messages(app, "I push the door.") == [
                ("system", f"Be terse.\n\n{HOW_TO_ASK}"),
                ("user", "I push the door."),
            ]
            # The premise itself is the user's text, untouched.
            assert app.store.stories.get_system(app.story_id) == "Be terse."
        finally:
            app.close()

    def test_a_position_counts_the_readers_messages_from_the_end(self, server, tmp_path) -> None:
        # 2 is before the reader's previous line: the replies between are
        # not counted.
        app = with_prompt(tmp_path / "state", server)
        try:
            app.play("One.")
            app.play("Two.")
            app.api.stories.settings.update(
                app.ensure_story(), "allow_questions", enabled=True, position=InjectionPosition(2)
            )
            app.play("Three.")
            bodies = [body for _, body in messages(app, "Three.")]
            assert bodies[2] == f"((OOC: {HOW_TO_ASK}))\n\nTwo."
            assert bodies[4] == "Three."
        finally:
            app.close()


class TestShownTools:
    """Which tools' calls a reader is shown, as the story says it: the
    notes open while their display switch is on, folded while it is off —
    a frontend that can fold draws them by their label — and nowhere
    while the tool is off."""

    def test_notes_are_shown_while_displayed_folded_while_not_and_gone_when_off(
        self, app: App
    ) -> None:
        story = app.ensure_story()
        app.api.stories.settings.update(
            story, "allow_assistant_notes", enabled=True, display_notes=True
        )
        facts = app.api.stories.get(story)
        assert ("note" in facts.shown_tools, "note" in facts.folded_tools) == (True, False)
        app.api.stories.settings.update(story, "allow_assistant_notes", display_notes=False)
        facts = app.api.stories.get(story)
        assert ("note" in facts.shown_tools, "note" in facts.folded_tools) == (False, True)
        app.api.stories.settings.update(story, "allow_assistant_notes", enabled=False)
        facts = app.api.stories.get(story)
        assert "note" not in facts.shown_tools | facts.folded_tools


class TestSettingsOfReminders:
    def test_both_go_out_of_character_the_shared_one_leading(self, app: App) -> None:
        # A reminder rides a message of the reader's, where bare text
        # would read as the reader's line.
        app.api.shared_reminder.update(text="Stay grim.")
        app.api.stories.settings.update(
            app.ensure_story(), "use_shared_reminder", enabled=True, position=InjectionPosition(1)
        )
        app.api.stories.settings.update(
            app.ensure_story(),
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(1),
            reminder_text="[Here: rain]",
        )
        app.play("I push the door.")
        assert messages(app, "I push the door.") == [
            ("user", "((OOC: Stay grim.))\n\n((OOC: [Here: rain]))\n\nI push the door.")
        ]

    def test_each_is_a_switch_of_its_own(self, app: App) -> None:
        app.api.shared_reminder.update(text="Stay grim.")
        app.api.stories.settings.update(
            app.ensure_story(),
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(1),
            reminder_text="[Here: rain]",
        )
        app.play("I push the door.")
        assert messages(app, "I push the door.") == [
            ("user", "((OOC: [Here: rain]))\n\nI push the door.")
        ]

    def test_each_rides_at_its_own_position(self, app: App) -> None:
        app.api.shared_reminder.update(text="Stay grim.")
        app.play("One.")
        app.api.stories.settings.update(
            app.ensure_story(), "use_shared_reminder", enabled=True, position=InjectionPosition(2)
        )
        app.api.stories.settings.update(
            app.ensure_story(),
            "use_story_reminder",
            enabled=True,
            position=InjectionPosition(1),
            reminder_text="[Here: rain]",
        )
        app.play("Two.")
        bodies = [body for _, body in messages(app, "Two.")]
        assert bodies[0] == "((OOC: Stay grim.))\n\nOne."
        assert bodies[2] == "((OOC: [Here: rain]))\n\nTwo."

    def test_the_text_is_shared_and_the_switch_is_each_story_s(self, app: App) -> None:
        app.api.shared_reminder.update(text="Stay grim.")
        app.api.stories.settings.update(
            app.ensure_story(), "use_shared_reminder", enabled=True, position=InjectionPosition(1)
        )
        app.play("I push the door.")
        app.play("/new")
        app.play("A second story.")  # a new story has it off
        assert messages(app, "A second story.") == [("user", "A second story.")]
        app.api.stories.settings.update(
            app.ensure_story(), "use_shared_reminder", enabled=True, position=InjectionPosition(1)
        )
        app.play("And on.")
        assert messages(app, "And on.")[-1] == ("user", "((OOC: Stay grim.))\n\nAnd on.")

    def test_a_reminder_with_nothing_written_sends_nothing(self, app: App) -> None:
        app.api.stories.settings.update(
            app.ensure_story(), "use_story_reminder", enabled=True, position=InjectionPosition(1)
        )
        app.play("I push the door.")
        assert messages(app, "I push the door.") == [("user", "I push the door.")]

    def test_cleared_it_is_gone_from_the_store(self, app: App) -> None:
        app.api.shared_reminder.update(text="Stay grim.")
        assert app.store.settings.get("shared_reminder") == "Stay grim."
        assert app.api.shared_reminder.get().text == "Stay grim."
        app.api.shared_reminder.update(text="")
        assert app.store.settings.get("shared_reminder") == ""
        assert app.api.shared_reminder.get().text == ""


class TestToolsTab:
    """The dossier's Tools tab: one row per captioned setting — Space
    checks and unchecks it — with the setting's fields in the panel
    beside: Enter or Tab moves in, ↑/↓ walk the fields, Enter acts on
    one (a checkbox toggles, the depth opens its dropdown, a reminder's
    text edits in place, the instructions open the prompt over the
    panel), Esc returns to the list. Every change lands through
    `backend.api`, so the store is what the keys are held against."""

    def test_space_checks_and_unchecks_a_setting(self, app: App) -> None:
        app.play("I push the door.")
        self.tools(app, SPACE + ESC)
        assert self.setting(app, "allow_questions").enabled
        self.tools(app, SPACE + ESC)
        assert not self.setting(app, "allow_questions").enabled

    def test_the_depth_is_picked_from_the_dropdown(self, app: App) -> None:
        app.play("I push the door.")
        # questions: Enter into the panel lands on the instructions; ↓ to
        # the depth; Enter opens the closed list, ↓ picks the next place,
        # Enter sets it; Esc back to the list, Esc quits.
        self.tools(app, ENTER + DOWN + ENTER + DOWN + ENTER + ESC + ESC)
        assert self.setting(app, "allow_questions").injection_position == InjectionPosition(2)
        # Tab is the other way in, and never a way back
        self.tools(app, TAB + DOWN + ENTER + DOWN + ENTER + ESC + ESC)
        assert self.setting(app, "allow_questions").injection_position == InjectionPosition(3)

    def test_the_story_reminder_edits_in_place(self, app: App) -> None:
        app.play("I push the door.")
        # fifth row, after the four tools; its first field is the text:
        # Enter edits, Ctrl+S saves
        self.tools(app, DOWN * 4 + ENTER + ENTER + "Rain." + CTRL_S + ESC + ESC)
        assert self.setting(app, "use_story_reminder").reminder_text == "Rain."

    def test_the_shared_reminder_is_every_storys(self, app: App) -> None:
        app.play("I push the door.")
        self.tools(app, DOWN * 5 + ENTER + ENTER + "Stay grim." + CTRL_S + ESC + ESC)
        assert app.api.shared_reminder.get().text == "Stay grim."

    def test_the_notes_display_is_a_checkbox_in_the_panel(self, app: App) -> None:
        app.play("I push the door.")
        # fourth row: Space in the panel toggles the display; Space on the
        # list, after Esc, checks the tool itself
        self.tools(app, DOWN * 3 + ENTER + SPACE + ESC + SPACE + ESC)
        notes = self.setting(app, "allow_assistant_notes")
        assert (notes.enabled, notes.display_notes) == (True, False)

    def test_the_web_search_key_is_typed_in_place_and_delete_forgets_it(self, app: App) -> None:
        app.play("I push the door.")
        # second row; its fields are the instructions, the key, the depth:
        # the key is typed over where it stands and saved with Ctrl+S
        self.tools(app, DOWN + ENTER + DOWN + ENTER + "tvly-abc" + CTRL_S + ESC + ESC)
        assert app.api.integrations.web_search.get() == WebSearch(True)
        raw = app.paths.integrations_file.read_text()
        assert "tvly-abc" not in raw  # never plain in the file
        assert tomllib.loads(raw)["web_search"]["api_key"].startswith("sealed:")
        # Delete on the field forgets it, file and app both
        self.tools(app, DOWN + ENTER + DOWN + DELETE + ESC + ESC)
        assert app.api.integrations.web_search.get() == WebSearch(False)
        file = tomllib.loads(app.paths.integrations_file.read_text())
        assert file["web_search"]["api_key"] == ""

    def test_the_emulation_is_the_last_row_its_instructions_then_its_depth(self, app: App) -> None:
        app.play("I push the door.")
        # seventh row, after the reminders: off until checked
        self.tools(app, DOWN * 6 + SPACE + ESC)
        assert self.setting(app, EMULATION).enabled
        # its fields are its instructions, then the depth: the dropdown's
        # next place after the system message is before the reader's
        # latest message
        self.tools(app, DOWN * 6 + ENTER + DOWN + ENTER + DOWN + ENTER + ESC + ESC)
        assert self.setting(app, EMULATION).injection_position == InjectionPosition(1)
        # the instructions are its prompt, edited over the panel
        self.tools(app, DOWN * 6 + ENTER + ENTER + "Go on. " + CTRL_S + ESC + ESC)
        assert prompt_of(app, EMULATION).startswith("Go on. ")

    def test_the_instructions_row_edits_the_tools_prompt(self, app: App) -> None:
        app.play("I push the door.")
        shipped = prompt_of(app, "allow_questions")
        # the row opens the prompt over the panel; typed at its start
        self.tools(app, ENTER + ENTER + "Be brief. " + CTRL_S + ESC + ESC)
        assert prompt_of(app, "allow_questions") == "Be brief. " + shipped
        # Esc in the editor writes nothing
        self.tools(app, ENTER + ENTER + "Dropped. " + ESC + ESC + ESC)
        assert prompt_of(app, "allow_questions") == "Be brief. " + shipped
        # the notes tool's instructions are its own: the fourth row, the
        # field after its display checkbox
        self.tools(app, DOWN * 3 + ENTER + DOWN + ENTER + "Short. " + CTRL_S + ESC + ESC)
        assert prompt_of(app, "allow_assistant_notes").startswith("Short. ")

    def test_the_prompts_file_holds_what_was_saved(self, app: App) -> None:
        app.play("I push the door.")
        self.tools(app, ENTER + ENTER + "Be brief. " + CTRL_S + ESC + ESC)
        text = app.paths.prompts_file.read_text()
        assert "Be brief. " in text

    def tools(self, app: App, keys: str) -> None:
        # the dossier opens on the scenes; two tabs to the right sit the tools
        with contextlib.suppress(EOFError):
            run_screen(
                RIGHT + RIGHT + keys,
                lambda: screen_dossier.browse(app.api, app.story_id, "scenes"),
            )

    def setting(self, app: App, name: str) -> StorySetting:
        assert app.story_id is not None
        return app.api.stories.settings.get(app.story_id, name)


class TestSettingsAreTheStorys:
    def test_a_switch_flipped_before_the_first_turn_is_kept(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        try:
            assert app.story_id is None
            app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
            app.play("I push the door.")
            assert HOW_TO_ASK in messages(app, "I push the door.")[-1][1]
            stored = app.store.stories.get_settings(app.story_id)
            assert [(s.name, s.enabled) for s in stored] == [("allow_questions", True)]
        finally:
            app.close()

    def test_the_switches_survive_a_launch(self, server, tmp_path) -> None:
        app = with_prompt(tmp_path / "state", server)
        app.api.stories.settings.update(
            app.ensure_story(), "allow_questions", enabled=True, position=InjectionPosition(2)
        )
        app.api.stories.settings.update(
            app.ensure_story(), "allow_assistant_notes", display_notes=False
        )
        app.play("I push the door.")
        app.close()
        app = launch(tmp_path / "state", server)
        try:
            ask = setting(app, "allow_questions")
            assert (ask.enabled, ask.injection_position) == (True, InjectionPosition(2))
            assert setting(app, "allow_assistant_notes").display_notes is False
        finally:
            app.close()

    def test_a_new_story_starts_with_every_switch_off(self, app: App) -> None:
        app.api.stories.settings.update(app.ensure_story(), "allow_questions", enabled=True)
        app.api.stories.settings.update(app.ensure_story(), EMULATION, enabled=True)
        app.play("I push the door.")
        app.play("/new")
        assert not any(each.enabled for each in switches(app))
        # the one value among them starts at its default: a story is
        # always read in a layout
        assert setting(app, "layout").layout is Layout.MANUSCRIPT

    def test_the_layout_is_the_storys_and_reads_back(self, app: App) -> None:
        app.api.stories.settings.update(app.ensure_story(), "layout", layout=Layout.CANVAS)
        app.play("I push the door.")
        assert app.store.stories.get_settings(app.story_id) == (
            StorySettingDB("layout", enabled=True, layout=Layout.CANVAS),
        )
        assert setting(app, "layout").layout is Layout.CANVAS
        app.play("/new")
        assert setting(app, "layout").layout is Layout.MANUSCRIPT

    def test_a_fork_plays_the_way_its_origin_does(self, app: App) -> None:
        app.api.stories.settings.update(
            app.ensure_story(), "allow_assistant_notes", enabled=True, position=InjectionPosition()
        )
        app.play("I push the door.")
        origin = app.story_id
        app.play("/fork")
        assert app.story_id != origin
        assert app.store.stories.get_settings(app.story_id) == (
            app.store.stories.get_settings(origin)
        )
        notes = setting(app, "allow_assistant_notes")
        assert (notes.enabled, notes.injection_position) == (True, InjectionPosition())

    def test_a_story_the_chat_is_not_in_is_reached_by_its_id(self, app: App) -> None:
        first, second = two_stories(app)
        app.api.stories.settings.update(first, "allow_questions", enabled=True)
        # The chat's story is left as it was; the named one took the switch.
        assert not setting(app, "allow_questions").enabled
        assert app.api.stories.settings.get(first, "allow_questions").enabled
        assert app.story_id == second


class TestSettingsRefused:
    def test_what_a_setting_cannot_take_is_refused(self, app: App) -> None:
        for asked in (
            {"name": "plan", "enabled": True},
            # Past the deepest place. (What is not a position at all —
            # "end", 0, "2" — never becomes one: `InjectionPosition.from_value`
            # reads None at the wire.)
            {"name": "allow_questions", "position": InjectionPosition(9)},
            {"name": "allow_questions", "reminder_text": "Ask about the weather."},
            # Only the notes tool has something to display.
            {"name": "allow_questions", "display_notes": False},
            # A reminder is never the system message's: that is the premise's.
            {"name": "use_story_reminder", "position": InjectionPosition()},
            {"name": "use_shared_reminder", "position": InjectionPosition()},
            # The shared reminder's text is no one story's.
            {"name": "use_shared_reminder", "reminder_text": "Stay grim."},
            # The layout has no switch, and a switch has no layout.
            {"name": "layout", "enabled": True},
            {"name": "allow_questions", "layout": Layout.CANVAS},
            # The emulation takes neither a reminder nor a display.
            {"name": EMULATION, "reminder_text": "Go on."},
            {"name": EMULATION, "display_notes": False},
        ):
            with pytest.raises(Refused):
                name = asked.pop("name")
                app.api.stories.settings.update(app.ensure_story(), name, **asked)  # type: ignore[arg-type]
        # Nothing was written by a refusal.
        assert not any(each.enabled for each in switches(app))


def switches(app: App) -> list[StorySetting]:
    """The story's settings that are switches — every one but the
    layout, which is a value and always in force."""
    assert app.story_id is not None
    return [
        each for each in app.api.stories.settings.list(app.story_id) if each.name != LAYOUT_SETTING
    ]


def setting(app: App, name: str) -> StorySetting:
    """One setting of the chat's story, as it stands."""
    assert app.story_id is not None
    return app.api.stories.settings.get(app.story_id, name)
