"""The story's typed language.

`check` returns the usage line for a malformed line and None for a good
one — an unknown `/word` is prose, not an error. `read(line)` answers
with the direction the line opened with, prose included, so nothing has
to test for its absence; `request_kind` and `reply_kind` mark the two
rows — disagreeing for `/you`, which instructs out of character but
asks for the scene in character. `spans` reads a line as its story and
its words with the model: runs that join back into the line exactly,
out of character all of a /you or /ooc line after its token and
whatever follows an inliner, the tokens themselves story, the breaks at
the line's edges read past. `shown` is a stored line as both frontends
show it: those runs, but a /chapter line its chapter's heading once its
number is known. A /chapter line is the one direction played without an
answer.

`to_wire` composes what a stored turn sends. Two shapes reach it: a
turn stored before the syntax existed, whose prose was already stripped
of its prefix and whose framing already names its character (v1), and a
turn stored since, which holds the line exactly as typed (v2). An
inliner's text becomes its own enclosure; a cue is there only while its
turn is the newest.
"""

import random

from otaku.context.syntax import DIRECTIONS, read, roll_dice, shown, spans, to_wire
from otaku.store.schema import Message

# What a v2 turn stores: the template, placeholders intact.
ME = "<<{name} speaks>>\n{body}"
YOU = "<<now play {name}>>"
OOC = "<<aside: {body}>>"


def wire(body: str, template: str | None, *, is_last: bool) -> str:
    """`to_wire` over a stored row, the way every old case spelled it."""
    return to_wire(Message(role="user", body=body, template=template), is_last=is_last)


class TestCheck:
    def test_prose_is_always_valid(self) -> None:
        assert read("She looks up.").check() is None

    def test_an_unknown_command_is_prose(self) -> None:
        assert read("/shrug at it").check() is None

    def test_a_well_formed_me_passes(self) -> None:
        assert read("/me Elara: I step in.").check() is None

    def test_me_needs_a_colon(self) -> None:
        assert read("/me Elara I step in").check() == "Usage: /me NAME: PROMPT"

    def test_me_needs_prose_after_the_colon(self) -> None:
        assert read("/me Elara:").check() == "Usage: /me NAME: PROMPT"

    def test_me_needs_a_name(self) -> None:
        assert read("/me : I step in.").check() == "Usage: /me NAME: PROMPT"

    def test_you_needs_a_name(self) -> None:
        assert read("/you").check() == "Usage: /you NAME[: HINT]"

    def test_you_takes_an_optional_hint_after_a_colon(self) -> None:
        assert read("/you Elara: be cold").check() is None
        assert read("/you Elara:").check() is None  # an empty hint is the bare form

    def test_ooc_needs_prose(self) -> None:
        assert read("/ooc").check() == "Usage: /ooc PROMPT"

    def test_a_well_formed_roll_passes_with_or_without_an_action(self) -> None:
        assert read("/roll 1d20+5 I search the alcove").check() is None
        assert read("/roll 2d20kh1").check() is None

    def test_a_bare_roll_needs_dice(self) -> None:
        assert read("/roll").check() is not None

    def test_a_roll_spec_that_is_not_dice_is_refused(self) -> None:
        error = read("/roll banana I flail").check()
        assert error is not None and error.startswith("Usage: /roll DICE [PROMPT]")

    def test_an_inliner_needs_text_after_it(self) -> None:
        assert read("She looks up /cue").check() is not None

    def test_a_command_and_an_inliner_can_share_a_line(self) -> None:
        assert read("/me Elara: I step in /cue keep it tense").check() is None

    def test_a_chapter_needs_a_title(self) -> None:
        assert read("/chapter").check() is not None
        assert read("/chapter The Flood").check() is None


class TestTemplateField:
    # The template METHOD is gone: the caller resolves `template_field`
    # against its prompts at record time (this module reads no settings).
    # The pure remainder: each direction names its prompts.toml key.
    def test_prose_names_no_template(self) -> None:
        assert read("She looks up.").template_field == ""

    def test_each_direction_names_its_own(self) -> None:
        assert read("/me Elara: hi").template_field == "me_framing"
        assert read("/you Elara").template_field == "you_framing"
        assert read("/ooc What genre?").template_field == "ooc_framing"
        assert read("/roll 1d6").template_field == "roll_framing"
        assert read("/chapter The Flood").template_field == "chapter_framing"


