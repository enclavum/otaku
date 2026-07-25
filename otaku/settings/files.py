"""Shared helpers for the TOML files otaku writes."""

from pathlib import Path

# Written config lines align their comments to one column, so the values read
# as a column instead of a wall of prose.
COMMENT_COLUMN = 30


def toml_key(name: str) -> str:
    """A TOML key or table header: bare when it can be, quoted otherwise —
    model names carry dots and colons, which TOML reads as nested tables."""
    if name and all(c.isalnum() or c in "_-" for c in name):
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def toml_scalar(value: object) -> str:
    """One TOML value. otaku writes only strings, numbers, and booleans."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{text}"'


def row(setting: str, comment: str) -> str:
    """One config line with its comment aligned to COMMENT_COLUMN. A setting
    longer than the column still gets two spaces before the `#`."""
    if not comment:
        return setting
    if len(setting) < COMMENT_COLUMN:
        return f"{setting:<{COMMENT_COLUMN}}# {comment}"
    return f"{setting}  # {comment}"


def write_atomic(path: Path, text: str) -> None:
    """Write via tmp + rename, so a kill mid-write can never truncate the
    file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
