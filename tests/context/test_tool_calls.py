"""Tool calls in a reply: `<otk-NAME>…</otk-NAME>` spans that are the
model using a tool and are not story.

The `otk-` namespace is the whole test of what a call is: no list of
tools is consulted, so a call stays one in a build that never heard of
its tool. `parse_reply`'s contract: the body as `Prose` and `ToolCall`
segments in order; a call's `name` is the NAME, lowercase, its inside
kept as written, running to the end when never closed; anything
outside the namespace is prose; calls never nest — an opening tag
inside a call ends it and opens its own, any closing tag ends the open
call, a closing tag with nothing open is dropped. `strip`'s: the body
without its calls, tags and all, nothing else changed. `to_wire`'s:
the body under a `ToolSet` — a call of a tool that is on as its
canonical self, one of a tool that is off as its rule's text, any
other gone with the blank line beside it. `ReplyParser`'s: the same
parse over a stream — whatever the chunking, the pieces joined are
`parse_reply`'s segments; a tail that may yet open or close a tag is
held back until it does or cannot.
"""

import pytest

from otaku.context.tool_calls import (
    Prose,
    ReplyParser,
    ToolCall,
    ToolSet,
    closing,
    parse_reply,
    strip,
    to_wire,
)


class TestParseReply:
    def test_text_without_calls_is_one_prose_segment(self) -> None:
        assert parse_reply("She nods. The door opens.") == [Prose("She nods. The door opens.")]

    def test_a_call_is_cut_out_of_the_prose_around_it(self) -> None:
        assert parse_reply("She nods.<otk-note>plan</otk-note> The door opens.") == [
            Prose("She nods."),
            ToolCall("note", "plan", closed=True),
            Prose(" The door opens."),
        ]

    def test_the_inside_is_kept_as_written(self) -> None:
        assert parse_reply("<otk-question>\nStay or go?\n1. Stay\n</otk-question>") == [
            ToolCall("question", "\nStay or go?\n1. Stay\n", closed=True)
        ]

    def test_a_tag_matches_in_any_case_and_its_name_reads_lowercase(self) -> None:
        assert parse_reply("<OTK-Note>x</otk-NOTE>") == [ToolCall("note", "x", closed=True)]

    def test_an_unclosed_call_runs_to_the_end(self) -> None:
        # A reply cut mid-call (Ctrl+C keeps what arrived).
        assert parse_reply("She nods.\n<otk-note>the letter is") == [
            Prose("She nods.\n"),
            ToolCall("note", "the letter is", closed=False),
        ]

    def test_a_call_of_a_tool_nobody_knows_is_a_call_all_the_same(self) -> None:
        # The tags outlive the build that wrote them: a retired tool's
        # call, or a newer build's, must never turn back into story.
        assert parse_reply("<otk-plan>x</otk-plan>") == [ToolCall("plan", "x", closed=True)]

    def test_any_closing_tag_ends_the_open_call(self) -> None:
        # Calls never nest: a closing tag of another name still closes,
        # and the closing tag left over is noise, dropped.
        assert parse_reply("<otk-question>a</otk-note>b</otk-question>c") == [
            ToolCall("question", "a", closed=True),
            Prose("bc"),
        ]

    def test_an_opening_tag_inside_a_call_ends_it_and_opens_its_own(self) -> None:
        assert parse_reply("<otk-note>a<otk-question>b</otk-question>") == [
            ToolCall("note", "a", closed=True),
            ToolCall("question", "b", closed=True),
        ]

    def test_markup_outside_the_namespace_is_prose(self) -> None:
        # What is INSIDE a call goes with it, so nothing else may be one:
        # an emphasis keeps its word, a chat log's name keeps its line.
        body = "<notes>x</notes>, <b>bold</b>, <Alice> hello, a < b, and <3"
        assert parse_reply(body) == [Prose(body)]

    def test_a_tag_without_a_name_of_letters_is_prose(self) -> None:
        for body in ("<otk->x</otk->", "<otk-a1>x</otk-a1>", "<otk-note >x", "<otk note>x"):
            assert parse_reply(body) == [Prose(body)], body

    def test_a_stray_closing_tag_is_dropped(self) -> None:
        assert parse_reply("x</otk-note>y") == [Prose("xy")]

    def test_an_empty_call_is_a_segment(self) -> None:
        assert parse_reply("<otk-question></otk-question>") == [
            ToolCall("question", "", closed=True)
        ]

    def test_adjacent_calls_stay_apart(self) -> None:
        assert parse_reply("<otk-note>a</otk-note><otk-note>b</otk-note>") == [
            ToolCall("note", "a", closed=True),
            ToolCall("note", "b", closed=True),
        ]

    def test_a_lookalike_letter_spells_no_tag(self) -> None:
        # U+017F, the long s, case-folds to "s" — and must not match it.
        body = "<otk-aſk>Stay?</otk-aſk>"
        assert parse_reply(body) == [Prose(body)]

    def test_an_empty_body_has_no_segments(self) -> None:
        assert parse_reply("") == []


