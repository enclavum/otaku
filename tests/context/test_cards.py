"""Cards as context material: the wire block composed from the archive
(`card_to_wire`), bound at wire time, and what a card injects beside it
(`card_injections`)."""

import tomllib

import pytest

from otaku.context.cards import (
    StoryCard,
    bind,
    card_injections,
    card_to_wire,
    card_toml,
    fields_toml,
    read_fields,
)
from otaku.store.schema import InjectionPosition


class TestCardToWire:
    TEMPLATE = (
        "((OOC: You are joined by {name}.\n"
        "Description: {description}\n"
        "Personality: {personality}\n"
        "Scenario: {scenario}\n"
        "How they speak: {examples}\n"
        "))"
    )

    def test_fills_and_binds_in_one_pass(self) -> None:
        card = StoryCard(name="Elara", description="{{char}} guards {{user}}", personality="stern")
        got = card_to_wire(card_toml(card, user="Alex"), self.TEMPLATE)
        assert "You are joined by Elara." in got
        assert "Description: Elara guards Alex" in got
        assert "Personality: stern" in got

    def test_a_line_of_empty_placeholders_is_dropped(self) -> None:
        card = StoryCard(name="Elara", description="warden")
        got = card_to_wire(card_toml(card, user="A"), self.TEMPLATE)
        assert "Scenario:" not in got
        assert "How they speak:" not in got  # no examples — the label goes with them
        assert got.endswith("\n))")  # the close keeps its own line

    def test_a_template_still_naming_the_depth_note_gets_nothing_there(self) -> None:
        # The reminder rides on its own now; a template edited before
        # keeps its line, which reads empty and drops.
        card = StoryCard(name="Elara", description="warden", reminder="always hits")
        got = card_to_wire(card_toml(card, user="A"), "D: {description}\nNote: {depth_note}")
        assert got == "D: warden"

    def test_the_examples_join_as_blocks(self) -> None:
        card = StoryCard(name="E", examples=("a: hi", "b: yo"))
        got = card_to_wire(card_toml(card, user="U"), "X:\n{examples}")
        assert "a: hi\n\nb: yo" in got

    def test_braces_inside_card_text_are_never_placeholders(self) -> None:
        # One pass over the TEMPLATE only: values are not re-scanned.
        card = StoryCard(name="E", description="use {personality} literally")
        got = card_to_wire(card_toml(card, user="U"), "D: {description}\nP: {personality}")
        assert "D: use {personality} literally" in got
        assert "\nP:" not in got  # personality itself is empty -> line dropped

    def test_an_unknown_placeholder_is_left_as_written(self) -> None:
        got = card_to_wire(card_toml(StoryCard(name="E"), user="U"), "hello {nonsense}")
        assert got == "hello {nonsense}"


class TestBind:
    def test_both_names_in_any_case(self) -> None:
        text = "{{char}} met {{USER}}; {{ Char }} smiled at {{user}}"
        assert bind(text, char="Elara", user="Alex") == "Elara met Alex; Elara smiled at Alex"

    def test_the_tavern_era_spellings(self) -> None:
        assert bind("<BOT> waves to <USER>", char="Elara", user="Alex") == "Elara waves to Alex"

    def test_unknown_macros_stay_written(self) -> None:
        assert bind("roll {{roll:d20}}", char="E", user="U") == "roll {{roll:d20}}"

    def test_original_stands_for_nothing(self) -> None:
        # SillyTavern's stand-in for the prompt a card's replaces; here a
        # card's joins the story's instead.
        assert bind("{{original}}Be {{ Original }}kind.", char="E", user="U") == "Be kind."

    def test_a_backslash_in_a_name_survives(self) -> None:
        assert bind("{{char}}!", char=r"El\ara", user="U") == r"El\ara!"


class TestCardToml:
    def test_round_trips_field_for_field(self) -> None:
        card = StoryCard(
            name="Elara",
            description="line one\nline 'two'",
            personality="it's complicated",
            greeting='she says "hi"\nand waits',
            alternate_greetings=("a", "b"),
            examples=("x: 1", "y: 2"),
        )
        data = tomllib.loads(card_toml(card, user="Alex"))
        assert data["name"] == "Elara"
        assert data["user"] == "Alex"
        assert data["description"] == card.description
        assert data["personality"] == card.personality
        assert data["greeting"] == card.greeting
        assert data["alternate_greetings"] == ["a", "b"]
        assert data["examples"] == ["x: 1", "y: 2"]

    def test_empty_fields_are_omitted(self) -> None:
        text = card_toml(StoryCard(name="E"), user="U")
        assert "description" not in text and "examples" not in text

    def test_macros_are_archived_unbound(self) -> None:
        # The archive keeps the source; the bindings ride as data.
        card = StoryCard(name="Elara", description="{{char}} waits for {{user}}")
        data = tomllib.loads(card_toml(card, user="Alex"))
        assert data["description"] == "{{char}} waits for {{user}}"

    def test_a_triple_quote_in_card_text_still_round_trips(self) -> None:
        card = StoryCard(name="E", description="she said '''no'''")
        data = tomllib.loads(card_toml(card, user="U"))
        assert data["description"] == "she said '''no'''"


