"""Pure text helpers — the stdlib-like leaf any package may use. The
TOML escaping helpers and `render` moved in from the old settings
package: text functions, not settings.
"""

import locale
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Control characters display must drop: C0 minus newline and tab, DEL, and
# C1 (U+0080..U+009F) — xterm honors 8-bit CSI/OSC aliases even in UTF-8 mode.
_CONTROL = {c: None for c in range(32) if chr(c) not in "\n\t"}
_CONTROL[0x7F] = None
_CONTROL.update(dict.fromkeys(range(0x80, 0xA0)))

# The control characters TOML basic strings spell with short escapes.
_STRING_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


# A fork's numbering suffix (see the store's fork titles).
_FORK_NUMBER = re.compile(r" - \d+$")

# An SGR escape — colour, weight, reset. What a terminal eats rather
# than draws, which is the whole of `drawn_width` below.
_SGR = re.compile(r"\x1b\[[0-9;]*m")

# The currencies otaku knows a symbol for; anything else is written with
# its code after the figure ("4.82 XTS"), which is how a reader tells an
# unfamiliar currency from a familiar one.
_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}

# How many places a currency is quoted in. The default is two; the ones
# quoted differently are named, because rounding somebody's balance to
# the wrong place is not a display detail.
_PLACES = {"JPY": 0}
_DEFAULT_PLACES = 2


@dataclass(frozen=True, order=True)
class Money:
    """An amount and the currency it is in.

    DECIMAL, never float: this is a figure somebody is billed against,
    and binary floating point cannot hold two decimal places exactly —
    `0.1 + 0.2` is the reason this type exists rather than a number and
    a string beside it. Build one with `Money.of`, which takes what a
    provider actually sends (a string, an int, another Decimal) and
    refuses what is not a number.

    A leaf type on purpose: providers report balances in it, reports
    carry it, and both frontends print it — so it lives here, where
    everything above may reach it."""

    amount: Decimal
    currency: str = "USD"

    @classmethod
    def of(cls, amount: object, currency: str = "USD") -> "Money | None":
        """`amount` as money — None when it is not a number at all. A
        float is accepted through its own repr (the shortest string that
        round-trips), never through binary expansion."""
        if isinstance(amount, Money):
            return amount
        try:
            return cls(Decimal(str(amount)), currency.upper() or "USD")
        except (InvalidOperation, ValueError, TypeError):
            return None

    def __str__(self) -> str:
        """The figure as a reader sees it: quantized to the currency's
        own places, with its symbol where otaku knows one."""
        places = _PLACES.get(self.currency, _DEFAULT_PLACES)
        figure = f"{self.amount:.{places}f}"
        symbol = _SYMBOLS.get(self.currency)
        return f"{symbol}{figure}" if symbol else f"{figure} {self.currency}"

    def __add__(self, other: "Money") -> "Money":
        """Two amounts of the SAME currency. Adding across currencies is
        a conversion, and otaku has no rate to convert with."""
        if self.currency != other.currency:
            raise ValueError(f"cannot add {self.currency} to {other.currency}")
        return Money(self.amount + other.amount, self.currency)


def pretty_path(path: Path) -> str:
    """A path with the home dir shortened to `~`."""
    try:
        relative = path.relative_to(Path.home())
    except ValueError:
        return str(path)
    return "~" if relative == Path() else f"~/{relative}"