class TestSpans:
    def test_the_runs_are_the_line_exactly(self) -> None:
        for line in (
            "/you Mara: be terse",
            "I open the door /cue make it quiet",
            "/me Mara: Hello /ooc keep it short",
            "\n\n/ooc after a break\n\n",
            "plain  spaced   prose",
            "",
        ):
            assert "".join(text for text, _ in spans(line)) == line

    def test_a_you_or_ooc_line_is_out_of_character_after_its_token(self) -> None:
        assert spans("/you Mara: be terse") == [("/you", False), (" Mara: be terse", True)]
        assert spans("/ooc slow down") == [("/ooc", False), (" slow down", True)]

    def test_what_follows_an_inliner_is_out_of_character(self) -> None:
        assert spans("I open the door /cue make it quiet") == [
            ("I open the door /cue", False),
            (" make it quiet", True),
        ]
        assert spans("/me Mara: Hello /ooc short") == [
            ("/me Mara: Hello /ooc", False),
            (" short", True),
        ]

    def test_a_you_line_with_an_inliner_is_one_aside_after_its_token(self) -> None:
        assert spans("/you Mara /cue be quick") == [("/you", False), (" Mara /cue be quick", True)]

    def test_me_and_roll_are_story(self) -> None:
        assert spans("/me Mara: Hello") == [("/me Mara: Hello", False)]
        assert spans("/roll 1d20 I jump") == [("/roll 1d20 I jump", False)]

    def test_a_token_with_nothing_after_it_is_story_alone(self) -> None:
        assert spans("/you") == [("/you", False)]
        assert spans("I go /cue") == [("I go /cue", False)]
        assert spans("I go /cue   ") == [("I go /cue   ", False)]

    def test_a_slash_inside_a_word_is_prose(self) -> None:
        assert spans("and/or https://x.co/cue now") == [("and/or https://x.co/cue now", False)]

    def test_the_breaks_at_a_lines_edges_are_read_past(self) -> None:
        assert spans("\n\n/ooc after a break") == [
            ("\n\n/ooc", False),
            (" after a break", True),
        ]


class TestDirections:
    def test_the_registry_holds_exactly_the_five(self) -> None:
        assert set(DIRECTIONS) == {"/me", "/you", "/ooc", "/chapter", "/roll"}

    def test_a_chapter_alone_is_played_without_an_answer(self) -> None:
        assert [token for token, line in DIRECTIONS.items() if not line.needs_answer] == [
            "/chapter"
        ]
        assert read("She looks up.").needs_answer


class TestKinds:
    def test_prose_plays_the_story(self) -> None:
        assert _kinds("She looks up.") == ("dialogue", "dialogue")

    def test_me_plays_the_story(self) -> None:
        assert _kinds("/me Elara: hi") == ("dialogue", "dialogue")

    def test_a_roll_plays_the_story(self) -> None:
        # The mechanics ride the framing; the action is scene like any
        # other, and the answer narrates in character.
        assert _kinds("/roll 1d20+5 I search") == ("dialogue", "dialogue")

    def test_ooc_asks_and_answers_out_of_character(self) -> None:
        assert _kinds("/ooc What genre?") == ("ooc", "ooc")

    def test_you_instructs_out_of_character_but_wants_the_scene(self) -> None:
        # The one place the two disagree. Inferring the reply from the row
        # would answer a character switch out of character.
        assert _kinds("/you Elara") == ("ooc", "dialogue")

    def test_an_inliner_changes_neither(self) -> None:
        assert _kinds("She looks up /ooc nervous") == ("dialogue", "dialogue")

    def test_a_chapter_is_a_kind_of_its_own(self) -> None:
        assert read("/chapter The Flood").request_kind == "chapter"


class TestShown:
    def test_a_line_shows_as_its_runs_of_story_and_words_with_the_model(self) -> None:
        assert shown("I wait /cue make it rain") == [
            ("I wait /cue", "prose"),
            (" make it rain", "ooc"),
        ]
        assert shown("She looks up.", chapter=2) == [("She looks up.", "prose")]

    def test_a_chapter_shows_its_numbered_heading(self) -> None:
        runs = shown("/chapter The Flood", chapter=3)
        assert [kind for _, kind in runs] == ["chapter_number", "prose", "chapter_title"]
        assert "3" in runs[0][0]
        assert runs[-1][0] == "The Flood"

    def test_a_chapter_whose_number_is_unknown_shows_as_typed(self) -> None:
        assert shown("/chapter The Flood") == [("/chapter The Flood", "prose")]


