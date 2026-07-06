"""Tests for the streaming block+inline markdown renderer.

The renderer is a character-level state machine; these feed text (often in odd
chunks, to exercise chunk boundaries) and assert on the ANSI emitted to an
in-memory buffer. Code-block *content* is asserted with a no-/unknown-language
fence so highlighting falls back to a deterministic dim style.
"""

from __future__ import annotations

import io

import pytest

from otaku.chat.mdstream import BOLD, CODE, DIM, ITALIC, RESET, MarkdownStreamer


def render(*chunks: str) -> str:
    buf = io.StringIO()
    md = MarkdownStreamer(out=buf)
    for c in chunks:
        md.feed(c)
    md.flush()
    return buf.getvalue()


class TestInline:
    def test_plain_text_passthrough(self) -> None:
        assert render("hello world") == "hello world"

    def test_bold(self) -> None:
        assert render("**foo**") == f"{RESET}{BOLD}foo{RESET}"

    def test_italic(self) -> None:
        assert render("*foo*") == f"{RESET}{ITALIC}foo{RESET}"

    def test_inline_code(self) -> None:
        assert render("`foo`") == f"{RESET}{CODE}foo{RESET}"

    def test_text_around_bold(self) -> None:
        assert render("a **b** c") == f"a {RESET}{BOLD}b{RESET} c"

    def test_indented_plain_line(self) -> None:
        assert render("  hello") == "  hello"


class TestEscaping:
    def test_backslash_escapes_star(self) -> None:
        assert render(r"\*foo\*") == "*foo*"

    def test_backslash_escapes_backtick(self) -> None:
        assert render(r"\`code\`") == "`code`"

    def test_code_span_is_literal_inside(self) -> None:
        assert render(r"`a\*b`") == f"{RESET}{CODE}a\\*b{RESET}"


class TestChunkBoundaries:
    def test_bold_marker_split(self) -> None:
        assert render("*", "*foo**") == f"{RESET}{BOLD}foo{RESET}"

    def test_bold_content_split(self) -> None:
        assert render("**fo", "o**") == f"{RESET}{BOLD}foo{RESET}"

    def test_char_by_char_matches_whole(self) -> None:
        doc = "# Title\n- a **b**\n```\ncode\n```\n> q\n"
        whole = render(doc)
        by_char = render(*list(doc))
        assert whole == by_char


class TestHeaders:
    def test_header_is_bold_without_hashes(self) -> None:
        assert render("# Title\n") == f"{RESET}{BOLD}Title{RESET}\n"

    def test_deeper_header_also_bold(self) -> None:
        assert render("### Sub\n") == f"{RESET}{BOLD}Sub{RESET}\n"

    def test_hash_without_space_is_literal(self) -> None:
        assert render("#nospace\n") == "#nospace\n"

    def test_seven_hashes_is_literal(self) -> None:
        out = render("####### too deep\n")
        assert out.startswith("#######")


class TestLists:
    def test_unordered_dash(self) -> None:
        assert render("- item\n") == f"{DIM}•{RESET} item\n"

    def test_unordered_star(self) -> None:
        assert render("* item\n") == f"{DIM}•{RESET} item\n"

    def test_unordered_plus(self) -> None:
        assert render("+ item\n") == f"{DIM}•{RESET} item\n"

    def test_ordered(self) -> None:
        assert render("1. one\n") == f"{DIM}1.{RESET} one\n"

    def test_ordered_paren(self) -> None:
        assert render("2) two\n") == f"{DIM}2.{RESET} two\n"

    def test_indent_preserved(self) -> None:
        assert render("  - x\n") == f"  {DIM}•{RESET} x\n"

    def test_list_content_gets_inline_styling(self) -> None:
        assert render("- **b**\n") == f"{DIM}•{RESET} {RESET}{BOLD}b{RESET}\n"


class TestBlockquote:
    def test_quote_with_space(self) -> None:
        assert render("> quote\n") == f"{DIM}│{RESET} quote\n"

    def test_quote_without_space(self) -> None:
        assert render(">quote\n") == f"{DIM}│{RESET} quote\n"


class TestHorizontalRule:
    def test_dashes(self) -> None:
        out = render("---\n")
        assert "---" not in out
        assert "───" in out
        assert out.startswith(DIM)

    def test_stars(self) -> None:
        assert "───" in render("***\n")

    def test_two_dashes_is_not_a_rule(self) -> None:
        # only 3+ markers form a rule; "--" is literal text
        assert render("--\n") == "--\n"


class TestFencedCode:
    def test_fence_lines_removed_body_dim(self) -> None:
        out = render("```\ncode line\n```\n")
        assert "```" not in out
        assert "code line" in out
        assert out.startswith(CODE)

    def test_language_label_shown(self) -> None:
        # unknown language → dim body, but the label is still printed
        out = render("```zzznotalang\nhi\n```\n")
        assert "zzznotalang" in out
        assert "hi" in out
        assert "```" not in out

    def test_inline_markup_is_literal_inside_fence(self) -> None:
        out = render("```\n**not bold**\n```\n")
        assert "**not bold**" in out

    def test_tilde_fence(self) -> None:
        out = render("~~~\nx\n~~~\n")
        assert "~~~" not in out
        assert "x" in out

    def test_unterminated_fence_flushes_body(self) -> None:
        out = render("```\ncode\n")  # no closing fence
        assert "code" in out
        assert "```" not in out


class TestHighlighting:
    def test_known_language_is_highlighted(self) -> None:
        pytest.importorskip("pygments")
        out = render("```python\ndef f():\n    return 1\n```\n")
        # Terminal256Formatter emits 256-colour SGR codes for tokens
        assert "\x1b[38;5;" in out
        assert "```" not in out
        assert "python" in out  # language label

    def test_degrades_to_dim_without_pygments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("otaku.chat.mdstream._HIGHLIGHT", False)
        out = render("```python\ndef f(): pass\n```\n")
        assert "def f(): pass" in out  # literal — not split into highlighted tokens
        assert "\x1b[38;5;" not in out  # no colour codes
        assert out.startswith(f"{DIM}python{RESET}\n")  # label + dim body


class TestLineSemantics:
    def test_blank_lines_preserved(self) -> None:
        assert render("para one\n\npara two") == "para one\n\npara two"

    def test_spans_reset_at_line_end(self) -> None:
        # emphasis doesn't cross a hard line break; the terminal is left clean
        assert render("*a\nb*") == f"{RESET}{ITALIC}a{RESET}\nb*"

    def test_flush_resets_unclosed_bold(self) -> None:
        assert render("**foo").endswith(RESET)

    def test_flush_on_clean_text_adds_nothing(self) -> None:
        assert render("hello") == "hello"
