"""Streaming markdown renderer.

Consumes text chunks as they arrive from the model and emits ANSI-styled
output. Handles inline markup (`*…*`, `**…**`, `` `…` ``) and block markup
detected at the start of a line: ATX headers, unordered/ordered lists,
blockquotes, horizontal rules, and fenced code blocks (rendered dim, the
info string as a language label).

The renderer is forward-only (no repaint). Block markers are the only thing
buffered, and only until the line's type is known — a plain prose line
resolves on its first character, so normal text still streams character by
character. Code-block bodies buffer one line at a time (needed to detect
the closing fence). State survives chunk boundaries; `flush()` closes any
open span or unterminated fence so the terminal is never left styled.
"""

import io
import re
import shutil
import sys
from typing import Any, TextIO

_BOLD = "\x1b[1m"
_ITALIC = "\x1b[3m"
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"

_MORE: Any = object()  # verdict: keep buffering, block type not yet known

_RE_HEADER = re.compile(r"^ {0,3}(#{1,6}) ")
_RE_ULIST = re.compile(r"^(\s*)[-*+] ")
_RE_OLIST = re.compile(r"^(\s*)(\d{1,9})[.)] ")
_RE_QUOTE = re.compile(r"^(\s*)>")
_RE_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_RE_HR = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")