class TestPromptToWire:
    def test_prose_goes_out_as_it_is(self) -> None:
        assert wire("She looks up.", None, is_last=True) == "She looks up."

    def test_a_lines_leading_whitespace_is_its_seam_and_rides_the_wire(self) -> None:
        # typed so to go on with the text before it (`assembler.join_of`)
        assert wire(" and the door", None, is_last=True) == " and the door"
        assert wire("\n\nAnd the door", None, is_last=True) == "\n\nAnd the door"

    def test_a_command_prefix_never_reaches_the_model(self) -> None:
        got = wire("/me Elara: I step in.", ME, is_last=True)
        assert got == "<<Elara speaks>>\nI step in."

    def test_a_bodyless_command_sends_its_framing_alone(self) -> None:
        assert wire("/you Elara", YOU, is_last=True) == "<<now play Elara>>"

    def test_ooc_slots_its_prose_into_the_template(self) -> None:
        got = wire("/ooc What genre?", OOC, is_last=True)
        assert got == "<<aside: What genre?>>"

    def test_a_chapter_slots_its_title_into_the_template(self) -> None:
        got = wire("/chapter The Flood", '<<chapter "{body}">>', is_last=True)
        assert got == '<<chapter "The Flood">>'

    def test_the_name_comes_from_the_line_not_the_cast(self) -> None:
        # No lookup anywhere: the spelling typed is the spelling sent, so a
        # turn's wire text never shifts when the cast changes.
        got = wire("/me elara: I bow.", ME, is_last=True)
        assert got == "<<elara speaks>>\nI bow."

    def test_an_edited_away_command_drops_its_template(self) -> None:
        # The browser edit can strip a row's leading command; the framing
        # goes with it — never a literal `{name}` on the wire. A v1
        # framing, its name baked in and no placeholder, still joins.
        assert wire("Just prose now.", ME, is_last=True) == "Just prose now."
        v1 = "((OOC: The user writes as Elara.))\n{body}"
        got = wire("Edited prose.", v1, is_last=True)
        assert got == "((OOC: The user writes as Elara.))\nEdited prose."

    def test_an_inliner_becomes_its_own_enclosure(self) -> None:
        got = wire("She looks up /ooc make her nervous", None, is_last=True)
        assert got == "She looks up ((OOC: make her nervous))"

    def test_a_command_and_an_inliner_compose_together(self) -> None:
        got = wire("/me Elara: I step in /ooc nervous", ME, is_last=True)
        assert got == "<<Elara speaks>>\nI step in ((OOC: nervous))"

    def test_a_cue_on_a_me_line_is_spent_like_any_cue(self) -> None:
        kept = wire("/me Elara: I bow /cue whisper", ME, is_last=True)
        assert kept == "<<Elara speaks>>\nI bow ((OOC: whisper))"
        later = wire("/me Elara: I bow /cue whisper", ME, is_last=False)
        assert later == "<<Elara speaks>>\nI bow"

    def test_a_cue_on_an_ooc_line_rides_beside_the_aside(self) -> None:
        got = wire("/ooc what now /cue short", OOC, is_last=True)
        assert got == "<<aside: what now>> ((OOC: short))"

    def test_a_spent_cue_leaves_no_trace_on_a_bodyless_line(self) -> None:
        # Empty parts drop: no dangling space where the cue was.
        got = wire("/you Elara /cue whisper", "<<play {name}. {body}>>", is_last=False)
        assert got == "<<play Elara.>>"

    def test_the_first_inliner_owns_the_rest_of_the_line(self) -> None:
        # The line is split at its FIRST inliner; a second spelling after
        # it is text inside the first one's enclosure.
        got = wire("She waits /ooc slow down /cue whisper", None, is_last=False)
        assert got == "She waits ((OOC: slow down /cue whisper))"

    def test_a_newline_bounds_an_inliner_like_a_space(self) -> None:
        # Inside a \"\"\" block an inliner may open its own line.
        text = "She waits.\n/cue whisper"
        assert wire(text, None, is_last=True) == "She waits. ((OOC: whisper))"
        assert wire(text, None, is_last=False) == "She waits."

    def test_a_me_line_keeps_its_interior_newlines(self) -> None:
        # A \"\"\" block is an ordinary prompt: the layout inside is prose.
        got = wire("/me Elara: I bow.\nThe hall is cold.", ME, is_last=True)
        assert got == "<<Elara speaks>>\nI bow.\nThe hall is cold."

    def test_an_unknown_command_goes_out_verbatim(self) -> None:
        assert wire("/shrug at it", None, is_last=True) == "/shrug at it"

    def test_the_writers_own_braces_are_never_placeholders(self) -> None:
        # {name} fills into the TEMPLATE before the text joins it, and the
        # text is never scanned again — typed braces survive literally.
        got = wire("/me Elara: I read {name} aloud.", ME, is_last=True)
        assert got == "<<Elara speaks>>\nI read {name} aloud."
        got = wire("/me Elara: literally {body} here", ME, is_last=True)
        assert got == "<<Elara speaks>>\nliterally {body} here"

    def test_a_you_hint_slots_into_the_templates_body(self) -> None:
        got = wire("/you Elara: be cold", "<<play {name}. {body}>>", is_last=False)
        assert got == "<<play Elara. be cold>>"

    def test_a_bare_you_takes_the_empty_slot_and_its_space_out(self) -> None:
        # So a template ending "… {body}))" sends, bare, the exact wire it
        # sent before the slot existed.
        got = wire("/you Elara", "<<play {name}. {body}>>", is_last=False)
        assert got == "<<play Elara.>>"

    def test_a_you_hint_without_a_slot_becomes_its_own_aside(self) -> None:
        # A template from before the slot existed still carries the hint —
        # enclosed, never joined bare: direction is never story.
        got = wire("/you Elara: be cold to the guest", YOU, is_last=True)
        assert got == "<<now play Elara>> ((OOC: be cold to the guest))"

    def test_a_you_hint_is_never_spent(self) -> None:
        # The hint is the standing form — /cue is the one-shot one, and
        # both may ride the same line.
        got = wire("/you Elara: be cold /cue whisper", YOU, is_last=False)
        assert got == "<<now play Elara>> ((OOC: be cold))"

    def test_a_you_hint_and_an_inline_aside_share_a_line(self) -> None:
        got = wire(
            "/you Elara: be cold /ooc keep it brief", "<<play {name}. {body}>>", is_last=False
        )
        assert got == "<<play Elara. be cold>> ((OOC: keep it brief))"

    # A cue is spent; an ooc aside is not.
    def test_a_cue_goes_out_while_its_turn_is_newest(self) -> None:
        got = wire("She looks up /cue keep it tense", None, is_last=True)
        assert got == "She looks up ((OOC: keep it tense))"

    def test_a_cue_is_gone_from_every_later_turn(self) -> None:
        got = wire("She looks up /cue keep it tense", None, is_last=False)
        assert got == "She looks up"

    def test_an_ooc_aside_is_never_spent(self) -> None:
        got = wire("She looks up /ooc nervous", None, is_last=False)
        assert got == "She looks up ((OOC: nervous))"

    # v1: turns written before the syntax existed must compose unchanged.
    def test_a_legacy_framed_turn_slots_its_bare_body(self) -> None:
        # Stored back then: the prose alone, the name already in the
        # framing. Nothing here may strip or re-fill anything.
        assert wire("I step in.", "<<Elara speaks>>\n{body}", is_last=True) == (
            "<<Elara speaks>>\nI step in."
        )

    def test_a_legacy_bodyless_turn_sends_its_read(self) -> None:
        assert wire("", "<<now play Elara>>", is_last=True) == "<<now play Elara>>"

    def test_a_legacy_plain_turn_is_untouched(self) -> None:
        assert wire("I step in.", None, is_last=True) == "I step in."

    def test_a_legacy_body_is_never_read_as_syntax(self) -> None:
        # Its prefix was stripped before storing, so a slash inside it is
        # prose — and a framing with no placeholder leads its body.
        assert wire("and/or it", "<<note>>", is_last=True) == "<<note>>\n\nand/or it"

    def test_a_legacy_body_keeps_an_inliner_spelling_literally(self) -> None:
        # The legacy branch composes before any syntax is read: text that
        # LOOKS like an inliner is prose there, and never spent.
        got = wire("I bow /cue whisper", "<<note>>\n{body}", is_last=False)
        assert got == "<<note>>\nI bow /cue whisper"

    # Prose keeps its slashes.
    def test_a_slash_inside_a_word_is_prose(self) -> None:
        got = wire("She looks up and/or down", None, is_last=True)
        assert got == "She looks up and/or down"

    def test_a_url_spelling_an_inliner_is_prose(self) -> None:
        got = wire("Read https://x.co/ooc now", None, is_last=True)
        assert got == "Read https://x.co/ooc now"


