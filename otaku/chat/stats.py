"""Format streaming chat stats for the verbose status line.

Output shape:

    [ total 1.3s, prompt 40 tok, eval 37 tok @ 35.2 tok/s, ctx 12% / 32K ]

`total` is wall-clock for the whole request; the `tok/s` rate is computed
over the decode-only span (first emitted token → end, excluding prefill
and time-to-first-token) so it reflects generation speed rather than
end-to-end throughput. Falls back to total wall-clock when no decode span
is available. Fields whose underlying value is None / 0 are skipped.
"""

from __future__ import annotations

from otaku.client import FinalStats
from otaku.text import format_context


def format_stats(s: FinalStats) -> str:
    parts: list[str] = [f"total {s.duration_seconds:.1f}s"]

    if s.prompt_tokens is not None:
        parts.append(f"prompt {s.prompt_tokens} tok")

    if s.completion_tokens is not None:
        # Rate over the decode-only span (excludes prefill/TTFT) when we
        # have it; fall back to total wall-clock otherwise.
        gen = s.generation_seconds if s.generation_seconds else s.duration_seconds
        if gen > 0:
            rate = s.completion_tokens / gen
            parts.append(f"eval {s.completion_tokens} tok @ {rate:.1f} tok/s")
        else:
            parts.append(f"eval {s.completion_tokens} tok")

    if s.context_max:
        cap = format_context(s.context_max)
        if s.prompt_tokens is not None and s.context_max > 0:
            pct = s.prompt_tokens / s.context_max * 100
            parts.append(f"ctx {pct:.0f}% / {cap}")
        else:
            parts.append(f"ctx {cap}")

    return "[ " + ", ".join(parts) + " ]"