class TestStrip:
    def test_text_without_calls_is_unchanged(self) -> None:
        assert strip("She nods. The door opens.") == "She nods. The door opens."

    def test_a_call_leaves_with_its_tags(self) -> None:
        assert strip("A <otk-note>plan</otk-note> B") == "A  B"

    def test_every_call_leaves_across_lines(self) -> None:
        body = (
            "One.\n<otk-note>first\nsecond</otk-note>\nTwo."
            "<otk-question>Stay?\n1. Stay</otk-question>"
        )
        assert strip(body) == "One.\n\nTwo."

    def test_an_unclosed_call_takes_the_rest(self) -> None:
        assert strip("She nods.\n<otk-note>the letter is") == "She nods.\n"

    def test_a_text_that_is_only_a_call_is_empty(self) -> None:
        assert strip("<otk-note>all of it</otk-note>") == ""

    def test_markup_outside_the_namespace_stays(self) -> None:
        body = "She <i>never</i> agreed.\n<Alice> hello <notes>x</notes>"
        assert strip(body) == body


class TestToWire:
    ON = ToolSet(on=frozenset({"note"}))
    OFF = ToolSet(off={"question": lambda inside: inside.strip().splitlines()[0]})

    def test_text_without_calls_is_unchanged(self) -> None:
        assert to_wire("She nods.", self.ON) == "She nods."

    def test_a_call_of_a_tool_that_is_on_goes_as_written(self) -> None:
        body = "She nods.\n<otk-note>plan</otk-note>"
        assert to_wire(body, self.ON) == body

    def test_a_call_that_is_on_goes_in_its_canonical_form(self) -> None:
        # Lowercase tags, and closed — whatever the text spelled or left
        # open — so the history teaches the model closed calls.
        assert to_wire("<OTK-Note>plan", self.ON) == "<otk-note>plan</otk-note>"

    def test_a_call_of_a_tool_that_is_off_becomes_its_rule_text(self) -> None:
        body = "She nods.\n\n<otk-question>Stay or go?\n1. Stay\n2. Go</otk-question>"
        assert to_wire(body, self.OFF) == "She nods.\n\nStay or go?"

    def test_a_call_of_any_other_tool_leaves_the_wire(self) -> None:
        # A white list: a decommissioned tool's calls never ride.
        assert to_wire("A <otk-plan>x</otk-plan>B", self.ON) == "A B"

    def test_a_call_that_leaves_takes_the_blank_line_after_it(self) -> None:
        assert to_wire("<otk-plan>x</otk-plan>\n\nShe nods.", self.ON) == "She nods."

    def test_a_call_that_leaves_at_the_end_takes_the_blank_line_before_it(self) -> None:
        assert to_wire("She nods.\n\n<otk-plan>x</otk-plan>\n", self.ON) == "She nods."

    def test_a_rule_that_makes_nothing_removes_the_call(self) -> None:
        tools = ToolSet(off={"note": lambda inside: ""})
        assert to_wire("She nods.\n\n<otk-note>plan</otk-note>", tools) == "She nods."

    def test_a_stray_closing_tag_leaves_the_wire(self) -> None:
        assert to_wire("x</otk-note>y", self.ON) == "xy"

    def test_an_opening_tag_inside_a_call_ends_it(self) -> None:
        tools = ToolSet(on=frozenset({"note", "question"}))
        body = "<otk-note>a<otk-question>b</otk-question>"
        assert to_wire(body, tools) == "<otk-note>a</otk-note><otk-question>b</otk-question>"


