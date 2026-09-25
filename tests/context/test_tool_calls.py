"""Tool calls in a reply: the fenced blocks marked `otk-NAME` that are
the model using a tool and are not story.

The `otk-` namespace is the whole test of what a call is: no list of
tools is consulted, so a call stays one in a build that never heard of
its tool. `parse_reply`'s contract: the body as `Prose` and `ToolCall`
segments in order; a call's `name` is the NAME, lowercase, its inside
the lines between its fences kept as written, no trailing blank line,
running to the end when never closed; a fence is a fence only at the
start of its line, and its line goes with the call, newline included,
as do the blank lines before it; calls never nest — an opening fence
inside a call ends it and opens its own, a bare fence line ends the
open call and is prose when none is, any other line is prose outside a
call and the call's own inside one; plain fences are never tracked.
`strip`'s: the body without its calls, fences and all, a newline where
a call stood between two runs of prose, nothing else changed.
`to_wire`'s: the body under a `ToolSet` — a call of a tool that is on
as its canonical self, one of a tool that is off as its rule's text,
any other gone with the blank line beside it. `ReplyParser`'s: the same
parse over a stream — whatever the chunking, the pieces joined are
`parse_reply`'s segments; a line's start that may yet become a fence is
held back until its newline decides it, and so is a line's end until
the next line says whose it is; a call of a tool in `ending` ends the
text, and `cut` says where.
"""

from typing import ClassVar

import pytest

from otaku.context.tool_calls import (
    FENCE,
    Cut,
    Prose,
    ReplyParser,
    ToolCall,
    ToolSet,
    parse_reply,
    strip,
    to_wire,
)

QUESTION = "```otk-question\nGo in?\n1. Yes\n2. No\n```"


