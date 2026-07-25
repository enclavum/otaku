"""Model-facing text templates: configs/prompts.toml.

Every string otaku puts in front of a model is a template here, loaded once
into a `Prompts` value. The `/me`, `/you`, and `/ooc` commands write their
template into a turn's `framing` verbatim, filling only `{name}`; the
`((OOC: …))` enclosure lives IN the template — the code wraps nothing, so
what you edit is exactly what the model sees. `{body}`, where a template
has it, marks where the turn's own text is slotted at wire time.

The stub is written on first use with every template active; once the file
exists it is the source — edit a value to change it, delete the file to
regenerate the release defaults. A key absent from the file falls back to
the built-in, and a malformed file (or a template missing a required
placeholder) is reported once and ignored — a bad override must never cost
a session.
"""

import sys
import tomllib
from dataclasses import dataclass, fields

from otaku.paths import Paths
from otaku.settings.files import write_atomic

_DEFAULTS = {
    "me_framing": "((OOC: The user writes as {name}.))\n{body}",
    "you_framing": (
        "((OOC: You play {name} in an interactive story. Respond only as {name} — "
        "their words, actions, and perceptions, consistent with the story so far. "
        "Never speak, act, or decide for any other character.))"
    ),
    "ooc_framing": (
        "((OOC: {body}\n\nAnswer briefly out of character, as a co-author planning "
        "the story — do not continue the scene or write any prose.))"
    ),
}

# Placeholders a template cannot do without: every one its built-in text
# uses. `{name}` is what the command is about, and `{body}` decides where
# the turn's own text sits relative to the ((OOC:)) enclosure — dropping
# either silently changes what the model is told.
_REQUIRED = {
    "me_framing": ("name", "body"),
    "you_framing": ("name",),
    "ooc_framing": ("body",),
}

_HEADER = [
    "# otaku prompt templates — every prompt otaku sends, active and editable.",
    "# Edit a value to change it; delete this file to regenerate it with the",
    "# current release's built-in defaults. Placeholders in {braces} are",
    "# required — a template missing one is reported and ignored.",
]


@dataclass(frozen=True)
class Prompts:
    me_framing: str = _DEFAULTS["me_framing"]
    you_framing: str = _DEFAULTS["you_framing"]
    ooc_framing: str = _DEFAULTS["ooc_framing"]


def load(paths: Paths) -> Prompts:
    """The templates, with the file's overrides applied over the built-ins."""
    path = paths.prompts_file
    if not path.exists():
        return Prompts()
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        print(f"otaku: ignoring {path} ({e})", file=sys.stderr)
        return Prompts()
    known = {f.name for f in fields(Prompts)}
    unknown = sorted(set(raw) - known)
    if unknown:
        # A key otaku no longer reads (or a typo) would otherwise sit there
        # looking active while doing nothing — say so once.
        keys = ", ".join(unknown)
        print(f"otaku: {path}: ignoring unknown prompt key(s): {keys}", file=sys.stderr)
    overrides: dict[str, str] = {}
    for key in known:
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            print(f"otaku: {path}: {key} must be a string — ignored", file=sys.stderr)
            continue
        missing = [p for p in _REQUIRED.get(key, ()) if "{" + p + "}" not in value]
        if missing:
            placeholders = ", ".join("{" + p + "}" for p in missing)
            print(f"otaku: {path}: {key} is missing {placeholders} — ignored", file=sys.stderr)
            continue
        overrides[key] = value
    return Prompts(**overrides)


def write_stub(paths: Paths) -> bool:
    """Write the first-run file — every template active, round-trip exact.
    Returns True when it wrote; an existing file is never overwritten."""
    path = paths.prompts_file
    if path.exists():
        return False
    lines = [*_HEADER, ""]
    for key, value in _DEFAULTS.items():
        lines.append(f"{key} = {_toml_string(value)}")
        lines.append("")
    write_atomic(path, "\n".join(lines))
    return True


def _toml_string(value: str) -> str:
    """A TOML string literal that parses back byte-for-byte. A clean single
    line is a single-quoted literal; anything with a newline or an
    apostrophe uses a triple-single literal, whose newline right after the
    opening TOML trims."""
    if "\n" not in value and "'" not in value:
        return f"'{value}'"
    return f"'''\n{value}'''"
