"""Cards as context material: a character's profile written as TOML and
read back (`CharacterProfile.to_toml`, `from_toml`) — any card text,
field for field — the wire block composed from a story character's
profile (`card_to_wire`), bound at wire time (`bind`, the vocabulary
`binds` answers for), and what a card injects beside it
(`card_injections`) — each reading a profile an older version wrote
(`read_toml`)."""

import tomllib

import pytest

from otaku.context.cards import (
    CharacterProfile,
    bind,
    binds,
    card_injections,
    card_to_wire,
    read_toml,
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
        profile = CharacterProfile(
            name="Elara", user="Alex", description="{{char}} guards {{user}}", personality="stern"
        )
        got = card_to_wire(profile.to_toml(), self.TEMPLATE, char="Elara")
        assert "You are joined by Elara." in got
        assert "Description: Elara guards Alex" in got
        assert "Personality: stern" in got

    def test_the_name_is_the_characters_not_the_texts(self) -> None:
        # A copy written before 0.8.0 carries a `name` line; the
        # character's own name is the one that binds.
        old = "name = 'Elara'\n\nuser = 'Alex'\n\ndescription = '{{char}} waits for {{user}}'"
        got = card_to_wire(old, self.TEMPLATE, char="Mara")
        assert "You are joined by Mara." in got
        assert "Description: Mara waits for Alex" in got

    def test_a_line_of_empty_placeholders_is_dropped(self) -> None:
        profile = CharacterProfile(name="Elara", user="A", description="warden")
        got = card_to_wire(profile.to_toml(), self.TEMPLATE, char="Elara")
        assert "Scenario:" not in got
        assert "How they speak:" not in got  # no examples — the label goes with them
        assert got.endswith("\n))")  # the close keeps its own line

    def test_a_template_still_naming_the_depth_note_gets_nothing_there(self) -> None:
        # The reminder rides on its own now; a template edited before
        # keeps its line, which reads empty and drops.
        profile = CharacterProfile(name="Elara", description="warden", reminder="always hits")
        got = card_to_wire(profile.to_toml(), "D: {description}\nNote: {depth_note}", char="Elara")
        assert got == "D: warden"

    def test_the_examples_join_as_blocks(self) -> None:
        profile = CharacterProfile(name="E", examples=("a: hi", "b: yo"))
        got = card_to_wire(profile.to_toml(), "X:\n{examples}", char="E")
        assert "a: hi\n\nb: yo" in got

    def test_braces_inside_card_text_are_never_placeholders(self) -> None:
        # One pass over the TEMPLATE only: values are not re-scanned.
        profile = CharacterProfile(name="E", description="use {personality} literally")
        got = card_to_wire(profile.to_toml(), "D: {description}\nP: {personality}", char="E")
        assert "D: use {personality} literally" in got
        assert "\nP:" not in got  # personality itself is empty -> line dropped

    def test_an_unknown_placeholder_is_left_as_written(self) -> None:
        got = card_to_wire(CharacterProfile(name="E").to_toml(), "hello {nonsense}", char="E")
        assert got == "hello {nonsense}"

    def test_a_key_outside_the_vocabulary_fills_nothing(self) -> None:
        # what the profile does not read (`from_toml`), the wire does not send
        assert card_to_wire("model = 'x'", "M: {model}", char="E") == "M: {model}"

    def test_the_user_fills_from_the_profile(self) -> None:
        assert card_to_wire("user = 'Alex'", "P: {user}", char="E") == "P: Alex"
        text = CharacterProfile(name="E", description="d").to_toml()
        assert card_to_wire(text, "P: {user}\nD: {description}", char="E") == "D: d"

    def test_a_line_with_one_placeholder_filled_stays(self) -> None:
        text = CharacterProfile(name="E", description="warden").to_toml()
        assert card_to_wire(text, "{description} / {scenario}", char="E") == "warden / "


class TestBind:
    def test_both_names_in_any_case(self) -> None:
        text = "{{char}} met {{USER}}; {{ Char }} smiled at {{user}}"
        assert bind(text, char="Elara", user="Alex") == "Elara met Alex; Elara smiled at Alex"

    def test_the_tavern_era_spellings(self) -> None:
        assert bind("<BOT> waves to <USER>", char="Elara", user="Alex") == "Elara waves to Alex"
        assert bind("<bot> waves to <User>", char="Elara", user="Alex") == "Elara waves to Alex"

    def test_unknown_macros_stay_written(self) -> None:
        assert bind("roll {{roll:d20}}", char="E", user="U") == "roll {{roll:d20}}"

    def test_original_stands_for_nothing(self) -> None:
        # SillyTavern's stand-in for the prompt a card's replaces; here a
        # card's joins the story's instead.
        assert bind("{{original}}Be {{ Original }}kind.", char="E", user="U") == "Be kind."

    def test_a_backslash_in_a_name_survives(self) -> None:
        assert bind("{{char}}!", char=r"El\ara", user="U") == r"El\ara!"
        assert bind("{{user}}!", char="E", user=r"Al\1ex") == r"Al\1ex!"


class TestBinds:
    """Whether `bind` resolves an exact token: the vocabulary question."""

    KNOWN = ("{{char}}", "{{ USER }}", "<BOT>", "<user>", "{{original}}")
    UNKNOWN = ("{{roll:d20}}", "{{random}}")

    def test_the_vocabulary_binds(self) -> None:
        for macro in self.KNOWN:
            assert binds(macro), macro

    def test_anything_else_or_inexact_does_not(self) -> None:
        for macro in (*self.UNKNOWN, "{{char}} x", "{{char}}{{user}}"):
            assert not binds(macro), macro

    def test_it_answers_what_bind_does_to_the_token(self) -> None:
        for macro in (*self.KNOWN, *self.UNKNOWN):
            assert binds(macro) == (bind(macro, char="C", user="U") != macro), macro


class TestProfileToml:
    """A profile written as TOML and read back: every non-empty field, the
    name left to its row, the `user` a story's copy binds where it binds
    one."""

    def test_round_trips_field_for_field(self) -> None:
        profile = CharacterProfile(
            name="Elara",
            user="Alex",
            description="line one\nline 'two'",
            personality="it's complicated",
            greeting='she says "hi"\nand waits',
            alternate_greetings=("a", "b"),
            examples=("x: 1", "y: 2"),
            file_name="elara.png",
        )
        assert CharacterProfile.from_toml(profile.to_toml(), name="Elara") == profile

    def test_the_name_is_left_to_its_row(self) -> None:
        data = tomllib.loads(CharacterProfile(name="Elara", description="warden").to_toml())
        assert data == {"description": "warden"}

    def test_a_storys_copy_records_the_user_it_binds(self) -> None:
        profile = CharacterProfile(name="Elara", user="Alex", description="warden")
        assert tomllib.loads(profile.to_toml()) == {"user": "Alex", "description": "warden"}

    def test_the_user_comes_first(self) -> None:
        profile = CharacterProfile(name="Elara", user="Alex", description="warden")
        assert next(iter(tomllib.loads(profile.to_toml()))) == "user"

    def test_fields_are_one_blank_line_apart(self) -> None:
        text = CharacterProfile(name="E", description="d", personality="p").to_toml()
        assert len(text.split("\n\n")) == 2

    def test_the_text_is_edge_clean(self) -> None:
        # exactly what a document round-trip, which trims body edges, gives back
        for profile in (
            CharacterProfile(name="E", description="first\nline"),
            CharacterProfile(name="E", description="d", creator_notes="last\n"),
            CharacterProfile(name="E", greeting="\nopens on a break"),
        ):
            text = profile.to_toml()
            assert text == text.strip(), profile

    def test_a_default_depth_is_not_written(self) -> None:
        assert tomllib.loads(CharacterProfile(name="E", reminder="r").to_toml()) == {
            "reminder": "r"
        }

    @pytest.mark.parametrize(
        "profile",
        [
            CharacterProfile(name="E", description="a\r\nb"),  # an imported card's line ends
            CharacterProfile(name="E", personality="bell\x07"),
            CharacterProfile(name="E", personality="delete\x7f"),
            CharacterProfile(name="E", scenario="tab\there"),
            CharacterProfile(name="E", greeting="a\n'''b"),
            CharacterProfile(name="E", description="\nstarts on a new line"),
            CharacterProfile(name="E", creator_notes="ends with a quote'\nx'"),
            CharacterProfile(name="E", greeting="Hi 🎭"),
            CharacterProfile(name="E", greeting="Hi 🎭\n'''"),
            CharacterProfile(name="E", examples=("Hi 🎭", "multi\nline", 'q "x"')),
            CharacterProfile(name="E", alternate_greetings=("✨",)),
        ],
    )
    def test_any_card_text_round_trips(self, profile: CharacterProfile) -> None:
        assert CharacterProfile.from_toml(profile.to_toml(), name="E") == profile

    def test_empty_fields_are_omitted(self) -> None:
        text = CharacterProfile(name="E").to_toml()
        assert "description" not in text and "examples" not in text

    def test_macros_are_kept_unbound(self) -> None:
        # The profile keeps the source; the binding happens on the wire.
        profile = CharacterProfile(
            name="Elara", user="Alex", description="{{char}} waits for {{user}}"
        )
        assert tomllib.loads(profile.to_toml())["description"] == "{{char}} waits for {{user}}"

    def test_a_triple_quote_in_card_text_still_round_trips(self) -> None:
        profile = CharacterProfile(name="E", description="she said '''no'''")
        assert CharacterProfile.from_toml(profile.to_toml(), name="E") == profile

    def test_the_reminders_depth_round_trips_as_a_number(self) -> None:
        profile = CharacterProfile(name="E", reminder="r", reminder_depth=5)
        assert CharacterProfile.from_toml(profile.to_toml(), name="E") == profile

    def test_it_reads_under_the_name_given(self) -> None:
        got = CharacterProfile.from_toml("description = 'd'", name="E")
        assert got == CharacterProfile(name="E", description="d")

    def test_an_old_copys_name_line_is_left_out(self) -> None:
        old = "name = 'Elara'\n\nuser = 'Alex'\n\ndescription = 'd'"
        got = CharacterProfile.from_toml(old, name="Mara")
        assert got == CharacterProfile(name="Mara", user="Alex", description="d")

    def test_a_key_outside_the_vocabulary_is_left_out(self) -> None:
        got = CharacterProfile.from_toml("model = 'ollama/x'\ndescription = 'd'", name="E")
        assert got == CharacterProfile(name="E", description="d")

    def test_no_text_is_a_bare_profile(self) -> None:
        assert CharacterProfile.from_toml("", name="E") == CharacterProfile(name="E")

    def test_an_old_copys_depth_note_reads_as_the_reminder(self) -> None:
        old = "name = 'E'\n\nuser = 'U'\n\ndepth_note = 'always hits'"
        got = CharacterProfile.from_toml(old, name="E")
        assert got == CharacterProfile(name="E", user="U", reminder="always hits")

    def test_a_text_that_is_no_toml_is_refused(self) -> None:
        with pytest.raises(tomllib.TOMLDecodeError):
            CharacterProfile.from_toml("description = = 'x'", name="E")

    def test_a_profile_an_older_version_escaped_reads_again(self) -> None:
        # 0.7.0 and 0.8.0 wrote a character past U+FFFF as a JSON surrogate
        # pair, which TOML refuses
        old = 'examples = ["Hi \\ud83c\\udfad"]\n\nsystem_prompt = "Be \\ud83c\\udfad."'
        assert CharacterProfile.from_toml(old, name="E").examples == ("Hi 🎭",)
        assert card_to_wire(old, "X: {examples}", char="E") == "X: Hi 🎭"
        system, _ = card_injections(old, char="E")
        assert system.text == "Be 🎭."

    def test_an_escaped_backslash_is_no_older_versions_escape(self) -> None:
        text = 'description = "\\\\ud83c\\\\udfad"'
        assert read_toml(text) == {"description": "\\ud83c\\udfad"}


class TestCardInjections:
    """What a card asks of the request beside its row, read from a story
    character's profile: its system prompt joined to the system message,
    its reminder before the reader's message at its depth — both bound to
    the character's name and the profile's `user`; empty, so never sent,
    where the card has none."""

    def test_the_system_prompt_and_the_reminder_go_where_they_belong(self) -> None:
        profile = CharacterProfile(
            name="Wren",
            user="Alex",
            system_prompt="{{char}} never lies to {{user}}.",
            reminder="{{char}} answers in riddles.",
            reminder_depth=3,
        )
        system, reminder = card_injections(profile.to_toml(), char="Wren")
        assert (system.text, system.position) == ("Wren never lies to Alex.", InjectionPosition())
        assert (reminder.text, reminder.position) == (
            "Wren answers in riddles.",
            InjectionPosition(3),
        )

    def test_the_name_is_the_characters_not_the_texts(self) -> None:
        old = "name = 'Wren'\n\nuser = 'Alex'\n\nsystem_prompt = 'Be {{char}}.'"
        system, _ = card_injections(old, char="Robin")
        assert system.text == "Be Robin."

    def test_original_leaves_nothing_behind(self) -> None:
        profile = CharacterProfile(name="Wren", system_prompt="{{original}}\nKeep it short.")
        system, _ = card_injections(profile.to_toml(), char="Wren")
        assert system.text == "Keep it short."

    def test_a_depth_below_one_is_read_as_one(self) -> None:
        text = "user = 'Alex'\n\nreminder = 'r'\n\nreminder_depth = 0"
        _, reminder = card_injections(text, char="Wren")
        assert reminder.position == InjectionPosition(1)

    def test_an_old_copys_depth_note_is_the_reminder(self) -> None:
        old = "name = 'Wren'\n\nuser = 'Alex'\n\ndepth_note = '{{char}} hums.'"
        _, reminder = card_injections(old, char="Wren")
        assert (reminder.text, reminder.position) == ("Wren hums.", InjectionPosition(2))

    def test_a_card_without_them_injects_nothing(self) -> None:
        system, reminder = card_injections(CharacterProfile(name="Wren").to_toml(), char="Wren")
        assert (system.text, reminder.text) == ("", "")

    def test_a_profile_that_is_no_toml_is_refused(self) -> None:
        with pytest.raises(tomllib.TOMLDecodeError):
            card_injections("name = = 'x'", char="Wren")