class TestParseReply:
    """The grammar, over a whole body."""

    def test_text_without_calls_is_one_prose_segment(self) -> None:
        assert parse_reply("She nods. The door opens.") == [Prose("She nods. The door opens.")]

    def test_an_empty_body_has_no_segments(self) -> None:
        assert parse_reply("") == []

    def test_a_body_of_blank_lines_alone_has_no_segments(self) -> None:
        assert parse_reply("\n\n") == []

    def test_a_call_is_a_fenced_block_marked_with_the_namespace(self) -> None:
        body = "She nods.\n\n```otk-note\nplan\n```\nThe door opens."
        assert parse_reply(body) == [
            Prose("She nods."),
            ToolCall("note", "plan", closed=True),
            Prose("The door opens."),
        ]

    def test_the_inside_is_the_lines_between_as_written(self) -> None:
        # A leading blank line, trailing spaces, a blank line within: kept.
        body = "```otk-question\n\nStay or go?\n\n1. Stay  \n```"
        assert parse_reply(body) == [
            ToolCall("question", "\nStay or go?\n\n1. Stay  ", closed=True)
        ]

    def test_the_insides_trailing_blank_lines_go_with_the_closing_fence(self) -> None:
        assert parse_reply("```otk-note\nx\n\n\n```") == [ToolCall("note", "x", closed=True)]

    def test_an_inside_of_blank_lines_alone_is_empty(self) -> None:
        assert parse_reply("```otk-note\n\n\n```") == [ToolCall("note", "", closed=True)]

    def test_a_whitespace_line_is_content_not_a_blank_line(self) -> None:
        assert parse_reply("```otk-note\nx\n  \n```") == [ToolCall("note", "x\n  ", closed=True)]

    def test_the_blank_lines_before_an_opening_fence_go_with_the_call(self) -> None:
        # So a call left out leaves no hole — and the prose is whole.
        assert parse_reply("She nods.\n\n\n```otk-note\nx\n```") == [
            Prose("She nods."),
            ToolCall("note", "x", closed=True),
        ]

    def test_the_proses_own_blank_lines_stay(self) -> None:
        assert parse_reply("She nods.\n\nThe door opens.\n") == [
            Prose("She nods.\n\nThe door opens.")
        ]

    def test_the_fence_lines_go_with_the_call_newlines_included(self) -> None:
        # The closing fence's newline is the call's: the prose after
        # begins on the next line, its own blank lines kept.
        assert parse_reply("```otk-note\nx\n```\n\nAfter.") == [
            ToolCall("note", "x", closed=True),
            Prose("\nAfter."),
        ]
        assert parse_reply("```otk-note\nx\n```\nAfter.") == [
            ToolCall("note", "x", closed=True),
            Prose("After."),
        ]

    def test_a_call_may_open_the_body(self) -> None:
        assert parse_reply("```otk-note\nx\n```\nShe nods.") == [
            ToolCall("note", "x", closed=True),
            Prose("She nods."),
        ]

    def test_a_call_may_close_the_body(self) -> None:
        assert parse_reply("She nods.\n" + QUESTION) == [
            Prose("She nods."),
            ToolCall("question", "Go in?\n1. Yes\n2. No", closed=True),
        ]

    def test_a_name_matches_in_any_case_and_reads_lowercase(self) -> None:
        assert parse_reply("```OTK-Note\nx\n```") == [ToolCall("note", "x", closed=True)]
        assert parse_reply("```Otk-QUESTION\nx\n```") == [ToolCall("question", "x", closed=True)]

    def test_a_name_may_be_thirty_two_letters_and_no_more(self) -> None:
        assert parse_reply("```otk-" + "a" * 32 + "\nx\n```") == [
            ToolCall("a" * 32, "x", closed=True)
        ]
        body = "```otk-" + "a" * 33 + "\nx\n```"
        assert parse_reply(body) == [Prose(body)]

    def test_a_fence_may_be_longer_than_three_backticks(self) -> None:
        assert parse_reply("````otk-note\nx\n`````") == [ToolCall("note", "x", closed=True)]

    def test_a_fence_may_be_indented_three_spaces_at_most(self) -> None:
        assert parse_reply("   ```otk-note\nx\n   ```") == [ToolCall("note", "x", closed=True)]
        body = "    ```otk-note\nx\n```"  # four: an indented code block to markdown, prose here
        assert parse_reply(body) == [Prose(body)]

    def test_spaces_and_tabs_may_stand_around_the_name(self) -> None:
        assert parse_reply("```  otk-note \t\nx\n```  ") == [ToolCall("note", "x", closed=True)]
        assert parse_reply("```\totk-note\nx\n```") == [ToolCall("note", "x", closed=True)]

    def test_a_fence_whose_info_string_is_not_the_name_alone_is_prose(self) -> None:
        for body in (
            "```otk-\nx\n```",
            "```otk-a1\nx\n```",
            "```otk-note x\nx\n```",
            "```otk note\nx\n```",
            "```otk_note\nx\n```",
            "```otknote\nx\n```",
            "``otk-note\nx\n``",
        ):
            assert parse_reply(body) == [Prose(body)], body

    def test_a_tilde_fence_is_prose(self) -> None:
        body = "~~~otk-note\nx\n~~~"
        assert parse_reply(body) == [Prose(body)]

    def test_a_lookalike_letter_spells_no_fence(self) -> None:
        # U+017F, the long s, case-folds to "s" — and must not match it.
        body = "```otk-aſk\nStay?\n```"
        assert parse_reply(body) == [Prose(body)]

    def test_a_fence_is_one_only_at_the_start_of_its_line(self) -> None:
        for body in ("She said ```otk-note\nx\n```", "x ```\ny"):
            assert parse_reply(body) == [Prose(body)], body
        # inside a call too: backticks mid-line are the inside's
        assert parse_reply("```otk-note\nx ```\n```") == [ToolCall("note", "x ```", closed=True)]

    def test_an_unclosed_call_runs_to_the_end(self) -> None:
        # A reply cut mid-call (Ctrl+C keeps what arrived).
        assert parse_reply("She nods.\n```otk-note\nthe letter is") == [
            Prose("She nods."),
            ToolCall("note", "the letter is", closed=False),
        ]

    def test_an_unclosed_calls_trailing_newlines_are_not_its_inside(self) -> None:
        assert parse_reply("```otk-note\nx\n\n") == [ToolCall("note", "x", closed=False)]

    def test_a_call_opened_at_the_very_end_is_a_segment(self) -> None:
        # A stream cut right after the opening fence: the call exists.
        for body in ("She nods.\n```otk-note", "She nods.\n```otk-note\n"):
            assert parse_reply(body) == [Prose("She nods."), ToolCall("note", "", closed=False)]

    def test_a_call_of_a_tool_nobody_knows_is_a_call_all_the_same(self) -> None:
        # The fences outlive the build that wrote them: a retired tool's
        # call, or a newer build's, must never turn back into story.
        assert parse_reply("```otk-plan\nx\n```") == [ToolCall("plan", "x", closed=True)]

    def test_an_opening_fence_inside_a_call_ends_it_and_opens_its_own(self) -> None:
        assert parse_reply("```otk-note\na\n```otk-question\nb\n```") == [
            ToolCall("note", "a", closed=True),
            ToolCall("question", "b", closed=True),
        ]

    def test_an_opening_fence_right_after_another_ends_an_empty_call(self) -> None:
        assert parse_reply("```otk-note\n```otk-question\nb\n```") == [
            ToolCall("note", "", closed=True),
            ToolCall("question", "b", closed=True),
        ]

    def test_any_bare_fence_line_closes_the_open_call_whatever_its_length(self) -> None:
        assert parse_reply("````otk-note\nx\n```") == [ToolCall("note", "x", closed=True)]
        assert parse_reply("```otk-note\nx\n``````") == [ToolCall("note", "x", closed=True)]

    def test_a_bare_fence_with_no_call_open_is_prose(self) -> None:
        for body in ("```\ncode\n```\nShe nods.", "```", "She nods.\n```"):
            assert parse_reply(body) == [Prose(body)], body

    def test_a_plain_fence_the_model_forgot_to_close_changes_nothing(self) -> None:
        # Plain fences are never tracked: the next opening fence opens its
        # call whatever a markdown viewer would make of the letter.
        letter = "She reads:\n\n```\nMeet me at the mill.\n\nShe folds it."
        body = letter + "\n\n```otk-question\nGo?\n1. Yes\n```"
        assert parse_reply(body) == [
            Prose(letter),
            ToolCall("question", "Go?\n1. Yes", closed=True),
        ]

    def test_a_fence_with_another_info_string_inside_a_call_is_its_own(self) -> None:
        # As a markdown viewer reads it: a closing fence carries no info
        # string, so the block goes on to the bare one.
        assert parse_reply("```otk-note\nsee:\n```py\nx = 1\n```") == [
            ToolCall("note", "see:\n```py\nx = 1", closed=True)
        ]

    def test_a_fence_with_another_info_string_outside_a_call_is_prose(self) -> None:
        body = "```python\nx = 1\n```\nShe nods."
        assert parse_reply(body) == [Prose(body)]

    def test_markup_outside_the_namespace_is_prose(self) -> None:
        body = "<notes>x</notes>, <b>bold</b>, <otk-note>x</otk-note>, <Alice> hello, a < b"
        assert parse_reply(body) == [Prose(body)]

    def test_an_empty_call_is_a_segment(self) -> None:
        assert parse_reply("```otk-question\n```") == [ToolCall("question", "", closed=True)]

    def test_adjacent_calls_stay_apart(self) -> None:
        assert parse_reply("```otk-note\na\n```\n```otk-note\nb\n```") == [
            ToolCall("note", "a", closed=True),
            ToolCall("note", "b", closed=True),
        ]

    def test_calls_and_prose_alternate_in_order(self) -> None:
        body = "One.\n```otk-note\na\n```\nTwo.\n\n```otk-question\nb\n```\n\nThree."
        assert parse_reply(body) == [
            Prose("One."),
            ToolCall("note", "a", closed=True),
            Prose("Two."),
            ToolCall("question", "b", closed=True),
            Prose("\nThree."),
        ]

    def test_a_carriage_return_is_content(self) -> None:
        # Lines end at a newline alone: the model's "\r" stays where it was.
        body = "```otk-note\r\nx\r\n```"
        assert parse_reply(body) == [Prose(body)]


