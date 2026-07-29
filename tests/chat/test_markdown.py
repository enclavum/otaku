"""The streaming markdown renderer.

The renderer's contract: text arrives in arbitrary chunks and is written
out immediately with ANSI styling, never repainted. So the tests check two
things — that the visible text survives (markers consumed, content kept),
and that how the input is split into chunks changes nothing.
"""

import io
import re

from otaku.chat.markdown import MarkdownStreamer, render_markdown

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_BOLD = "\x1b[1m"
_ITALIC = "\x1b[3m"
_RESET = "\x1b[0m"


def render(text: str, *, chunk: int = 0) -> str:
    """`text` through the renderer, in one chunk or in `chunk`-sized bites."""
    out = io.StringIO()
    streamer = MarkdownStreamer(out)
    if chunk:
        for i in range(0, len(text), chunk):
            streamer.feed(text[i : i + chunk])
    else:
        streamer.feed(text)
    streamer.flush()
    return out.getvalue()


def plain(text: str, *, chunk: int = 0) -> str:
    """The rendered output with the styling stripped — what a reader sees."""
    return _ANSI.sub("", render(text, chunk=chunk))


class TestPlainText:
    def test_passes_prose_through_unchanged(self) -> None:
        assert plain("The door opens.\n") == "The door opens.\n"

    def test_keeps_blank_lines(self) -> None:
        assert plain("one\n\ntwo\n") == "one\n\ntwo\n"

    def test_keeps_text_without_a_trailing_newline(self) -> None:
        assert plain("no newline") == "no newline"


class TestInlineMarkup:
    def test_bolds_double_asterisks(self) -> None:
        assert _BOLD in render("**loud**\n")

    def test_consumes_the_bold_markers(self) -> None:
        assert plain("**loud**\n") == "loud\n"

    def test_italicises_single_asterisks(self) -> None:
        assert _ITALIC in render("*soft*\n")

    def test_consumes_the_italic_markers(self) -> None:
        assert plain("*soft*\n") == "soft\n"

    def test_consumes_inline_code_backticks(self) -> None:
        assert plain("say `run` now\n") == "say run now\n"

    def test_keeps_an_escaped_asterisk(self) -> None:
        assert plain("2 \\* 3\n") == "2 * 3\n"

    def test_closes_styles_at_the_end_of_a_line(self) -> None:
        assert render("**unclosed\n").endswith(f"{_RESET}\n")


class TestBlocks:
    def test_bolds_a_header(self) -> None:
        assert _BOLD in render("# Title\n")

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


class TestChunking:
    DOCUMENT = (
        "# Title\n\nPlain **bold** and *italic* text.\n\n"
        "- one\n- two\n\n> a quote\n\n```py\nx = 1\n```\n\nlast line\n"
    )

    def test_one_chunk_and_char_by_char_agree(self) -> None:
        assert render(self.DOCUMENT) == render(self.DOCUMENT, chunk=1)

    def test_every_chunk_size_agrees(self) -> None:
        expected = render(self.DOCUMENT)
        for size in (2, 3, 5, 7, 13):
            assert render(self.DOCUMENT, chunk=size) == expected

    def test_a_marker_split_across_chunks_still_bolds(self) -> None:
        out = io.StringIO()
        streamer = MarkdownStreamer(out)
        streamer.feed("**bo")
        streamer.feed("ld**\n")
        streamer.flush()
        assert _BOLD in out.getvalue()
        assert _ANSI.sub("", out.getvalue()) == "bold\n"


class TestFlush:
    def test_leaves_no_style_open(self) -> None:
        for text in ("**bold", "*italic", "`code", "# header", "text"):
            assert not _open_style(render(text))


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


class TestRenderMarkdown:
    """`render_markdown` returns as a string exactly what the streamer
    would have printed for the same text."""

    def test_matches_the_streamers_output(self) -> None:
        text = "# Scene\nShe *waits*.\n**Bold** move, `run()`.\n> said so\n"
        assert render_markdown(text) == render(text)

    def test_returns_plain_prose_unchanged(self) -> None:
        assert render_markdown("The door opens.") == "The door opens."

    def test_returns_empty_for_empty_input(self) -> None:
        assert render_markdown("") == ""

    def test_closes_styles_at_the_end(self) -> None:
        assert render_markdown("**unclosed").endswith(_RESET)
