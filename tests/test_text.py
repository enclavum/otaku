"""Tests for otaku.text string helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from otaku.text import flatten, format_context, format_size, pretty_path, truncate


class TestPrettyPath:
    def test_under_home_renders_tilde(self) -> None:
        assert pretty_path(Path.home() / ".otaku" / "config.toml") == "~/.otaku/config.toml"

    def test_outside_home_stays_absolute(self) -> None:
        assert pretty_path(Path("/etc/hosts")) == "/etc/hosts"


class TestFlatten:
    def test_collapses_newlines_and_tabs(self) -> None:
        assert flatten("a\nb\tc") == "a b c"

    def test_strips_outer_whitespace(self) -> None:
        assert flatten("  \n hi \t ") == "hi"

    def test_empty(self) -> None:
        assert flatten("") == ""


class TestTruncate:
    def test_shorter_than_limit_is_unchanged(self) -> None:
        assert truncate("hello", 10) == "hello"

    def test_exact_length_is_unchanged(self) -> None:
        assert truncate("hello", 5) == "hello"

    def test_longer_gets_ellipsis(self) -> None:
        assert truncate("hello", 3) == "he…"
        assert len(truncate("hello", 3)) == 3

    def test_limit_one_is_hard_slice_no_ellipsis(self) -> None:
        assert truncate("hello", 1) == "h"

    def test_limit_zero(self) -> None:
        assert truncate("hello", 0) == ""


class TestFormatSize:
    def test_none(self) -> None:
        assert format_size(None) == "—"

    def test_zero_and_negative(self) -> None:
        assert format_size(0) == "—"
        assert format_size(-5) == "—"

    def test_one_gb(self) -> None:
        assert format_size(1024**3) == "1.0 GB"

    def test_always_one_decimal(self) -> None:
        assert format_size(int(1.5 * 1024**3)) == "1.5 GB"


class TestFormatContext:
    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            (None, ""),
            (0, ""),
            (-1, ""),
            (1024, "1K"),
            (2048, "2K"),
            (8192, "8K"),
            (131072, "128K"),
            (1000, "1,000"),
            (1500, "1,500"),
            (1048576, "1M"),
            (2097152, "2M"),
            (1572864, "1536K"),  # 1.5M: not an exact M multiple → falls to K
        ],
    )
    def test_cases(self, n: int | None, expected: str) -> None:
        assert format_context(n) == expected
