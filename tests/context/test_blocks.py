"""Tagged blocks in a reply: `<otk-NAME>…</otk-NAME>` spans that are not
story.

The `otk-` namespace is the whole test of what a block is: no list of
names is consulted, so a block stays one in a build that never heard of
its tool. `split`'s contract: the body as `Prose` and `Block` segments
in order; a block's `tag` is its NAME, lowercase, its inside kept as
written, running to the end when never closed; anything outside the
namespace is prose. `strip`'s: the body without its blocks, tags and
all, nothing else changed. `Splitter`'s: the same split over a stream —
whatever the chunking, the pieces joined are `split`'s segments; a tail
that may yet open or close a tag is held back until it does or cannot.
"""

import pytest

from otaku.context.blocks import Block, Prose, Splitter, split, strip


class TestSplit:
    def test_text_without_blocks_is_one_prose_segment(self) -> None:
        assert split("She nods. The door opens.") == [Prose("She nods. The door opens.")]

    def test_a_block_is_cut_out_of_the_prose_around_it(self) -> None:
        assert split("She nods.<otk-notes>plan</otk-notes> The door opens.") == [
            Prose("She nods."),
            Block("notes", "plan", closed=True),
            Prose(" The door opens."),
        ]

    def test_the_inside_is_kept_as_written(self) -> None:
        assert split("<otk-ask>\nStay or go?\n1. Stay\n</otk-ask>") == [
            Block("ask", "\nStay or go?\n1. Stay\n", closed=True)
        ]

    def test_a_tag_matches_in_any_case_and_its_name_reads_lowercase(self) -> None:
        assert split("<OTK-Notes>x</otk-NOTES>") == [Block("notes", "x", closed=True)]

    def test_an_unclosed_block_runs_to_the_end(self) -> None:
        # A reply cut mid-block (Ctrl+C keeps what arrived).
        assert split("She nods.\n<otk-notes>the letter is") == [
            Prose("She nods.\n"),
            Block("notes", "the letter is", closed=False),
        ]

    def test_a_block_of_a_tool_nobody_knows_is_a_block_all_the_same(self) -> None:
        # The tags outlive the build that wrote them: a retired tool's
        # block, or a newer build's, must never turn back into story.
        assert split("<otk-plan>x</otk-plan>") == [Block("plan", "x", closed=True)]

    def test_only_its_own_closing_tag_ends_a_block(self) -> None:
        assert split("<otk-ask>a</otk-notes>b</otk-ask>c") == [
            Block("ask", "a</otk-notes>b", closed=True),
            Prose("c"),
        ]

    def test_markup_outside_the_namespace_is_prose(self) -> None:
        # What is INSIDE a block goes with it, so nothing else may be one:
        # an emphasis keeps its word, a chat log's name keeps its line.
        body = "<notes>x</notes>, <b>bold</b>, <Alice> hello, a < b, and <3"
        assert split(body) == [Prose(body)]

    def test_a_tag_without_a_name_of_letters_is_prose(self) -> None:
        for body in ("<otk->x</otk->", "<otk-a1>x</otk-a1>", "<otk-ask >x", "<otk ask>x"):
            assert split(body) == [Prose(body)], body

    def test_a_stray_closing_tag_is_prose(self) -> None:
        assert split("x</otk-notes>y") == [Prose("x</otk-notes>y")]

    def test_an_empty_block_is_a_segment(self) -> None:
        assert split("<otk-ask></otk-ask>") == [Block("ask", "", closed=True)]

    def test_adjacent_blocks_stay_apart(self) -> None:
        assert split("<otk-notes>a</otk-notes><otk-notes>b</otk-notes>") == [
            Block("notes", "a", closed=True),
            Block("notes", "b", closed=True),
        ]

    def test_a_lookalike_letter_spells_no_tag(self) -> None:
        # U+017F, the long s, case-folds to "s" — and must not match it.
        body = "<otk-aſk>Stay?</otk-aſk>"
        assert split(body) == [Prose(body)]

    def test_an_empty_body_has_no_segments(self) -> None:
        assert split("") == []


