"""The `/help` page, laid out for a terminal.

The shared table (`backend.commands`) carries the commands and their
wording; this owns how they FIT: two columns when both hold a readable
description, one column when they would not, and a description that
wraps inside its own column instead of running off the edge. It also
spells a few rows shorter than the shared table does — the command
column is narrow here, where a web page's is not (`_SPELLINGS`), and
what a command takes is still shown in full by the completion menu.

Text in, text out: nothing here reaches the session, the screen, or a
backend call. The shortcut captions arrive as DATA — `bindings.SHORTCUTS`
passed in, the way the completion menu's key column gets them — so the
key table keeps its one home and this module has no way back into it.
"""

import textwrap
from collections.abc import Mapping

from otaku.backend import commands
from otaku.terminal.tty.cursor import terminal_width

# The terminal spells a few rows shorter than the shared table does: its
# command column is narrow where a web page's is not, so an argument
# shape that costs more than it explains is dropped here — and where
# dropping it would lose something, the description picks it up. The
# TABLE is untouched: this is one frontend's spelling, not the command.
_SPELLINGS: dict[str, tuple[str, str]] = {
    "/model": ("[SPEC]", "Switch model — SPEC is PROVIDER/MODEL; bare opens the picker"),
    "/set parameter": ("", ""),
    "/set verbose": ("", ""),
    "/set autocorrect": ("", ""),
    "/set notification": ("", ""),
    "/set max_context": ("", ""),
}

# The keys the prompt itself answers to — no command of their own, so
# the table has no row for them and the page ends with theirs.
# fmt: off
_KEY_ROWS: tuple[tuple[str, str], ...] = (
    ('"""', 'Begin a multiline message; close it with """'),
    ("@", "In a FILE argument: enable path autocompletion"),
    ("Up / Down", "Walk your recent prompt history"),
    ("Ctrl+C", "Clear the current line; cancel an in-flight reply"),
)
# fmt: on

_GAP = 5  # columns between the two columns
_INDENT = 2
_KEY_GAP = 2  # between the command column and the shortcut column
# A description column narrower than this reads worse than a tall single
# column, so the second column is not worth taking.
_MIN_DESCRIPTION = 26
# Nor is it worth taking for a sliver: the split must save at least this
# share of the height to be worth the wrapping it forces.
_TWO_COLUMN_SAVING = 0.1
# The group that opens the right column: the first about the app rather
# than the story it is played in. A fixed seam, so a group is found where
# it always is — and the two sides come out close at every width that
# takes two columns, where a seam chased by height left Import/export
# straddling the middle and the right column short.
_RIGHT_COLUMN_OPENS = "transfer"

# One block: its heading ("" for a group that names itself) and its rows,
# each a (label, shortcut caption, description).
_Block = tuple[str, list[tuple[str, str, str]]]


def text(shortcuts: Mapping[str, str], width: int | None = None) -> str:
    """The page: every command with its shortcut caption from
    `shortcuts` (token → caption) and its description, in as many
    columns as `width` holds — the measured terminal when None."""
    columns = terminal_width() if width is None else width
    blocks = _blocks(shortcuts)
    stacked = _stacked(_render(blocks, columns))
    half = (columns - _GAP) // 2
    if half - _widths(blocks)[2] < _MIN_DESCRIPTION:
        return "\n".join(stacked)
    second = _right_column(blocks)
    left = _stacked(_render(blocks[:second], half))
    right = _stacked(_render(blocks[second:], half))
    beside = _beside(left, right)
    # Two columns only where they actually save height: halving the
    # description width costs wrapped lines, and just above the floor
    # that costs back everything the split saves.
    if len(beside.splitlines()) > len(stacked) * (1 - _TWO_COLUMN_SAVING):
        return "\n".join(stacked)
    return beside


def _blocks(shortcuts: Mapping[str, str]) -> list[_Block]:
    """The groups in the table's order, this frontend's spellings
    applied, with the keys section last."""
    blocks: list[_Block] = []
    group = None
    for spec in commands.COMMANDS:
        if spec.group != group:
            group = spec.group
            # The label is the shared table's; the colon is this page's,
            # as every heading here wears one — including the keys
            # section below, which has no group of its own.
            blocks.append((f"{commands.GROUP_LABELS[group]}:", []))
            if group == commands.PROSE_GROUP:
                blocks[-1][1].append((commands.PROSE_LABEL, "", commands.PROSE_DESCRIPTION))
        args, description = spec.args, spec.description
        if spec.token in _SPELLINGS:
            args, replacement = _SPELLINGS[spec.token]
            description = replacement or description
        label = f"{spec.token} {args}".strip()
        blocks[-1][1].append((label, shortcuts.get(spec.token, ""), description))
    blocks.append(("Keys at the prompt:", [(key, "", about) for key, about in _KEY_ROWS]))
    return blocks


def _widths(blocks: list[_Block]) -> tuple[int, int, int]:
    """The command column, the shortcut column, and the two plus their
    padding — where a row's description begins."""
    rows = [row for _, block_rows in blocks for row in block_rows]
    label = max((len(label) for label, _, _ in rows), default=0)
    key = max((len(caption) for _, caption, _ in rows), default=0)
    return label, key, _INDENT + label + _KEY_GAP + key + 2


def _render(blocks: list[_Block], width: int) -> list[list[str]]:
    """Each block's lines at `width` columns — its own columns measured
    over these blocks alone, so a column packs to what it actually
    holds."""
    label_width, key_width, head = _widths(blocks)
    description_width = max(_MIN_DESCRIPTION, width - head)
    rendered = []
    for heading, rows in blocks:
        lines = [heading] if heading else []
        for label, caption, description in rows:
            lead = f"{'':<{_INDENT}}{label:<{label_width}}{'':<{_KEY_GAP}}{caption:<{key_width}}  "
            # Never at a hyphen: "mid-stream" and "in-flight" are words,
            # and a column is no reason to break one. A token too long
            # for the column still breaks — the layout's width is a
            # promise, and a value list is the only thing that long.
            wrapped = textwrap.wrap(description, description_width, break_on_hyphens=False) or [""]
            lines.append((lead + wrapped[0]).rstrip())
            lines.extend((" " * len(lead) + more).rstrip() for more in wrapped[1:])
        rendered.append(lines)
    return rendered


def _stacked(rendered: list[list[str]]) -> list[str]:
    """The blocks down one column, a blank line between them."""
    lines: list[str] = []
    for block in rendered:
        if lines:
            lines.append("")
        lines += block
    return lines


def _right_column(blocks: list[_Block]) -> int:
    """The block the right column starts at: the seam's own heading, so
    a group is never broken across the two."""
    heading = f"{commands.GROUP_LABELS[_RIGHT_COLUMN_OPENS]}:"
    return next(i for i, (head, _rows) in enumerate(blocks) if head == heading)


def _beside(left: list[str], right: list[str]) -> str:
    """Two columns side by side, the shorter one running out first."""
    width = max((len(line) for line in left), default=0)
    rows = []
    for i in range(max(len(left), len(right))):
        start = left[i] if i < len(left) else ""
        end = right[i] if i < len(right) else ""
        rows.append(f"{start:<{width}}{' ' * _GAP}{end}".rstrip())
    return "\n".join(rows)