def decode_text(raw: bytes) -> str:
    """`raw` as text, whatever wrote it.

    Everything current writes UTF-8 — but 0.3.0's writer passed no
    encoding, so a file it wrote on native Windows carries the locale's
    ANSI codepage, and a strict read would end an upgrade in a
    traceback. The ladder: UTF-8, the locale's own encoding (the file
    was written on this machine), cp1252 (the common ANSI, for a file
    that crossed machines), then latin-1, which cannot fail. A caller
    that needs to KNOW the file was legacy compares the round-trip:
    valid UTF-8 with plain newlines re-encodes byte-exact, a fallback
    decode or a CRLF file never does.

    Newlines normalize to \n — what `read_text`'s universal-newline
    mode always did, and what reading bytes must not silently lose: a
    0.3.0 on Windows wrote CRLF, and a stray \r is an invalid
    character to a TOML parser."""

    def normalized(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    try:
        return normalized(raw.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    for encoding in (locale.getpreferredencoding(False), "cp1252"):
        try:
            return normalized(raw.decode(encoding))
        except (UnicodeDecodeError, LookupError):
            continue
    return normalized(raw.decode("latin-1"))


def printable(text: str) -> str:
    """`text` with every control character a terminal could act on
    dropped — C0 except newline and tab, DEL, the C1 range. The display
    chokepoint for model output and server messages; storage keeps every
    byte."""
    return text.translate(_CONTROL)


def drawn_width(text: str) -> int:
    """The columns `text` takes on screen, its SGR escapes discounted:
    colour and weight are bytes the terminal eats rather than cells it
    fills, so `len` overstates a styled row — by enough to pad it short
    and step whatever lines up beside it out of true."""
    return len(_SGR.sub("", text))


def flatten(text: str) -> str:
    """Prose as one line: whitespace runs become single spaces, edges
    stripped — for previews that must fit a row."""
    return " ".join(text.split())


def truncate(text: str, limit: int) -> str:
    """At most `limit` display chars, ending with an ellipsis when cut."""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def truncate_label(text: str, limit: int) -> str:
    """A story label cut for one row: flattened, at most `limit` chars —
    a trailing fork number (" - N") surviving the cut, because it is the
    only thing telling two copies of one story apart, and a name that
    needs cutting is exactly where the cut would land."""
    label = flatten(text)
    number = _FORK_NUMBER.search(label)
    if number is None:
        return truncate(label, limit)
    stem = truncate(label[: number.start()], limit - len(number.group()))
    return f"{stem}{number.group()}"


def format_size(size: int | None) -> str:
    """Bytes → human-readable, one decimal in GB; em dash when unknown."""
    if size is None or size <= 0:
        return "—"
    return f"{size / 1024**3:.1f} GB"


def format_duration(seconds: float) -> str:
    """Seconds → "42s" under a minute, "3m 07s" above — the system log's
    span format."""
    if seconds >= 60:
        return f"{int(seconds // 60)}m {int(seconds % 60):02d}s"
    return f"{seconds:.0f}s"


def format_seconds(seconds: float) -> str:
    """Seconds as a sentence carries them — "0.5 seconds", "1 second",
    "30 seconds": tenths below ten, whole from ten up, the noun
    agreeing. `format_duration` is the log's terse span."""
    figure = f"{seconds:.1f}".rstrip("0").rstrip(".") if seconds < 10 else f"{seconds:.0f}"
    return f"{figure} second" if figure == "1" else f"{figure} seconds"


def format_context(tokens: int | None) -> str:
    """A context window the way the catalogs label it: '8K', '128K', '1M';
    "" when unknown. K and M are DECIMAL first — that is how the catalogs
    read them, so a round decimal size wins its label — and an exact
    power of two takes the label ITS vendor prints (131,072 is 128K too);
    anything else rounds to a whole unit."""
    if tokens is None or tokens <= 0:
        return ""
    for unit, decimal, binary in (("M", 1_000_000, 1_048_576), ("K", 1_000, 1_024)):
        if tokens >= decimal and tokens % decimal == 0:
            return f"{tokens // decimal}{unit}"
        if tokens >= binary and tokens % binary == 0:
            return f"{tokens // binary}{unit}"
        if tokens >= decimal:
            return f"{round(tokens / decimal)}{unit}"
    return str(tokens)


def human_age(t: datetime) -> str:
    """How long ago an aware timestamp was: "just now", "7m ago", …"""
    sec = (datetime.now(UTC) - t.astimezone(UTC)).total_seconds()
    if sec < 60:
        return "just now"
    if sec < 3600:
        return f"{int(sec // 60)}m ago"
    if sec < 86400:
        return f"{int(sec // 3600)}h ago"
    return f"{int(sec // 86400)}d ago"


def render(template: str, **substitutions: str) -> str:
    """Fill `{placeholder}`s in ONE pass: only the given names substitute,
    every other brace stays literal, and substituted text is never
    rescanned. Rendering cannot fail: an unknown `{word}` is just text."""
    if not substitutions:
        return template
    pattern = "|".join(r"\{" + re.escape(name) + r"\}" for name in substitutions)
    return re.sub(pattern, lambda m: substitutions[m.group(0)[1:-1]], template)


def toml_key(name: str) -> str:
    """A TOML key or table header: bare when it can be, quoted (and
    escaped) otherwise — keys are values too, and one raw control byte
    there would unparse a whole file."""
    if name and all(c.isalnum() or c in "_-" for c in name):
        return name
    return '"' + _escaped(name) + '"'


def toml_scalar(value: object) -> str:
    """One TOML value (str, int, float, bool), control characters escaped
    so no value can render a file that fails to parse back."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    return '"' + _escaped(str(value)) + '"'


def toml_string(value: str) -> str:
    """A TOML string literal that parses back byte-for-byte: a clean line
    single-quoted, anything with a newline or an apostrophe a
    triple-single literal (whose newline right after the opening TOML
    trims)."""
    if "\n" not in value and "'" not in value:
        return f"'{value}'"
    return f"'''\n{value}'''"


def _escaped(text: str) -> str:
    """`text` as a TOML basic-string body: the short escapes, `\\uXXXX`
    for every other control character."""
    out = []
    for ch in text:
        if ch in _STRING_ESCAPES:
            out.append(_STRING_ESCAPES[ch])
        elif ch < " " or ch == "\x7f":
            out.append(f"\\u{ord(ch):04X}")
        else:
            out.append(ch)
    return "".join(out)