class TestStrip:
    def test_text_without_calls_is_unchanged(self) -> None:
        assert strip("She nods.\n\nThe door opens.") == "She nods.\n\nThe door opens."

    def test_a_call_leaves_with_its_fences_a_newline_where_it_stood(self) -> None:
        assert strip("A\n```otk-note\nplan\n```\nB") == "A\nB"
        assert strip("A\n\n```otk-note\nplan\n```\n\nB") == "A\n\nB"

    def test_a_call_at_either_end_leaves_no_newline_behind(self) -> None:
        assert strip("```otk-note\nx\n```\nShe nods.") == "She nods."
        assert strip("She nods.\n" + QUESTION) == "She nods."

    def test_every_call_leaves(self) -> None:
        body = "One.\n```otk-note\nfirst\nsecond\n```\nTwo.\n```otk-question\nStay?\n1. Stay\n```"
        assert strip(body) == "One.\nTwo."

    def test_adjacent_calls_leave_one_newline_between_the_prose(self) -> None:
        assert strip("A\n```otk-note\nx\n```\n```otk-note\ny\n```\nB") == "A\nB"

    def test_an_unclosed_call_takes_the_rest(self) -> None:
        assert strip("She nods.\n```otk-note\nthe letter is") == "She nods."

    def test_a_text_that_is_only_a_call_is_empty(self) -> None:
        assert strip("```otk-note\nall of it\n```") == ""

    def test_markup_outside_the_namespace_stays(self) -> None:
        body = "She <i>never</i> agreed.\n```\ncode\n```\n<Alice> hello"
        assert strip(body) == body