class MarkdownStreamer:
    """Block-and-inline markdown state machine. Feed text via `feed()`; call
    `flush()` once the stream ends to close any open span or fence."""

    def __init__(self, out: TextIO | None = None) -> None:
        self._out = out if out is not None else sys.stdout
        # inline span state
        self._bold = False
        self._italic = False
        self._code = False
        self._header = False  # whole line is a header → bold
        self._pending_star = False
        self._escape = False
        # line/block state
        self._at_bol = True  # at the start of a line, classifying the block
        self._pending = ""  # buffered leading chars while classifying
        self._eat_space = False  # swallow one leading space (after `>`)
        # fenced-code state
        self._in_fence = False
        self._fence_char = "`"
        self._fence_len = 3
        self._fence_lang = ""
        self._code_line = ""

    def feed(self, text: str) -> None:
        for ch in text:
            self._consume(ch)
        self._out.flush()

    def flush(self) -> None:
        if self._in_fence:
            if self._code_line:
                self._render_code_line(self._code_line)
                self._out.write("\n")
            self._in_fence = False
            self._code_line = ""
        elif self._at_bol and self._pending:
            pending, self._pending = self._pending, ""
            self._flush_text(pending)
            self._at_bol = False
        styled = self._bold or self._italic or self._code or self._header
        if self._pending_star:
            self._pending_star = False
            if self._italic:
                self._italic = False
            else:
                self._out.write("*")
        if styled:
            self._out.write(_RESET)
        self._bold = self._italic = self._code = self._header = False
        self._out.flush()

    # ---------- top-level dispatch ----------

    def _consume(self, ch: str) -> None:
        if self._in_fence:
            self._fence_consume(ch)
            return
        if self._at_bol:
            self._bol_consume(ch)
            return
        if ch == "\n":
            self._end_line()
            return
        if self._eat_space:
            self._eat_space = False
            if ch == " ":
                return
        self._inline(ch)

    # ---------- beginning of line: detect the block type ----------

    def _bol_consume(self, ch: str) -> None:
        if ch == "\n":
            # Line ended while still classifying: resolve fence / rule / text.
            pending, self._pending = self._pending, ""
            fence = _RE_FENCE.match(pending)
            if fence:
                self._enter_fence(fence.group(1), fence.group(2))
                return
            if _RE_HR.match(pending):
                self._render_hr()
                return
            self._flush_text(pending)
            self._end_line()
            return
        self._pending += ch
        verdict = self._classify(self._pending)
        if verdict is _MORE:
            return
        self._start_block(verdict)

    def _classify(self, line: str) -> Any:
        if _RE_HEADER.match(line):
            return ("header",)
        m = _RE_OLIST.match(line)
        if m:
            return ("olist", m.group(1), m.group(2))
        m = _RE_ULIST.match(line)
        if m:
            return ("ulist", m.group(1))
        m = _RE_QUOTE.match(line)
        if m:
            return ("quote", m.group(1))
        # Fence openers need the whole line (for the info string) — buffer on.
        if re.match(r"^\s*(`{3,}|~{3,})", line):
            return _MORE
        stripped = line.lstrip(" ")
        if stripped == "":
            return _MORE  # only indentation so far
        first = stripped[0]
        if first == "#":  # header candidate: `#`… awaiting the required space
            run = len(stripped) - len(stripped.lstrip("#"))
            return _MORE if stripped[run:] == "" and run <= 6 else ("text",)
        if first in "`~":  # 1-2 fence chars could still grow into a fence
            run = len(stripped) - len(stripped.lstrip(first))
            return _MORE if run <= 2 and stripped[run:] == "" else ("text",)
        if first in "-+*_":  # bare marker: list / rule / emphasis undecided
            if len(stripped) == 1:
                return _MORE
            if first in "-*_" and all(c == first for c in stripped):
                return _MORE  # possible horizontal rule
            return ("text",)
        if first.isdigit():  # ordered-list marker still forming
            if re.fullmatch(r"\s*\d+[.)]?", line):
                return _MORE
            return ("text",)
        return ("text",)

    def _start_block(self, verdict: Any) -> None:
        kind = verdict[0]
        self._at_bol = False
        marker, self._pending = self._pending, ""
        if kind == "text":
            self._flush_text(marker)
        elif kind == "header":
            self._header = True
            self._restyle()
        elif kind == "ulist":
            self._out.write(f"{verdict[1]}{_DIM}•{_RESET} ")
        elif kind == "olist":
            self._out.write(f"{verdict[1]}{_DIM}{verdict[2]}.{_RESET} ")
        elif kind == "quote":
            self._out.write(f"{verdict[1]}{_DIM}│{_RESET} ")
            self._eat_space = True  # drop the space after `>`

    def _flush_text(self, text: str) -> None:
        """Stream buffered leading text through the inline state machine."""
        for ch in text:
            self._inline(ch)

    # ---------- fenced code blocks ----------

    def _enter_fence(self, fence: str, info: str) -> None:
        self._in_fence = True
        self._fence_char = fence[0]
        self._fence_len = len(fence)
        tokens = info.strip().split()
        self._fence_lang = tokens[0] if tokens else ""
        self._code_line = ""
        if self._fence_lang:
            self._out.write(f"{_DIM}{self._fence_lang}{_RESET}\n")

    def _fence_consume(self, ch: str) -> None:
        if ch != "\n":
            self._code_line += ch
            return
        line, self._code_line = self._code_line, ""
        if self._is_close_fence(line):
            self._in_fence = False
            self._at_bol = True
            return
        self._render_code_line(line)
        self._out.write("\n")

    def _is_close_fence(self, line: str) -> bool:
        stripped = line.strip()
        return (
            bool(stripped)
            and all(c == self._fence_char for c in stripped)
            and len(stripped) >= self._fence_len
        )

    def _render_code_line(self, line: str) -> None:
        self._out.write(f"{_DIM}{line}{_RESET}")

    # ---------- horizontal rule ----------

    def _render_hr(self) -> None:
        try:
            width = shutil.get_terminal_size((80, 24)).columns
        except OSError:
            width = 80
        self._out.write(f"{_DIM}{'─' * min(width, 80)}{_RESET}\n")
        self._at_bol = True

    # ---------- inline emphasis / code (within a line) ----------

    def _inline(self, ch: str) -> None:
        if self._escape:
            self._escape = False
            self._out.write(ch)
            return
        if self._code:
            if ch == "`":
                self._code = False
                self._restyle()
            else:
                self._out.write(ch)
            return
        if self._pending_star:
            self._pending_star = False
            if ch == "*":
                self._bold = not self._bold
                self._restyle()
                return
            self._italic = not self._italic
            self._restyle()
            self._inline(ch)  # reprocess ch under the new state
            return
        if ch == "\\":
            self._escape = True
            return
        if ch == "*":
            self._pending_star = True
            return
        if ch == "`":
            self._code = True
            self._restyle()
            return
        self._out.write(ch)

    def _restyle(self) -> None:
        """Emit _RESET then the currently-active style escapes — cheaper than
        toggling attributes off, which terminals do inconsistently."""
        self._out.write(_RESET)
        if self._bold or self._header:
            self._out.write(_BOLD)
        if self._italic:
            self._out.write(_ITALIC)
        if self._code:
            self._out.write(_DIM)

    def _end_line(self) -> None:
        """Close the current line: resolve a dangling `*`, reset open spans,
        and emit the newline — inline spans do not cross lines, so the
        terminal can never get stuck styled on a mid-span break."""
        styled = self._bold or self._italic or self._code or self._header
        if self._pending_star:
            self._pending_star = False
            if self._italic:
                self._italic = False
            else:
                self._out.write("*")
        if styled:
            self._out.write(_RESET)
        self._bold = self._italic = self._code = self._header = False
        self._escape = self._eat_space = False
        self._out.write("\n")
        self._at_bol = True
        self._pending = ""


def render_markdown(text: str) -> str:
    """`text` rendered in one pass, returned as a string: exactly what the
    streamer would have printed — for echoing stored messages the way they
    looked when they streamed."""
    out = io.StringIO()
    streamer = MarkdownStreamer(out)
    streamer.feed(text)
    streamer.flush()
    return out.getvalue()
