"""The banner: pixel-art mark beside three lines of the session's own.

Two vertical pixels per character cell, so a 16x12 sprite fits in 6
terminal rows next to six lines of text. In colour each cell is a `▀`
with a background behind it; without colour it is the SAME sprite as ink
and paper — a half block per lit pixel — so the mark is one drawing at
one size wherever it appears, and a piped or NO_COLOR session gets the
picture rather than a substitute for it.
"""

import os
import shutil
import sys
from dataclasses import dataclass

from otaku.console import BOLD, DEFAULT_BG, DIM, MARGIN, RESET
from otaku.formatting import drawn_width, format_context


def _fg(color: int) -> str:
    """SGR 256-color foreground. The sprite is the one thing that paints by
    palette INDEX rather than by a theme role: it is a fixed picture, not
    part of the interface, so its colors are its own."""
    return f"\x1b[38;5;{color}m"


def _bg(color: int) -> str:
    """SGR 256-color background — the lower half of a sprite cell."""
    return f"\x1b[48;5;{color}m"


# A girl with long violet hair — the face reads at 16x12 because the eyes
# get two cells each (dark iris + a white shine pixel).
_SPRITE = [
    "....hhhhhhhh....",
    "..hhhhhhhhhhhh..",
    ".hhhhhhhhhhhhhh.",
    ".hhhsssssssshhh.",
    ".hhssssssssssbh.",
    ".hhseessseesshh.",
    ".hhsewsssewsshh.",
    ".hhssssmssssshh.",
    "..hhsssssssshh..",
    "...hhhhhhhhhh...",
    "....cccccccc....",
    "...cccccccccc...",
]
# 256-colour: h violet hair, s skin, e iris, w shine, m mouth, b blush,
# c collar.
_PALETTE = {"h": 140, "s": 223, "e": 236, "w": 231, "m": 167, "b": 217, "c": 60, ".": None}

# The same sprite with one colour to spend. The DARK half of the palette
# is ink (hair, iris, mouth, collar) and the light half is paper (skin,
# shine, blush): a silhouette of the whole head would be a blob, and it
# is the face that has to survive.
_INK = frozenset("hemc")

# A cell is two pixels, and with no colour each is only lit or not.
_BLOCKS = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}


@dataclass(frozen=True)
class SessionFacts:
    """What a chat banner states, as its caller reads them off the
    session it is opening — arriving ready to print: the banner draws
    them and nothing else, asks no store and no provider anything, and
    cuts nothing (a display width is the frontend's decision, made
    where the facts are read)."""

    version: str
    model: str  # "(no model)" when none
    engine: str  # the kind of server behind it; "" when none
    max_context: int | None  # the context the model gets, when a LOCAL engine answers
    story: str  # the story's name, cut by the caller; "" when it has none


@dataclass(frozen=True)
class _Style:
    """The banner's escape codes — or empty strings when colour is off."""

    accent: str = ""
    bold: str = ""
    dim: str = ""
    gray: str = ""
    rule: str = ""
    reset: str = ""


_COLOUR = _Style(
    accent=_fg(180),
    bold=BOLD,
    dim=DIM,
    gray=_fg(242),
    # Dimmed, not a grey: a fixed near-black read at 9.7:1 on a white
    # terminal and 2.2:1 on a black one, where the rule all but vanished.
    # Reduced intensity is derived from the text color, so it holds on
    # either — the same reason the pickers dim instead of recoloring.
    rule=DIM,
    reset=RESET,
)
_PLAIN = _Style()


def render_terminal(facts: SessionFacts) -> str:
    """The banner a chat session opens with. Its three lines are what
    that session IS: the story being played, on what model, through what
    engine."""
    style = _style()
    details = [f"{style.gray}{facts.engine}{style.reset}" if facts.engine else ""]
    if facts.max_context:
        details.append(f"{style.gray}{format_context(facts.max_context)} context{style.reset}")
    return _render(
        facts.version,
        [
            f"{style.gray}{facts.story}{style.reset}"
            if facts.story
            else f"{style.dim}/help for commands{style.reset}",
            f"{style.accent}{facts.model}{style.reset}",
            f"{style.dim} · {style.reset}".join(part for part in details if part),
        ],
    )