class TestDice:
    def test_a_die_and_a_modifier_read_like_the_table(self) -> None:
        assert roll_dice("1d20+5", _dice(14)) == (19, "14 + 5")

    def test_several_dice_show_each_roll(self) -> None:
        assert roll_dice("3d6", _dice(4, 5, 2)) == (11, "[4, 5, 2]")

    def test_keep_highest_is_advantage(self) -> None:
        assert roll_dice("2d20kh1+5", _dice(18, 11)) == (23, "[18, 11 → 18] + 5")

    def test_keep_lowest_is_disadvantage(self) -> None:
        assert roll_dice("2d20kl1", _dice(18, 11)) == (11, "[18, 11 → 11]")

    def test_a_modifier_subtracts_too(self) -> None:
        assert roll_dice("1d20-2", _dice(10)) == (8, "10 - 2")

    def test_a_bare_d_rolls_one_die(self) -> None:
        assert roll_dice("d20", _dice(7)) == (7, "7")

    def test_dice_terms_chain(self) -> None:
        assert roll_dice("1d20+2d4-1", _dice(9, 3, 1)) == (12, "9 + [3, 1] - 1")

    def test_case_does_not_matter(self) -> None:
        assert roll_dice("2D20KH1", _dice(3, 15)) == (15, "[3, 15 → 15]")

    def test_the_dice_ignore_any_seed(self) -> None:
        # The promise is the OS's entropy, unseedable: dice that could
        # be seeded could be loaded. Seeding Python's global generator
        # must therefore change nothing — two rolls under identical
        # seeds still disagree (ten thousand-sided dice cannot repeat
        # by chance in any universe worth testing in).
        random.seed(11)
        first = roll_dice("10d1000")
        random.seed(11)
        second = roll_dice("10d1000")
        assert first is not None and second is not None
        assert first[1] != second[1]

    def test_what_is_not_dice_is_none(self) -> None:
        for spec in (
            "",
            "banana",
            "1d",
            "d",
            "5",  # a flat number rolls nothing, so it is not a roll
            "0d6",
            "1d1",  # a one-sided die is not a die
            "101d6",
            "1d1001",
            "2d20kh2",  # keeping everything rolled keeps nothing at all
            "2d20kh0",
            "1d20++5",
            "+1d20",
            "1d20+",
            "1d6 2d4",
        ):
            assert roll_dice(spec, _dice(1, 1, 1)) is None, spec

    def test_the_spec_never_reaches_the_wire_from_the_body(self) -> None:
        # The roll rides the frozen template; the body contributes only
        # the action, or nothing — sending the spec again would invite
        # the model to roll it.
        frozen = "<<rolled 1d20+5 = 19 (14 + 5)>> {body}"
        got = wire("/roll 1d20+5 I search the alcove", frozen, is_last=True)
        assert got == "<<rolled 1d20+5 = 19 (14 + 5)>> I search the alcove"
        assert wire("/roll 1d20+5", "<<r>> {body}", is_last=True) == "<<r>>"

    def test_an_inliner_still_closes_a_roll_line(self) -> None:
        got = wire("/roll 1d6 I go /ooc really?", "<<r>> {body}", is_last=True)
        assert got == "<<r>> I go ((OOC: really?))"

    def test_freeze_bakes_the_roll_into_the_template(self) -> None:
        frame = read("/roll 2d6+1 I strike.")
        assert frame.note == ""  # nothing rolled until the record freezes it
        frozen = frame.freeze_template("[{dice}] {body}")
        assert frozen is not None and frozen.endswith("] {body}")
        rolled = frozen[1 : -len("] {body}")]
        total = int(rolled.split(" = ", 1)[1].split(" ", 1)[0])
        assert rolled.startswith("2d6+1 = ")
        assert 3 <= total <= 13
        assert frame.note == f"dice: {rolled}"

    def test_every_other_line_freezes_verbatim_and_notes_nothing(self) -> None:
        frame = read("/me Elara: I step in.")
        assert frame.freeze_template(ME) == ME
        assert frame.note == ""


def _dice(*values: int) -> random.Random:
    """Dice that fall exactly as scripted — the grammar under test, not
    the entropy."""

    class Scripted(random.Random):
        def __init__(self) -> None:
            super().__init__()
            self.left = list(values)

        def randint(self, a: int, b: int) -> int:
            return self.left.pop(0)

    return Scripted()


def _kinds(line: str) -> tuple[str, str]:
    """`(request_kind, reply_kind)` — read together, since the point is
    where they differ."""
    return read(line).request_kind, read(line).reply_kind