class TestToWire:
    ON = ToolSet(on=frozenset({"note"}))
    OFF = ToolSet(off={"question": lambda inside: inside.strip().splitlines()[0]})

    def test_text_without_calls_is_unchanged(self) -> None:
        assert to_wire("She nods.", self.ON) == "She nods."

    def test_a_call_of_a_tool_that_is_on_goes_as_written(self) -> None:
        body = "She nods.\n\n```otk-note\nplan\n```\n\nMore."
        assert to_wire(body, self.ON) == body

    def test_a_call_that_is_on_goes_in_its_canonical_form(self) -> None:
        # The canonical fences, lowercase, closed — whatever the text
        # spelled or left open — so the history teaches closed calls.
        assert to_wire("  ```` OTK-Note  \nplan", self.ON) == "```otk-note\nplan\n```"
        assert to_wire("```otk-note\nplan\n\n\n````", self.ON) == "```otk-note\nplan\n```"
        assert to_wire("```otk-note\n```", self.ON) == "```otk-note\n```"
        assert to_wire("```otk-note", self.ON) == "```otk-note\n```"

    def test_the_canonical_inside_is_the_lines_between_as_written(self) -> None:
        body = "```otk-note\n\nfirst  \n\nsecond\n```"
        assert to_wire(body, self.ON) == body

    def test_a_call_of_a_tool_that_is_off_becomes_its_rule_text(self) -> None:
        body = "She nods.\n\n```otk-question\nStay or go?\n1. Stay\n2. Go\n```\nYes."
        assert to_wire(body, self.OFF) == "She nods.\n\nStay or go?\nYes."

    def test_a_call_ended_by_an_opening_fence_keeps_the_line_between(self) -> None:
        tools = ToolSet(on=frozenset({"note"}), off=self.OFF.off)
        body = "```otk-question\nStay?\n1. Stay\n```otk-note\nplan\n```"
        assert to_wire(body, tools) == "Stay?\n```otk-note\nplan\n```"
        tools = ToolSet(on=frozenset({"note", "question"}))
        assert (
            to_wire(body, tools) == "```otk-question\nStay?\n1. Stay\n```\n```otk-note\nplan\n```"
        )

    def test_a_call_of_any_other_tool_leaves_the_wire(self) -> None:
        # A white list: a decommissioned tool's calls never ride.
        assert to_wire("A\n```otk-plan\nx\n```\nB", self.ON) == "A\nB"

    def test_a_call_that_leaves_takes_the_blank_lines_after_it(self) -> None:
        assert to_wire("```otk-plan\nx\n```\n\nShe nods.", self.ON) == "She nods."
        assert to_wire("A\n\n```otk-plan\nx\n```\n\nB", self.ON) == "A\n\nB"

    def test_a_call_that_leaves_at_the_end_takes_the_blank_lines_before_it(self) -> None:
        assert to_wire("She nods.\n\n```otk-plan\nx\n```\n", self.ON) == "She nods."
        assert to_wire("She nods.\n\n```otk-plan\nx", self.ON) == "She nods."

    def test_a_body_that_is_only_a_call_that_leaves_is_empty(self) -> None:
        assert to_wire("```otk-plan\nx\n```", self.ON) == ""

    def test_a_rule_that_makes_nothing_removes_the_call(self) -> None:
        tools = ToolSet(off={"note": lambda inside: ""})
        assert to_wire("She nods.\n\n```otk-note\nplan\n```", tools) == "She nods."

    def test_a_rule_reads_the_inside_without_its_trailing_blank_lines(self) -> None:
        seen: list[str] = []
        tools = ToolSet(off={"note": lambda inside: seen.append(inside) or "x"})
        to_wire("```otk-note\n\nplan  \n\n\n```", tools)
        assert seen == ["\nplan  "]

    def test_every_call_is_treated_by_its_own_tool(self) -> None:
        tools = ToolSet(on=frozenset({"note"}), off=self.OFF.off)
        body = "A\n```otk-question\nQ?\n1. x\n```\nB\n```otk-plan\np\n```\nC\n```otk-note\nn\n```"
        assert to_wire(body, tools) == "A\nQ?\nB\nC\n```otk-note\nn\n```"

    def test_a_plain_fence_is_untouched(self) -> None:
        body = "```\ncode\n```\nShe nods.\n```python\nx\n```"
        assert to_wire(body, self.ON) == body

    def test_markup_outside_the_namespace_is_untouched(self) -> None:
        body = "<otk-note>x</otk-note> and <b>bold</b>"
        assert to_wire(body, self.ON) == body