class TestCodexFields:
    """A codex card's fields: the archive's rows without the name, which
    the card keeps in a column of its own, and without `user`, which only
    a story binds — read back into the card they make."""

    def test_the_fields_leave_the_name_and_the_user_out(self) -> None:
        data = tomllib.loads(fields_toml(StoryCard(name="Elara", description="warden")))
        assert data == {"description": "warden"}

    def test_the_fields_read_back_whole_under_the_name_given(self) -> None:
        card = StoryCard(
            name="Elara",
            description="{{char}} waits",
            alternate_greetings=("a", "b"),
            examples=("x: 1",),
            file_name="elara.png",
        )
        assert read_fields(fields_toml(card), name="Elara") == card

    def test_a_storys_archive_reads_as_its_card_its_binding_left_behind(self) -> None:
        card = StoryCard(name="Elara", description="warden", examples=("x: 1",))
        archive = card_toml(card, user="Alex")
        assert read_fields(archive, name="Warden") == StoryCard(
            name="Warden", description="warden", examples=("x: 1",)
        )

    def test_a_key_outside_the_cards_vocabulary_is_left_out(self) -> None:
        assert read_fields("model = 'ollama/x'\ndescription = 'd'", name="E") == StoryCard(
            name="E", description="d"
        )

    def test_no_fields_is_a_bare_card(self) -> None:
        assert read_fields("", name="E") == StoryCard(name="E")

    def test_the_reminders_depth_round_trips_as_a_number(self) -> None:
        card = StoryCard(name="E", reminder="r", reminder_depth=5)
        assert read_fields(fields_toml(card), name="E") == card

    def test_an_old_archives_depth_note_reads_as_the_reminder(self) -> None:
        archive = "name = 'E'\n\nuser = 'U'\n\ndepth_note = 'always hits'"
        assert read_fields(archive, name="E") == StoryCard(name="E", reminder="always hits")


class TestCardInjections:
    """What a card asks of the request beside its row, read from a story
    character's archive: its system prompt joined to the system message,
    its reminder before the reader's message at its depth — both bound to
    the archive's names; empty, so never sent, where the card has none."""

    def test_the_system_prompt_and_the_reminder_go_where_they_belong(self) -> None:
        card = StoryCard(
            name="Wren",
            system_prompt="{{char}} never lies to {{user}}.",
            reminder="{{char}} answers in riddles.",
            reminder_depth=3,
        )
        system, reminder = card_injections(card_toml(card, user="Alex"))
        assert (system.text, system.position) == ("Wren never lies to Alex.", InjectionPosition())
        assert (reminder.text, reminder.position) == (
            "Wren answers in riddles.",
            InjectionPosition(3),
        )

    def test_original_leaves_nothing_behind(self) -> None:
        card = StoryCard(name="Wren", system_prompt="{{original}}\nKeep it short.")
        system, _ = card_injections(card_toml(card, user="Alex"))
        assert system.text == "Keep it short."

    def test_a_depth_below_one_is_read_as_one(self) -> None:
        archive = "name = 'Wren'\n\nuser = 'Alex'\n\nreminder = 'r'\n\nreminder_depth = 0"
        _, reminder = card_injections(archive)
        assert reminder.position == InjectionPosition(1)

    def test_an_old_archives_depth_note_is_the_reminder(self) -> None:
        archive = "name = 'Wren'\n\nuser = 'Alex'\n\ndepth_note = '{{char}} hums.'"
        _, reminder = card_injections(archive)
        assert (reminder.text, reminder.position) == ("Wren hums.", InjectionPosition(2))

    def test_a_card_without_them_injects_nothing(self) -> None:
        system, reminder = card_injections(card_toml(StoryCard(name="Wren"), user="Alex"))
        assert (system.text, reminder.text) == ("", "")

    def test_an_archive_that_is_no_toml_is_refused(self) -> None:
        with pytest.raises(tomllib.TOMLDecodeError):
            card_injections("name = = 'x'")
