"""Pictures attached from the prompt: `@path` anywhere in a played line.

The terminal's affordance, resolved on the terminal's side the way
`/system FILE` is. A token that opens with `@` after whitespace (or the
line's start) and names an existing file with a picture's extension is
an attachment: it leaves the line, one adjacent space with it, and its
bytes go with the turn. A token that names nothing stays as typed —
"@Mara" is prose. The token runs to the next whitespace, a space inside
a path escaped `\\ `, which is what the completion menu inserts. The
stored body never holds the token. Whether the model can see is the
backend's decision (`play.submit` refuses when it cannot); this module
only reads files. In a COMMAND line `@` keeps its old meaning, a
completion trigger, and this module is never asked.
"""

import re
from collections.abc import Callable
from pathlib import Path

from otaku.backend.files import RawFile

SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"})
# An `@` that opens a token (nothing but whitespace before it), then the
# path: to the next whitespace, an escaped space riding along.
_TOKEN = re.compile(r"(?<!\S)@((?:\\ |\S)*)")


def extract_pictures(
    line: str, read: Callable[[str], RawFile | None] = lambda path: _read(path)
) -> tuple[str, list[RawFile]]:
    """The line with its picture tokens taken out, and the pictures they
    named, read. The rule: every `@` token whose path `read` answers for
    — unescaped, `~` as typed — leaves the line, one adjacent space with
    it (the following one, else the preceding); a token it declines stays
    exactly as typed; the pictures come back in the order they stood.
    `read` is the filesystem's answer by default, an existing file with a
    picture's extension as bytes and its name, and the unit suite's
    seam."""
    files: list[RawFile] = []
    out = line
    for match in reversed(list(_TOKEN.finditer(line))):
        path = unescape(match.group(1))
        file = read(path) if path else None
        if file is None:
            continue
        files.insert(0, file)
        start, end = match.span()
        if end < len(out) and out[end] == " ":
            end += 1
        elif start > 0 and out[start - 1] == " ":
            start -= 1
        out = out[:start] + out[end:]
    return out, files


def token_at(text_before_cursor: str) -> str | None:
    """The path part of an `@` token being typed at the cursor, still
    escaped — "" right after the `@` — or None when the cursor is not
    in one."""
    if text_before_cursor.endswith((" ", "\t")):
        return None
    tokens = list(_TOKEN.finditer(text_before_cursor))
    if not tokens or tokens[-1].end() != len(text_before_cursor):
        return None
    return tokens[-1].group(1)


def escape(path: str) -> str:
    """A path as a token carries it: every space escaped."""
    return path.replace(" ", "\\ ")


def unescape(token: str) -> str:
    return token.replace("\\ ", " ")


def _read(path: str) -> RawFile | None:
    """The filesystem's answer: an existing file with a picture's
    extension, `~` expanded, as the bytes and the name; None otherwise."""
    candidate = Path(path).expanduser()
    if candidate.suffix.lower() not in SUFFIXES or not candidate.is_file():
        return None
    return RawFile(candidate.read_bytes(), candidate.name)