class TestReplyParser:
    """The same grammar over a stream."""

    BODIES: ClassVar[list[str]] = [
        "She nods. The door opens.",
        "```otk-note\nplan\n```\nShe nods.\n```otk-question\nStay?\n1. Stay\n2. Go\n```",
        "a < b, ```python\nx\n```, ``` \n and ```otk-\n astray\n```",
        "```OTK-NOTE\nx\n```\n```otk-note\ny\n```",
        "```otk-question\n```",
        "```otk-note\na\n```otk-question\nb\n```",
        "```otk-note\n```otk-question\nb\n```",
        "```otk-note\n\na\n\n```py\nb\n\n```\n\nc",
        "She nods.\n```otk-note\nthe letter is",
        "```otk-note\nx\n\n",
        "  ```` otk-note  \nx\n   `````  \n",
        "One.\n\n\n```otk-note\na\n```\nTwo.\n\n```otk-question\nb\n```\n\nThree.",
        "ends on a maybe\n```otk-qu",
        "ends on a maybe\n``",
        "ends on a maybe\n   ",
        "```otk-note",
        "```otk-note\n",
        "\n\n```otk-note\nx\n```\n\n",
        "x ```\ny\n```otk-note\nz ```\n```",
    ]

    @pytest.mark.parametrize("body", BODIES)
    def test_any_chunking_gives_the_segments_parse_reply_gives(self, body: str) -> None:
        cuts = [(i, j) for i in range(len(body) + 1) for j in range(i, len(body) + 1)]
        for i, j in cuts:
            assert streamed([body[:i], body[i:j], body[j:]]) == parse_reply(body), (i, j)
        assert streamed(list(body)) == parse_reply(body)

    def test_prose_streams_at_once_past_a_line_start(self) -> None:
        # Backticks mid-line are prose at once: a fence is one only at a
        # line's start.
        assert ReplyParser().feed("Hello ``") == [Prose("Hello ``")]
        assert ReplyParser().feed("He") == [Prose("He")]

    def test_a_line_start_that_may_be_a_fence_is_held(self) -> None:
        for start in (
            "`",
            "``",
            "```",
            "``` ",
            "```o",
            "```ot",
            "```otk",
            "```otk-",
            "```otk-qu",
            " ",
            "   ",
            "   ``",
        ):
            assert ReplyParser().feed(start) == [], start

    def test_a_held_line_start_is_let_go_once_it_cannot_be_a_fence(self) -> None:
        parser = ReplyParser()
        assert parser.feed("``") == []
        assert parser.feed("` python\nx") == [Prose("``` python\nx")]
        parser = ReplyParser()
        assert parser.feed("```otk-note ") == []
        assert parser.feed("x") == [Prose("```otk-note x")]
        assert ReplyParser().feed("    ") == [Prose("    ")]  # four spaces: no fence

    def test_a_name_that_never_ends_is_not_held_forever(self) -> None:
        # No tool's name is this long: the text is prose, and is let go.
        pieces = ReplyParser().feed("```otk-" + "a" * 200)
        assert pieces == [Prose("```otk-" + "a" * 200)]

    def test_a_held_line_start_opens_its_call_at_its_newline(self) -> None:
        parser = ReplyParser()
        assert parser.feed("```otk-no") == []
        assert parser.feed("te") == []
        assert parser.feed("\n") == [ToolCall("note", "", closed=False)]

    def test_flush_hands_over_what_was_held(self) -> None:
        parser = ReplyParser()
        parser.feed("a\n``")
        assert parser.flush() == [Prose("\n``")]
        assert parser.flush() == []

    def test_a_held_line_the_end_completes_into_an_opener_opens_its_call(self) -> None:
        parser = ReplyParser()
        parser.feed("a\n```otk-qu")
        assert parser.flush() == [ToolCall("qu", "", closed=False)]

    def test_a_held_line_the_end_completes_into_a_bare_fence_closes_the_call(self) -> None:
        parser = ReplyParser()
        parser.feed("```otk-note\nx\n```")
        assert parser.flush() == [ToolCall("note", "", closed=True)]

    def test_a_lines_end_waits_for_the_next_line(self) -> None:
        # The newlines before a fence are the fence's, so a line's end is
        # held until the next line says whose it is — in prose and inside
        # a call alike.
        parser = ReplyParser()
        assert parser.feed("Hello\n\n") == [Prose("Hello")]
        assert parser.feed("more\n") == [Prose("\n\nmore")]
        assert parser.feed("```otk-note\n") == [ToolCall("note", "", closed=False)]
        assert parser.feed("a\n") == [ToolCall("note", "a", closed=False)]
        assert parser.feed("b\n\n") == [ToolCall("note", "\nb", closed=False)]
        assert parser.feed("```\n") == [ToolCall("note", "", closed=True)]
        assert parser.feed("\nAfter") == [Prose("\nAfter")]

    def test_an_opened_call_announces_itself_with_an_empty_piece(self) -> None:
        assert ReplyParser().feed("```otk-note\n") == [ToolCall("note", "", closed=False)]

    def test_a_calls_pieces_join_up_to_the_one_the_closing_fence_ends(self) -> None:
        parser = ReplyParser()
        assert parser.feed("```otk-question\nStay") == [ToolCall("question", "Stay", closed=False)]
        assert parser.feed("?\n```\n") == [ToolCall("question", "?", closed=True)]

    def test_the_pieces_of_one_feed_are_joined(self) -> None:
        assert ReplyParser().feed("a\nb\n```otk-note\nx\ny\n```\nc\nd") == [
            Prose("a\nb"),
            ToolCall("note", "x\ny", closed=True),
            Prose("c\nd"),
        ]


