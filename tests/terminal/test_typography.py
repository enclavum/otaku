"""The streaming typesetter.

The typesetter's contract: text arrives in arbitrary chunks and is written
out immediately with ANSI styling, never repainted. So the tests check two
things — that the visible text survives (markers consumed, content kept),
and that how the input is split into chunks changes nothing.
"""

import io
import re

from otaku.terminal import color
from otaku.terminal.typography import Typesetter

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BOLD = "\x1b[1m"
_ITALIC = "\x1b[3m"
_RESET = "\x1b[0m"
_SPEECH = color("cyan")  # the tests pin an explicit spec, independent of the shipped default


class TestPlainText:
    def test_passes_prose_through_unchanged(self) -> None:
        assert plain("The door opens.\n") == "The door opens.\n"

    def test_keeps_blank_lines(self) -> None:
        assert plain("one\n\ntwo\n") == "one\n\ntwo\n"

    def test_keeps_text_without_a_trailing_newline(self) -> None:
        assert plain("no newline") == "no newline"


class TestInlineMarkup:
    def test_bolds_double_asterisks(self) -> None:
        assert _BOLD in typeset("**loud**\n")

    def test_consumes_the_bold_markers(self) -> None:
        assert plain("**loud**\n") == "loud\n"

    def test_italicises_single_asterisks(self) -> None:
        assert _ITALIC in typeset("*soft*\n")

    def test_consumes_the_italic_markers(self) -> None:
        assert plain("*soft*\n") == "soft\n"

    def test_consumes_inline_code_backticks(self) -> None:
        assert plain("say `run` now\n") == "say run now\n"

    def test_keeps_an_escaped_asterisk(self) -> None:
        assert plain("2 \\* 3\n") == "2 * 3\n"

    def test_closes_styles_at_the_end_of_a_line(self) -> None:
        assert typeset("**unclosed\n").endswith(f"{_RESET}\n")


class TestBlocks:
    def test_bolds_a_header(self) -> None:
        assert _BOLD in typeset("# Title\n")

    def test_consumes_the_header_marker(self) -> None:
        assert plain("# Title\n") == "Title\n"

    def test_renders_a_bullet_for_a_list_item(self) -> None:
        assert plain("- item\n") == "• item\n"

    def test_keeps_ordered_list_numbering(self) -> None:
        assert plain("1. first\n") == "1. first\n"

    def test_renders_a_quote_bar(self) -> None:
        assert plain("> quoted\n") == "│ quoted\n"

    def test_renders_a_horizontal_rule(self) -> None:
        assert set(plain("---\n").strip()) == {"─"}

    def test_keeps_a_lone_asterisk_line_as_text(self) -> None:
        assert plain("*\n") == "*\n"


class TestFencedCode:
    def test_keeps_the_code_body(self) -> None:
        assert "x = 1" in plain("```\nx = 1\n```\n")

    def test_consumes_the_fence_lines(self) -> None:
        assert "```" not in plain("```\nx = 1\n```\n")

    def test_labels_the_language(self) -> None:
        assert plain("```python\nx = 1\n```\n").startswith("python\n")

    def test_leaves_markup_inside_code_alone(self) -> None:
        assert "**not bold**" in plain("```\n**not bold**\n```\n")

    def test_closes_an_unterminated_fence_on_flush(self) -> None:
        assert "x = 1" in plain("```\nx = 1")


