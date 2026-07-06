"""Tiny string utilities used by both chat output and TUI rendering."""

from __future__ import annotations


def flatten(s: str) -> str:
    """Collapse newlines/tabs into spaces and strip — for one-line previews."""
    return s.replace("\n", " ").replace("\t", " ").strip()


def truncate(s: str, n: int) -> str:
    """Truncate to at most n display chars, ending with the ellipsis."""
    if len(s) <= n:
        return s
    if n <= 1:
        return s[:n]
    return s[: n - 1] + "…"


def format_size(n: int | None) -> str:
    """Bytes -> human-readable, always 1 decimal in GB. '—' when unknown."""
    if n is None or n <= 0:
        return "—"
    return f"{n / 1024**3:.1f} GB"


def format_context(n: int | None) -> str:
    """Token count -> compact label ('8K', '128K', '1M') for exact multiples
    of 1024; thousands-separated otherwise. Empty string when unknown (an
    unloaded model has no live context window — a blank cell reads cleaner
    than a placeholder)."""
    if n is None or n <= 0:
        return ""
    if n >= 1_048_576 and n % 1_048_576 == 0:
        return f"{n // 1_048_576}M"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024}K"
    return f"{n:,}"
