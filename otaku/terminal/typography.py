"""Streaming typesetter for the story's text.

Consumes text chunks as they arrive from the model and emits ANSI-styled
output. Handles inline markup (`*…*`, `**…**`, `` `…` ``), spoken lines, and block markup
detected at the start of a line: ATX headers, unordered/ordered lists,
blockquotes, horizontal rules, and fenced code blocks (rendered dim, the
info string as a language label).

Dialogue is colored, in both conventions writers use. Paired quotes —
"…", "…", «…», „…" — open and close a spoken span. A line opening with an
em or en dash is spoken until a dash that FOLLOWS sentence punctuation
hands over to the attribution ("— Yes, — he said. — Come in."), which
hands back on the next such dash. A dash after an ordinary word is a
parenthetical and changes nothing. Being forward-only, the typesetter must
decide at the opening mark, so a quoted word inside narration is colored
too — there is no way to look ahead and reconsider.

The typesetter is forward-only (no repaint). Block markers are the only thing
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

from otaku.formatting import printable
from otaku.terminal import BOLD, DIM, ITALIC, RESET, color
from otaku.terminal.query import background_is_dark

_MORE: Any = object()  # verdict: keep buffering, block type not yet known

_RE_HEADER = re.compile(r"^ {0,3}(#{1,6}) ")
_RE_ULIST = re.compile(r"^(\s*)[-*+] ")
_RE_OLIST = re.compile(r"^(\s*)(\d{1,9})[.)] ")
_RE_QUOTE = re.compile(r"^(\s*)>")
_RE_FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
_RE_HR = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")

# Dialogue. The ASCII hyphen is deliberately absent: `- ` opens a markdown
# list, and a list marker must stay a list marker.
_DASHES = "—–"  # noqa: RUF001 — en dash is deliberate
# Opening quote → the marks that may close it. `“` both opens (English) and
# closes („…“), resolved by whether a span is already open; the straight
# quote closes itself, so it toggles. `„` accepts either curly mark, because
# the strict `„…“` pairing is often typed as `„…”`.
_QUOTE_CLOSERS = {
    "«": "»",
    "\u201c": "\u201d",
    "\u201e": "\u201c\u201d",
    '"': '"',
}
# A dash hands over to (or back from) the attribution only after sentence
# punctuation — anywhere else it is a parenthetical inside the current voice.
_HANDOVER_AFTER = ",.!?…:;"

# What "auto" — the shipped default — resolves to: the teal pair, one
# shade per background (the cool tone this category's readers know as
# speech), designed rather than delegated to the theme. When the terminal
# keeps its background to itself, the theme's own cyan slot keeps us in
# the same family, legible on whatever the background turns out to be.
_AUTO_DARK = "#56b6c2"  # soft teal on a dark background
_AUTO_LIGHT = "#0e7490"  # deep teal on a light one
_AUTO_FALLBACK = "cyan"


class Typesetter:
    """Block-and-inline markdown state machine. Feed text via `feed()`; call
    `flush()` once the stream ends to close any open span or fence."""

    def __init__(
        self, out: TextIO | None = None, *, speech_color: str = "auto", speech_bold: bool = False
    ) -> None:
        self._out = out if out is not None else sys.stdout
        # `speech_color` is the SPEC — "auto", a color name, or #rrggbb —
        # resolved here so every caller can pass the setting through
        # verbatim. An unreadable spec resolves like "auto" rather than
        # printing itself at the reader.
        self._speech_color = _speech_escape(speech_color)
        self._speech_bold = speech_bold
        # inline span state
        self._bold = False
        self._italic = False
        self._code = False
        self._header = False  # whole line is a header → bold
        self._pending_star = False
        self._escape = False
        # dialogue state
        self._speech = False
        self._quote_close = ""  # the mark that will close the open quote
        self._quote_outer = False  # was speech already on when it opened?
        self._dash_line = False  # this line opened with a dialogue dash
        self._col0 = True  # nothing of this line's content written yet
        self._last_sig = ""  # last non-space character written
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
        # The one display chokepoint sanitizes: a control byte in model
        # output could steer the terminal and desync the screen ledger.
        for ch in printable(text):
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
        styled = self._bold or self._italic or self._code or self._header or self._speech
        if self._pending_star:
            self._pending_star = False
            if self._italic:
                self._italic = False
            else:
                self._out.write("*")
        if styled:
            self._out.write(RESET)
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
            self._out.write(f"{verdict[1]}{DIM}•{RESET} ")
        elif kind == "olist":
            self._out.write(f"{verdict[1]}{DIM}{verdict[2]}.{RESET} ")
        elif kind == "quote":
            self._out.write(f"{verdict[1]}{DIM}│{RESET} ")
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
            self._out.write(f"{DIM}{self._fence_lang}{RESET}\n")

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
        self._out.write(f"{DIM}{line}{RESET}")

    # ---------- horizontal rule ----------

    def _render_hr(self) -> None:
        try:
            width = shutil.get_terminal_size((80, 24)).columns
        except OSError:
            width = 80
        self._out.write(f"{DIM}{'─' * min(width, 80)}{RESET}\n")
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
        if self._dialogue(ch):
            return
        self._write(ch)

    def _dialogue(self, ch: str) -> bool:
        """Open or close a spoken span on `ch`. True when it was handled.

        A closing mark stays inside the span and an opening one joins it, so
        the marks themselves carry the color — including the dash that hands
        speech back, which reads as part of the line it introduces."""
        if self._quote_close:
            if ch in self._quote_close:
                self._write(ch)  # the closing mark belongs to the speech
                self._quote_close = ""
                # Back to whatever the quote interrupted, which is NOT
                # always narration: inside a dash-opened line, one speaker
                # quoting another is speech within speech, and forcing it
                # off here would invert the rest of the line — the words
                # after the citation would go plain and the attribution
                # would light up instead.
                self._speech = self._quote_outer
                self._restyle()
                return True
            return False
        if ch in _QUOTE_CLOSERS:
            self._quote_outer = self._speech
            self._speech = True
            self._quote_close = _QUOTE_CLOSERS[ch]
            self._restyle()
            self._write(ch)
            return True
        if ch not in _DASHES:
            return False
        if self._col0:  # a line opening with a dash is spoken
            self._dash_line = True
            self._speech = True
            self._restyle()
            self._write(ch)
            return True
        if self._dash_line and self._last_sig in _HANDOVER_AFTER:
            self._speech = not self._speech
            self._restyle()
            self._write(ch)
            return True
        return False

    def _write(self, ch: str) -> None:
        """Emit one character of content, remembering what a dialogue dash
        needs to know: whether the line has begun, and what preceded it."""
        self._out.write(ch)
        if not ch.isspace():
            self._col0 = False
            self._last_sig = ch

    def _restyle(self) -> None:
        """Emit RESET then the currently-active style escapes — cheaper than
        toggling attributes off, which terminals do inconsistently."""
        self._out.write(RESET)
        if self._speech:
            self._out.write(self._speech_color)
        # Weight only when asked for: the color carries the separation
        # on its own, and bold on a palette slot often means "the bright
        # variant" rather than a heavier font, which shifts the hue.
        if self._bold or self._header or (self._speech and self._speech_bold):
            self._out.write(BOLD)
        if self._italic:
            self._out.write(ITALIC)
        if self._code:
            self._out.write(DIM)

    def _end_line(self) -> None:
        """Close the current line: resolve a dangling `*`, reset open spans,
        and emit the newline — inline spans do not cross lines, so the
        terminal can never get stuck styled on a mid-span break."""
        styled = self._bold or self._italic or self._code or self._header or self._speech
        if self._pending_star:
            self._pending_star = False
            if self._italic:
                self._italic = False
            else:
                self._out.write("*")
        if styled:
            self._out.write(RESET)
        self._bold = self._italic = self._code = self._header = False
        self._speech = self._dash_line = False
        self._quote_close = ""
        self._quote_outer = False
        self._col0 = True
        self._last_sig = ""
        self._escape = self._eat_space = False
        self._out.write("\n")
        self._at_bol = True
        self._pending = ""


def typeset(text: str, *, speech_color: str = "auto", speech_bold: bool = False) -> str:
    """`text` rendered in one pass, returned as a string: exactly what the
    streamer would have printed — for echoing stored messages the way they
    looked when they streamed."""
    out = io.StringIO()
    streamer = Typesetter(out, speech_color=speech_color, speech_bold=speech_bold)
    streamer.feed(text)
    streamer.flush()
    return out.getvalue()


def _speech_escape(spec: str) -> str:
    """The SGR escape for a dialogue-color setting. "auto" — and any spec
    `color` cannot read — picks the teal tuned to the detected background,
    or the theme's cyan when the terminal keeps its background to itself;
    a color name or #rrggbb passes through as itself."""
    if spec.strip().lower() != "auto":
        resolved = color(spec)
        if resolved:
            return resolved
    dark = background_is_dark()
    if dark is None:
        return color(_AUTO_FALLBACK)
    return color(_AUTO_DARK if dark else _AUTO_LIGHT)