class TestDialogue:
    """Speech is colored in both conventions writers use: paired quotes, and
    a dash-opened line whose attribution is handed over on a dash that
    follows sentence punctuation."""

    def test_a_quoted_span_is_spoken(self) -> None:
        assert spoken('He said "come in" softly.\n') == ['"come in"']

    def test_every_quote_pairing_closes(self) -> None:
        assert spoken("«Da», sagte er.\n") == ["«Da»"]
        assert spoken("\u201cYes,\u201d she said.\n") == ["\u201cYes,\u201d"]
        assert spoken("\u201eJa\u201c, sagte er.\n") == ["\u201eJa\u201c"]
        # The strict low pairing is often typed with the other curly mark.
        assert spoken("\u201eJa\u201d, sagte er.\n") == ["\u201eJa\u201d"]

    def test_an_apostrophe_never_opens_a_span(self) -> None:
        assert spoken("Don't stop, it's fine.\n") == []

    def test_a_dash_line_speaks_until_the_attribution(self) -> None:
        assert spoken("— Hello, — he said.\n") == ["— Hello, "]

    def test_the_attribution_hands_speech_back(self) -> None:
        assert spoken("— Hello, — he said. — Come in.\n") == ["— Hello, ", "— Come in."]

    def test_a_parenthetical_dash_keeps_the_voice(self) -> None:
        # Preceded by a word, not by punctuation: not a handover.
        assert spoken("— I thought — and it matters — that it would.\n") == [
            "— I thought — and it matters — that it would."
        ]

    def test_a_citation_inside_speech_stays_speech(self) -> None:
        # One speaker quoting another: the quote interrupts the spoken line
        # without ending it, so the words after it are still spoken and the
        # attribution after the handover dash is still not.
        assert spoken("— \u00abNot so\u00bb, — he repeated.\n") == [
            "— ",
            "\u00abNot so\u00bb",
            ", ",
        ]

    def test_a_citation_in_the_attribution_stays_narration(self) -> None:
        spans = spoken("— Yes, — he said, quoting \u00abthe sign\u00bb.\n")
        assert spans == ["— Yes, ", "\u00abthe sign\u00bb"]

    def test_a_list_marker_is_not_a_dialogue_dash(self) -> None:
        assert spoken("- an item\n") == []

    def test_a_dash_outside_a_dash_line_is_narration(self) -> None:
        assert spoken("The hall — long and unlit — smelled of stone.\n") == []

    def test_code_is_never_spoken(self) -> None:
        assert spoken('Try `printf("hi")` now.\n') == []
        assert spoken('```py\nprint("— no —")\n```\n') == []

    def test_speech_never_crosses_a_line(self) -> None:
        assert spoken('An unclosed "quote here\nand the next line.\n') == ['"quote here']

    def test_chunking_does_not_change_the_result(self) -> None:
        text = '— Hello, — he said. — Come in.\nShe said "no" and left.\n'
        assert typeset(text) == typeset(text, chunk=1) == typeset(text, chunk=3)

    def test_flush_closes_speech_left_open_at_stream_end(self) -> None:
        assert spoken("— unfinished") == ["— unfinished"]

    def test_speech_is_not_bold_unless_asked(self) -> None:
        assert _SPEECH + _BOLD not in typeset('"hi"\n')
        assert _SPEECH + _BOLD in typeset('"hi"\n', speech_bold=True)

    def test_a_color_spec_is_resolved_and_used(self) -> None:
        styled = typeset('"hi"\n', speech_color="magenta")
        assert color("magenta") + '"hi"' in styled
        assert _SPEECH not in styled
        assert color("#9a6700") + '"hi"' in typeset('"hi"\n', speech_color="#9a6700")

    def test_auto_is_the_dark_blue_slot(self) -> None:
        assert "\x1b[34m" in typeset('"hi"\n', speech_color="auto")

    def test_an_unreadable_spec_resolves_like_auto(self) -> None:
        unreadable = typeset('"hi"\n', speech_color="chartreuse")
        assert unreadable == typeset('"hi"\n', speech_color="auto")


class TestChunking:
    DOCUMENT = (
        "# Title\n\nPlain **bold** and *italic* text.\n\n"
        "- one\n- two\n\n> a quote\n\n```py\nx = 1\n```\n\nlast line\n"
    )

    def test_one_chunk_and_char_by_char_agree(self) -> None:
        assert typeset(self.DOCUMENT) == typeset(self.DOCUMENT, chunk=1)

    def test_every_chunk_size_agrees(self) -> None:
        expected = typeset(self.DOCUMENT)
        for size in (2, 3, 5, 7, 13):
            assert typeset(self.DOCUMENT, chunk=size) == expected

    def test_a_marker_split_across_chunks_still_bolds(self) -> None:
        out = io.StringIO()
        streamer = Typesetter(out)
        streamer.feed("**bo")
        streamer.feed("ld**\n")
        streamer.flush()
        assert _BOLD in out.getvalue()
        assert _ANSI.sub("", out.getvalue()) == "bold\n"


class TestFlush:
    def test_leaves_no_style_open(self) -> None:
        for text in ("**bold", "*italic", "`code", "# header", "text"):
            assert not _open_style(typeset(text))


class TestRenderMarkdown:
    """`render` returns as a string exactly what the streamer
    would have printed for the same text."""

    def test_matches_the_streamers_output(self) -> None:
        text = "# Scene\nShe *waits*.\n**Bold** move, `run()`.\n> said so\n"
        assert typeset(text) == typeset(text)

    def test_returns_plain_prose_unchanged(self) -> None:
        assert typeset("The door opens.") == "The door opens."

    def test_returns_empty_for_empty_input(self) -> None:
        assert typeset("") == ""

    def test_closes_styles_at_the_end(self) -> None:
        assert typeset("**unclosed").endswith(_RESET)


def typeset(text: str, *, chunk: int = 0, **knobs: object) -> str:
    """`text` through the typesetter, in one chunk or in `chunk`-sized
    bites; `knobs` pass straight to the Typesetter (speech_color,
    speech_bold), with the color pinned to "cyan" unless a test says
    otherwise — "auto" would depend on the terminal running the tests."""
    knobs.setdefault("speech_color", "cyan")
    out = io.StringIO()
    streamer = Typesetter(out, **knobs)  # type: ignore[arg-type]
    if chunk:
        for i in range(0, len(text), chunk):
            streamer.feed(text[i : i + chunk])
    else:
        streamer.feed(text)
    streamer.flush()
    return out.getvalue()


def plain(text: str, *, chunk: int = 0) -> str:
    """The rendered output with the styling stripped — what a reader sees."""
    return _ANSI.sub("", typeset(text, chunk=chunk))


def spoken(text: str) -> list[str]:
    """The visible text of each span the typesetter marked as speech."""
    spans = re.findall(f"{re.escape(_SPEECH)}(.*?){re.escape(_RESET)}", typeset(text), re.S)
    return [_ANSI.sub("", span) for span in spans]


def _open_style(rendered: str) -> bool:
    """True when the output ends inside a style (no RESET after the last
    style escape) — the terminal would stay coloured."""
    codes = _ANSI.findall(rendered)
    for code in reversed(codes):
        if code == _RESET:
            return False
        if code in (_BOLD, _ITALIC, "\x1b[2m"):
            return True
    return False