class TestCut:
    """A call of a tool in `ending` ends the text."""

    ASK = "```otk-question\nGo in?\n1. Yes\n```"

    def test_without_ending_tools_nothing_cuts(self) -> None:
        parser = ReplyParser()
        parser.feed(self.ASK + "\n\nShe goes.")
        parser.flush()
        assert parser.cut is None

    def test_a_call_of_another_tool_does_not_cut(self) -> None:
        parser = ReplyParser(ending=frozenset({"question"}))
        assert parser.feed("```otk-note\nx\n```\nShe goes.") == [
            ToolCall("note", "x", closed=True),
            Prose("She goes."),
        ]
        assert parser.cut is None

    def test_the_cut_runs_through_the_closing_fence(self) -> None:
        parser = ReplyParser(ending=frozenset({"question"}))
        pieces = parser.feed("Creak.\n\n" + self.ASK + "\n\nShe goes in anyway.")
        assert pieces == [Prose("Creak."), ToolCall("question", "Go in?\n1. Yes", closed=True)]
        assert parser.cut == Cut(at=len("Creak.\n\n" + self.ASK), fenced=True)

    def test_nothing_after_the_cut_is_read(self) -> None:
        parser = ReplyParser(ending=frozenset({"question"}))
        parser.feed(self.ASK + "\n")
        assert parser.feed("She goes.\n```otk-note\nx\n```") == []
        assert parser.flush() == []
        assert parser.cut == Cut(at=len(self.ASK), fenced=True)

    def test_the_cut_at_an_opening_fence_ends_the_calls_last_line(self) -> None:
        # The model opened another block instead of closing this one:
        # the fence is the recorder's to add.
        parser = ReplyParser(ending=frozenset({"question"}))
        pieces = parser.feed("```otk-question\nGo in?\n1. Yes\n```otk-note\nlater\n```")
        assert pieces == [ToolCall("question", "Go in?\n1. Yes", closed=True)]
        assert parser.cut == Cut(at=len("```otk-question\nGo in?\n1. Yes"), fenced=False)

    def test_the_cut_at_an_opening_fence_leaves_the_blank_lines_before_it(self) -> None:
        parser = ReplyParser(ending=frozenset({"question"}))
        parser.feed("```otk-question\nGo in?\n\n\n```otk-note\nx\n```")
        assert parser.cut == Cut(at=len("```otk-question\nGo in?"), fenced=False)

    def test_the_cut_of_an_empty_call(self) -> None:
        parser = ReplyParser(ending=frozenset({"question"}))
        parser.feed("```otk-question\n```\nmore")
        assert parser.cut == Cut(at=len("```otk-question\n```"), fenced=True)
        parser = ReplyParser(ending=frozenset({"question"}))
        parser.feed("```otk-question\n```otk-note\nx\n```")
        assert parser.cut == Cut(at=len("```otk-question"), fenced=False)

    def test_the_cut_counts_across_chunks(self) -> None:
        parser = ReplyParser(ending=frozenset({"question"}))
        parser.feed("Creak.\n```otk-question\nGo?\n``")
        assert parser.cut is None
        parser.feed("`")
        assert parser.cut is None  # the line may yet open a block
        parser.feed("\nmore")
        assert parser.cut == Cut(at=len("Creak.\n```otk-question\nGo?\n```"), fenced=True)

    def test_the_cut_may_come_at_flush(self) -> None:
        parser = ReplyParser(ending=frozenset({"question"}))
        parser.feed(self.ASK)
        assert parser.cut is None
        assert parser.flush() == [ToolCall("question", "", closed=True)]
        assert parser.cut == Cut(at=len(self.ASK), fenced=True)

    def test_a_closing_fences_trailing_spaces_are_within_the_cut(self) -> None:
        parser = ReplyParser(ending=frozenset({"question"}))
        parser.feed("```otk-question\nGo?\n```  \nmore")
        assert parser.cut == Cut(at=len("```otk-question\nGo?\n```  "), fenced=True)

    def test_a_reply_that_never_closes_the_call_is_not_cut(self) -> None:
        parser = ReplyParser(ending=frozenset({"question"}))
        assert parser.feed("```otk-question\nGo?") == [ToolCall("question", "Go?", closed=False)]
        parser.flush()
        assert parser.cut is None

    def test_only_the_first_ending_call_cuts(self) -> None:
        parser = ReplyParser(ending=frozenset({"question", "note"}))
        pieces = parser.feed("```otk-note\nx\n```\n```otk-question\nGo?\n```")
        assert pieces == [ToolCall("note", "x", closed=True)]
        assert parser.cut == Cut(at=len("```otk-note\nx\n```"), fenced=True)

    def test_what_the_cut_keeps_is_a_closed_call(self) -> None:
        # The recorder's rule, pinned here with the parser's: what is
        # kept, with the fence added where `fenced` is False, parses
        # back as the same pieces, the call closed.
        for body in (
            "Creak.\n\n" + self.ASK + "\n\nShe goes.",
            "```otk-question\nGo in?\n1. Yes\n```otk-note\nlater\n```",
            "```otk-question\nGo in?\n\n```otk-note\nlater\n```",
            "```otk-question\n```otk-note\nlater\n```",
        ):
            parser = ReplyParser(ending=frozenset({"question"}))
            pieces = [*parser.feed(body), *parser.flush()]
            assert parser.cut is not None, body
            kept = body[: parser.cut.at] + ("" if parser.cut.fenced else "\n" + FENCE)
            assert parse_reply(kept) == streamed_join(pieces), body
            last = parse_reply(kept)[-1]
            assert isinstance(last, ToolCall) and last.closed, body


def streamed(chunks: list[str]) -> list[Prose | ToolCall]:
    """The chunks fed through a ReplyParser, its pieces joined the
    documented way."""
    parser = ReplyParser()
    pieces = [piece for chunk in chunks for piece in parser.feed(chunk)]
    return streamed_join(pieces + parser.flush())


def streamed_join(pieces: list[Prose | ToolCall]) -> list[Prose | ToolCall]:
    """Pieces joined the documented way: prose runs together, and so
    does a call's text up to the piece that is closed."""
    out: list[Prose | ToolCall] = []
    open_call = False  # the last segment is a call still taking pieces
    for piece in pieces:
        last = out[-1] if out else None
        if isinstance(piece, Prose):
            if isinstance(last, Prose):
                out[-1] = Prose(last.text + piece.text)
            else:
                out.append(piece)
            open_call = False
        elif open_call and isinstance(last, ToolCall) and last.name == piece.name:
            out[-1] = ToolCall(piece.name, last.text + piece.text, closed=piece.closed)
            open_call = not piece.closed
        else:
            out.append(piece)
            open_call = not piece.closed
    return out
