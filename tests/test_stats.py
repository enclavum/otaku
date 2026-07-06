"""Tests for otaku.chat.stats.format_stats."""

from __future__ import annotations

from otaku.chat.stats import format_stats
from otaku.client import FinalStats


class TestFormatStats:
    def test_full_line(self) -> None:
        s = FinalStats(
            prompt_tokens=40,
            completion_tokens=37,
            duration_seconds=1.3,
            context_max=32768,
            generation_seconds=1.05,
        )
        # rate = 37 / 1.05 = 35.238…
        assert (
            format_stats(s)
            == "[ total 1.3s, prompt 40 tok, eval 37 tok @ 35.2 tok/s, ctx 0% / 32K ]"
        )

    def test_rate_uses_decode_span_not_total(self) -> None:
        fast_decode = FinalStats(
            prompt_tokens=None,
            completion_tokens=100,
            duration_seconds=10.0,
            generation_seconds=1.0,
        )
        # 100 tok over the 1s decode span, not 10s wall-clock.
        assert "eval 100 tok @ 100.0 tok/s" in format_stats(fast_decode)

    def test_falls_back_to_wall_clock_when_no_decode_span(self) -> None:
        s = FinalStats(
            prompt_tokens=None,
            completion_tokens=10,
            duration_seconds=2.0,
            generation_seconds=None,
        )
        assert format_stats(s) == "[ total 2.0s, eval 10 tok @ 5.0 tok/s ]"

    def test_no_rate_when_all_durations_zero(self) -> None:
        s = FinalStats(
            prompt_tokens=None,
            completion_tokens=5,
            duration_seconds=0.0,
            generation_seconds=0.0,
        )
        assert format_stats(s) == "[ total 0.0s, eval 5 tok ]"

    def test_context_without_prompt_shows_cap_only(self) -> None:
        s = FinalStats(
            prompt_tokens=None,
            completion_tokens=None,
            duration_seconds=1.0,
            context_max=8192,
        )
        assert format_stats(s) == "[ total 1.0s, ctx 8K ]"

    def test_context_percentage_with_prompt(self) -> None:
        s = FinalStats(
            prompt_tokens=16384,
            completion_tokens=None,
            duration_seconds=1.0,
            context_max=32768,
        )
        assert "ctx 50% / 32K" in format_stats(s)

    def test_none_prompt_and_completion_are_skipped(self) -> None:
        s = FinalStats(prompt_tokens=None, completion_tokens=None, duration_seconds=0.5)
        assert format_stats(s) == "[ total 0.5s ]"

    def test_zero_context_max_is_skipped(self) -> None:
        s = FinalStats(
            prompt_tokens=10, completion_tokens=None, duration_seconds=1.0, context_max=0
        )
        assert "ctx" not in format_stats(s)
