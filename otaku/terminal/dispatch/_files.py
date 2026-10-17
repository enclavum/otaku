"""A typed path, read on this side: the file a command's argument names
— `~` expanded, a leading `@` already stripped by the command — or none,
and a `FILE [NAME]` argument split by what exists."""

from pathlib import Path


def existing_file(text: str) -> Path | None:
    """The file `text` names, or None when it names none — including
    when the filesystem REFUSES to answer: a command argument is text
    until proven a path, and the proof is a question the OS can decline
    (a component over 255 bytes raises ENAMETOOLONG instead of returning
    false). Anything the lookup itself raises means the same thing
    here — not a file. Read by `/system`, `/card` and `/export`."""
    try:
        candidate = Path(text).expanduser()
        return candidate if candidate.is_file() else None
    except (OSError, ValueError, RuntimeError):
        return None


def file_and_name(raw: str) -> tuple[Path, str]:
    """`FILE [NAME]` split by what EXISTS: the longest leading run of
    tokens naming a real file is the file, the rest is the rename — so
    both a path with spaces and a multi-word name read correctly. Nothing
    matching falls through whole, for `read_bytes` to refuse honestly."""
    tokens = raw.split()
    for i in range(len(tokens), 0, -1):
        candidate = existing_file(" ".join(tokens[:i]))
        if candidate is not None:
            return candidate, " ".join(tokens[i:]).strip()
    return Path(raw).expanduser(), ""