def render_web(version: str, url: str, *, full: bool = True) -> str:
    """What a served session opens with, in the two sizes a terminal
    wants it. Its three lines are the one thing that terminal is for:
    the address, how to open it, and how to stop serving. What the chat
    banner says (model, story) is on the page itself, so it would only
    be said twice.

    FULL draws the mark above them — `otaku web`, opening a terminal
    that has nothing else on it. Without it, the three lines alone under
    a rule of their own width: `/web` prints into a chat already on
    screen, where the mark was drawn when the session opened, and drawing
    it again would say the session had started twice when it has only
    changed medium. That rule is as wide as the longest line it closes
    and no wider — it parts the serving from the story above it, and a
    rule reaching past what it parts is underlining the screen — and the
    block stands in the column the request tail below it uses, so the
    address, the rule and the requests read as one thing."""
    style = _style()
    # Most terminals want a modifier with the click, and which one is
    # the platform's business — ⌘ on a Mac, ctrl everywhere else.
    click = "⌘" if sys.platform == "darwin" else "ctrl"
    lines = [
        f"{style.dim}web ui is available on:{style.reset} {style.accent}{url}{style.reset}",
        f"{style.dim}{click}-click to open / paste it in your browser{style.reset}",
        f"{style.dim}ctrl+c to stop{style.reset}",
    ]
    if full:
        return _render(version, lines)
    width = max(drawn_width(line) for line in lines)
    rule = f"{style.rule}{'─' * width}{style.reset}"
    return "\n".join(f"{' ' * MARGIN}{line}" for line in [*lines, rule])


def _render(version: str, lines: list[str]) -> str:
    """One banner: the mark, the two lines every banner opens with, a
    blank, and the three its caller filled in — closed by the rule that
    separates it from whatever is printed under it. A line past the
    bottom of the mark keeps its column rather than being dropped: a
    banner that says one thing less in a pipe than on a screen is a
    banner nobody can trust."""
    style = _style()
    rows = _sprite_rows()
    beside = " " * len(_SPRITE[0])
    beginning = [
        f"{style.accent}{style.bold}otaku{style.reset} {style.dim}v{version}{style.reset}",
        f"{style.dim}a roleplay client{style.reset}",
        "",
    ]
    said = beginning + lines
    out = [""]
    for i in range(max(len(rows), len(said))):
        sprite_row = rows[i] if i < len(rows) else beside
        text = said[i] if i < len(said) else ""
        out.append(f"{' ' * MARGIN}{sprite_row}   {text}".rstrip())
    # The rule stops short of a wide terminal: it closes the banner, it
    # does not underline the screen.
    width = min(shutil.get_terminal_size((80, 24)).columns, 72)
    out.append(f"{' ' * MARGIN}{style.rule}{'─' * width}{style.reset}")
    return "\n".join(out)


def _sprite_rows() -> list[str]:
    """The sprite as terminal rows — each row packs two sprite lines into
    one cell using a half-block glyph, painted where there is colour to
    paint with and cut out of ink and paper where there is not."""
    plain = not _colour()
    rows: list[str] = []
    for y in range(0, len(_SPRITE), 2):
        top = _SPRITE[y]
        bottom = _SPRITE[y + 1] if y + 1 < len(_SPRITE) else "." * len(top)
        row = ""
        for x in range(len(top)):
            if plain:
                row += _BLOCKS[(top[x] in _INK, bottom[x] in _INK)]
                continue
            upper, lower = _PALETTE.get(top[x]), _PALETTE.get(bottom[x])
            if upper is None:
                row += RESET + " " if lower is None else f"{_fg(lower)}{DEFAULT_BG}▄"
            elif lower is None:
                row += f"{_fg(upper)}{DEFAULT_BG}▀"
            else:
                row += f"{_fg(upper)}{_bg(lower)}▀"
        rows.append(row if plain else row + RESET)
    return rows


def _style() -> _Style:
    """The escape codes, or none — settled once per banner."""
    return _COLOUR if _colour() else _PLAIN


def _colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty() or os.environ.get("OTAKU_COLOR") == "1"
