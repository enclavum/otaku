"""The colors otaku prints, in the shade the terminal's background asks
for: one Theme per background, `use` settling it once at launch (the ask
reads stdin, so it must precede prompt_toolkit), `theme()` returning the
one in force. A surface names the role it needs; no module picks a
light/dark pair of its own.

A slot is a `Color`, resolved when the theme is built: the escape a
terminal wants and the name prompt_toolkit wants are the SAME decision,
and a caller should not have to make it again to paint with it.

Two kinds of spec go in, and the difference is deliberate:

- A **name** ("blue") compiles to one of the 16 palette slots, which the
  reader's own terminal theme shades. No shipped slot is one: otaku's
  colors are its own, the same in every terminal. It is what a reader
  spells in `dialogue_color` when they would rather their scheme decided
  the color of speech than otaku did.
- A **#rrggbb** is exact everywhere. Used only where otaku must PAINT:
  the played block's band, the selected row's, and a dialog floating
  over a list it would otherwise show through. A slot the reader's theme
  could shade would make those unreadable against themselves.
- **"default"** hands the terminal back its own color, which is what
  makes a surface transparent. A picker is the terminal with a list on
  it, not a card laid over one.

Which theme is `[terminal]`'s `theme`: "light" or "dark" is the reader saying
so and settles it, and "auto" asks the terminal. An unanswered
background reads as DARK — the ask cannot happen at all on Windows and
fails quietly enough elsewhere (a pipe, an ssh, a silent emulator) that
the fallback has to be the likelier terminal rather than the tidier
default.
"""

import os
import re
from dataclasses import dataclass, replace

from otaku.terminal import tty
from otaku.terminal.api.app.settings import TerminalSettings

# A color NAME is the portable form: it compiles to one of the 16 palette
# slots, which every terminal on every platform renders and the reader's
# own theme shades. A #rrggbb is truecolor — exact everywhere, and
# therefore fixed.
_COLOR_NAMES = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
    "bright black": 90,
    "bright red": 91,
    "bright green": 92,
    "bright yellow": 93,
    "bright blue": 94,
    "bright magenta": 95,
    "bright cyan": 96,
    "bright white": 97,
}
# The session's theme, settled once by `use`; empty until then. A list so
# "not settled yet" needs no separate flag.
_CURRENT: list["Theme"] = []

_HEX = re.compile(r"^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$")

_OSC11 = re.compile(rb"\x1b\]11;rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)")

# `_background_is_dark`'s one-per-session answer; a list so "not asked
# yet" and "asked, unanswered" stay distinct.
_BACKGROUND: list[bool | None] = []


@dataclass(frozen=True)
class Color:
    """One color in every form the terminal paints with, resolved once.
    The empty one is what an unreadable spec becomes: falsy, so a caller
    reading a hand-edited config falls back to its default instead of
    printing garbage at the reader."""

    fg: str = ""  # the SGR foreground escape
    bg: str = ""  # the SGR background escape
    style: str = ""  # what prompt_toolkit calls it, for a style sheet

    def __bool__(self) -> bool:
        return bool(self.fg)


@dataclass(frozen=True)
class Theme:
    """One background's colors, by what they MEAN rather than what they
    are — a surface names the role it needs and gets the right shade."""

    # Full-screen surfaces.
    panel: Color  # the pane a picker draws on
    raised: Color  # a dialog floating over it — opaque, or the list shows through
    text: Color  # body text, over whatever the terminal's background is
    # Text over something OTAKU painted — a band, a selected row, a dialog.
    # It cannot be `text`: that one is the terminal's own foreground, picked
    # to read against the terminal's background and not against ours. Paint
    # a background and you own both colors, or a theme that guessed the
    # background wrong turns white text onto a light grey band.
    ink: Color
    title: Color  # headings and the strong parts of a header
    # Secondary text is DIMMED, not recolored: the surfaces that use this
    # pair it with `dim` (SGR 2), which reduces the intensity of whatever
    # the text already is. A grey guessed against an unknown background
    # can vanish into it — reduced intensity cannot, because it is derived
    # from the color that was readable a moment ago.
    muted: Color  # secondary text — timestamps, help lines, borders
    selection: Color  # the band behind the selected row
    error: Color  # a failure the surface has to report
    ok: Color  # a confirmation (the model picker's tick)
    # Inline, over the terminal's own background.
    band: Color  # the played turn's block, drawn across the row
    placeholder: Color  # the empty prompt's hint
    caret: Color  # the block an empty line in story mode stands on, hex alone
    dialogue: Color  # spoken lines
    dialogue_bold: bool  # weight, not color — but the look is one thing
    command: Color  # a slash command, wherever one is shown


def color(spec: str) -> Color:
    """A color name ("cyan", "bright blue") or a #rrggbb hex, resolved
    into every form the app paints with. The empty `Color` when the spec
    is neither. Names are read loosely — case, hyphens and underscores
    all spell the same slot in a hand-edited file."""
    name = " ".join(spec.strip().lower().replace("-", " ").replace("_", " ").split())
    if name == "default":
        # SGR 39/49: hand the terminal back its own colors. What makes a
        # surface transparent — the reader's background shows through it.
        return Color("\x1b[39m", "\x1b[49m", "default")
    slot = _COLOR_NAMES.get(name)
    if slot is not None:
        # The background code is the foreground one plus ten, which holds
        # for the palette slots and for truecolor alike.
        return Color(f"\x1b[{slot}m", f"\x1b[{slot + 10}m", f"ansi{name.replace(' ', '')}")
    match = _HEX.match(spec.strip())
    if match is None:
        return Color()
    r, g, b = (int(part, 16) for part in match.groups())
    return Color(f"\x1b[38;2;{r};{g};{b}m", f"\x1b[48;2;{r};{g};{b}m", spec.strip().lower())