class TestStrip:
    def test_text_without_blocks_is_unchanged(self) -> None:
        assert strip("She nods. The door opens.") == "She nods. The door opens."

    def test_a_block_leaves_with_its_tags(self) -> None:
        assert strip("A <otk-notes>plan</otk-notes> B") == "A  B"

    def test_every_block_leaves_across_lines(self) -> None:
        body = "One.\n<otk-notes>first\nsecond</otk-notes>\nTwo.<otk-ask>Stay?\n1. Stay</otk-ask>"
        assert strip(body) == "One.\n\nTwo."

    def test_an_unclosed_block_takes_the_rest(self) -> None:
        assert strip("She nods.\n<otk-notes>the letter is") == "She nods.\n"

    def test_a_text_that_is_only_a_block_is_empty(self) -> None:
        assert strip("<otk-notes>all of it</otk-notes>") == ""

    def test_markup_outside_the_namespace_stays(self) -> None:
        body = "She <i>never</i> agreed.\n<Alice> hello <notes>x</notes>"
        assert strip(body) == body


class TestSplitter:
    @pytest.mark.parametrize(
        "body",
        [
            "She nods. The door opens.",
            "<otk-notes>plan</otk-notes>She nods.<otk-ask>Stay?\n1. Stay\n2. Go</otk-ask>",
            "a < b, <3, <b>bold</b>, <otk->, <otk-a1> and </otk-notes> astray",
            "<OTK-NOTES>x</otk-Notes><otk-notes>y</otk-notes>",
            "<otk-ask></otk-ask>",
            "<otk-ask>a</otk-notes>b</otk-ask>c",
            "She nods.\n<otk-notes>the letter is",
            "ends on a maybe <otk-as",
            "ends on a maybe <ot",
        ],
    )
    def test_any_chunking_gives_the_segments_split_gives(self, body: str) -> None:
        cuts = [(i, j) for i in range(len(body) + 1) for j in range(i, len(body) + 1)]
        for i, j in cuts:
            assert streamed([body[:i], body[i:j], body[j:]]) == split(body), (i, j)
        assert streamed(list(body)) == split(body)

    def test_prose_is_not_held_behind_a_possible_tag(self) -> None:
        assert Splitter().feed("Hello <otk-a") == [Prose("Hello ")]

    def test_a_held_tail_that_opens_nothing_comes_out_as_prose(self) -> None:
        splitter = Splitter()
        splitter.feed("I <")
        assert splitter.feed("3 you") == [Prose("<3 you")]

    def test_a_name_that_never_ends_is_not_held_forever(self) -> None:
        # No tool's name is this long: the text is prose, and is let go.
        pieces = Splitter().feed("<otk-" + "a" * 200)
        assert pieces == [Prose("<otk-" + "a" * 200)]

    def test_flush_hands_over_what_was_held(self) -> None:
        splitter = Splitter()
        splitter.feed("a <otk-as")
        assert splitter.flush() == [Prose("<otk-as")]
        assert splitter.flush() == []

    def test_the_piece_a_closing_tag_ends_is_marked_closed(self) -> None:
        splitter = Splitter()
        assert splitter.feed("<otk-ask>Stay") == [Block("ask", "Stay", closed=False)]
        assert splitter.feed("?</otk-ask>") == [Block("ask", "?", closed=True)]

    def test_current_tag_names_the_block_the_stream_is_in(self) -> None:
        splitter = Splitter()
        assert splitter.current_tag is None
        splitter.feed("She nods.<otk-ask>Stay?")
        assert splitter.current_tag == "ask"
        splitter.feed("</otk-ask>")
        assert splitter.current_tag is None

    def test_a_stream_that_ends_mid_block_keeps_its_current_tag(self) -> None:
        # What tells a recorder that a stop string cut the closing tag off.
        splitter = Splitter()
        splitter.feed("<otk-ask>Stay?\n1. Stay")
        splitter.flush()
        assert splitter.current_tag == "ask"


def streamed(chunks: list[str]) -> list[Prose | Block]:
    """The chunks fed through a Splitter, its pieces joined the documented
    way: prose runs together, and so does a block's text up to the piece
    that is closed."""
    splitter = Splitter()
    pieces = [piece for chunk in chunks for piece in splitter.feed(chunk)]
    pieces += splitter.flush()
    out: list[Prose | Block] = []
    open_block = False  # the last segment is a block still taking pieces
    for piece in pieces:
        last = out[-1] if out else None
        if isinstance(piece, Prose):
            if isinstance(last, Prose):
                out[-1] = Prose(last.text + piece.text)
            else:
                out.append(piece)
            open_block = False
        elif open_block and isinstance(last, Block) and last.tag == piece.tag:
            out[-1] = Block(piece.tag, last.text + piece.text, closed=piece.closed)
            open_block = not piece.closed
        else:
            out.append(piece)
            open_block = not piece.closed
    return out
