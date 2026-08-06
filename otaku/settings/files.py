"""Shared helpers for the TOML files otaku writes."""

from pathlib import Path

# Written config lines align their comments to one column, so the values read
# as a column instead of a wall of prose.
_COMMENT_COLUMN = 30

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


def toml_key(name: str) -> str:
    """A TOML key or table header: bare when it can be, quoted otherwise —
    model names carry dots and colons, which TOML reads as nested tables.
    The quoted form escapes like `toml_scalar`: keys are values too (a
    model name heads its models.toml table), and one raw control byte
    there would unparse the whole file."""
    if name and all(c.isalnum() or c in "_-" for c in name):
        return name
    return '"' + _escaped(name) + '"'


def toml_scalar(value: object) -> str:
    """One TOML value. otaku writes only strings, numbers, and booleans.
    Every control character a string carries is escaped per the TOML
    spec — no value (a server-reported model name, say) can render a
    file that fails to parse back."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    return '"' + _escaped(str(value)) + '"'


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


def row(setting: str, comment: str) -> str:
    """One config line with its comment aligned to _COMMENT_COLUMN. A setting
    longer than the column still gets two spaces before the `#`."""
    if not comment:
        return setting
    if len(setting) < _COMMENT_COLUMN:
        return f"{setting:<{_COMMENT_COLUMN}}# {comment}"
    return f"{setting}  # {comment}"


def write_atomic(path: Path, text: str) -> None:
    """Write via tmp + rename, so a kill mid-write can never truncate the
    file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