# A picker is the terminal with a list on it, not a card laid over one:
# the pane, the body text and the headings are the reader's own colors, so
# the browser sits inside their theme instead of punching a hole in it.
# Only what MUST be painted is — the selected row's band, and a dialog,
# which floats over the list and would show it through.
LIGHT = Theme(
    panel=color("default"),
    raised=color("#ffffff"),
    text=color("default"),
    ink=color("#000000"),
    title=color("default"),
    muted=color("default"),
    selection=color("#e4e4e4"),
    error=color("#c0392b"),
    ok=color("#2f9e44"),
    band=color("#f0f0f0"),
    placeholder=color("#8a8a8a"),
    caret=color("#aa5910"),
    dialogue=color("#427AB2"),
    dialogue_bold=False,
    command=color("#8A648E"),
)

DARK = Theme(
    panel=color("default"),
    raised=color("#1c1c1c"),
    text=color("default"),
    ink=color("#e4e4e4"),
    title=color("default"),
    muted=color("default"),
    selection=color("#3a3a3a"),
    error=color("#ff6b6b"),
    ok=color("#51cf66"),
    band=color("#303030"),
    # A mid grey reads as a hint against either background, so the hint is
    # the one slot that does not turn over.
    placeholder=color("#8a8a8a"),
    caret=color("#d58042"),
    dialogue=color("#A4A8EE"),
    dialogue_bold=False,
    command=color("#E17DE1"),
)


def use(settings: TerminalSettings) -> None:
    """Settle the theme for the session: the background's own base, with
    the user's dialogue settings laid over it. Called once, at the
    launch, before anything draws — which is also when the terminal gets
    asked what it looks like, and that ask READS STDIN. From the first
    prompt or picker on, stdin belongs to prompt_toolkit in raw mode,
    where a query eats the keystroke it lands on.

    A setting that is not a color resolves to the empty one and leaves
    the slot alone, so "auto" needs no special case and a typo costs
    nothing."""
    base = _theme_for(settings.theme)
    _CURRENT[:] = [
        replace(
            base,
            dialogue=color(settings.dialogue_color) or base.dialogue,
            dialogue_bold=settings.dialogue_bold,
        )
    ]


def theme() -> Theme:
    """The colors in force — cheap enough to call per rendered row.

    Before `use` has run there is no configured theme, only the
    background's: what a test, a script, or any code outside a launched
    session sees."""
    if _CURRENT:
        return _CURRENT[0]
    return _theme_for("auto")


def _theme_for(setting: str) -> Theme:
    """The theme `setting` asks for: "light" and "dark" are the reader
    saying so and win outright — a setting named for the theme has to set
    it, or the name is a lie — and anything else, "auto" and a typo
    alike, asks the terminal.

    A terminal that will not say reads as DARK. The ask fails in two
    quite different ways and one answer has to serve both: on Windows
    there is no way to ask at all, and everywhere else it is a pipe, an
    ssh, or an emulator keeping quiet. Dark is the better guess for each
    — every console Windows ships is dark, and so is the common terminal
    everywhere else — and a reader it guesses wrong for has the setting
    to say so, which is the part that was missing."""
    if setting == "light":
        return LIGHT
    if setting == "dark":
        return DARK
    answered = _background_is_dark()
    if answered is None:
        return DARK
    return DARK if answered else LIGHT


def _background_is_dark() -> bool | None:
    """Whether the terminal background is dark. The terminal is ASKED
    first (OSC 11, through `tty.ask`) and believed. `COLORFGBG` is only
    the fallback for one that will not answer: it is a static environment
    variable, so it goes stale when a profile changes, survives ssh and
    tmux into terminals it was never about, and some emulators export
    the default profile's rather than the live one's — iTerm2 reports
    `0;15`, a white background, from a dark window. OSC 11 cannot be
    stale; it is the terminal saying what it is painting right now.
    None when neither answers. Asked once — the answer, None included,
    is cached for the session."""
    if not _BACKGROUND:
        _BACKGROUND.append(_probe_background())
    return _BACKGROUND[0]


def _probe_background() -> bool | None:
    match = tty.ask("\x1b]11;?\x07", _OSC11)
    if match is not None:
        r, g, b = (_channel(part) for part in match.groups())
        return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.5
    return _dark_from_colorfgbg(os.environ.get("COLORFGBG", ""))


def _dark_from_colorfgbg(value: str) -> bool | None:
    """COLORFGBG is "fg;bg" (sometimes "fg;default;bg"): the last field is
    the background's palette slot — 7 and 15 are the light backgrounds,
    every other slot is dark. Unset or unreadable: None."""
    slot = value.strip().rsplit(";", 1)[-1]
    if not slot.isdigit():
        return None
    return int(slot) not in (7, 15)


def _channel(part: bytes) -> float:
    """One OSC 11 color component ("1c1c", scale set by its width) → 0..1."""
    return int(part, 16) / (16 ** len(part) - 1)  # type: ignore[no-any-return]