class TestClosing:
    def test_the_closing_tag_of_a_name(self) -> None:
        assert closing("question") == "</otk-question>"


class TestReplyParser:
    @pytest.mark.parametrize(
        "body",
        [
            "She nods. The door opens.",
            "<otk-note>plan</otk-note>She nods.<otk-question>Stay?\n1. Stay\n2. Go</otk-question>",
            "a < b, <3, <b>bold</b>, <otk->, <otk-a1> and </otk-note> astray",
            "<OTK-NOTE>x</otk-Note><otk-note>y</otk-note>",
            "<otk-question></otk-question>",
            "<otk-question>a</otk-note>b</otk-question>c",
            "<otk-note>a<otk-question>b</otk-question>",
            "She nods.\n<otk-note>the letter is",
            "ends on a maybe <otk-qu",
            "ends on a maybe <ot",
        ],
    )
    def test_any_chunking_gives_the_segments_parse_reply_gives(self, body: str) -> None:
        cuts = [(i, j) for i in range(len(body) + 1) for j in range(i, len(body) + 1)]
        for i, j in cuts:
            assert streamed([body[:i], body[i:j], body[j:]]) == parse_reply(body), (i, j)
        assert streamed(list(body)) == parse_reply(body)

    def test_prose_is_not_held_behind_a_possible_tag(self) -> None:
        assert ReplyParser().feed("Hello <otk-q") == [Prose("Hello ")]

    def test_a_held_tail_that_opens_nothing_comes_out_as_prose(self) -> None:
        parser = ReplyParser()
        parser.feed("I <")
        assert parser.feed("3 you") == [Prose("<3 you")]

    def test_a_name_that_never_ends_is_not_held_forever(self) -> None:
        # No tool's name is this long: the text is prose, and is let go.
        pieces = ReplyParser().feed("<otk-" + "a" * 200)
        assert pieces == [Prose("<otk-" + "a" * 200)]

    def test_flush_hands_over_what_was_held(self) -> None:
        parser = ReplyParser()
        parser.feed("a <otk-qu")
        assert parser.flush() == [Prose("<otk-qu")]
        assert parser.flush() == []

    def test_the_piece_a_closing_tag_ends_is_marked_closed(self) -> None:
        parser = ReplyParser()
        assert parser.feed("<otk-question>Stay") == [ToolCall("question", "Stay", closed=False)]
        assert parser.feed("?</otk-question>") == [ToolCall("question", "?", closed=True)]

    def test_current_call_names_the_call_the_stream_is_in(self) -> None:
        parser = ReplyParser()
        assert parser.current_call is None
        parser.feed("She nods.<otk-question>Stay?")
        assert parser.current_call == "question"
        parser.feed("</otk-question>")
        assert parser.current_call is None

    def test_a_stream_that_ends_mid_call_keeps_its_current_call(self) -> None:
        # What tells a recorder that a stop string cut the closing tag off.
        parser = ReplyParser()
        parser.feed("<otk-question>Stay?\n1. Stay")
        parser.flush()
        assert parser.current_call == "question"


def streamed(chunks: list[str]) -> list[Prose | ToolCall]:
    """The chunks fed through a ReplyParser, its pieces joined the
    documented way: prose runs together, and so does a call's text up to
    the piece that is closed."""
    parser = ReplyParser()
    pieces = [piece for chunk in chunks for piece in parser.feed(chunk)]
    pieces += parser.flush()
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
