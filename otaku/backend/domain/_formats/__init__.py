"""The story document and the foreign formats — every wild file otaku
reads is decoded in this package: the export document, SillyTavern
chats, plain text, and character-card files (`cards`).

This package owns the story document, and the round-trip is a law:
`imports.parse_story(exports.render_story(x))` returns `x` exactly. The
reader reads every format version ever written; a newer declared format
is refused with directions, never guessed at.

The document is one Markdown file, readable as prose and parseable as
data — a metadata comment (recognition + versions), the optional
`# title` heading, a `## Story` section (story so far, system, cast — an
imported card's archive TOML as a `#### Name` block under the roster),
`## Scenes` (span, summary, per-character journals), and `## Messages` —
one `### n · role (kind) · speaker · "template"` header per message, the
kind, speaker, and JSON-quoted template present only when they exist,
with the verbatim body starting on the very next line. Empty parts are
simply absent, an untitled story has no heading, and message bodies keep
their interior blank lines. Body text is structure-proof: a content line
that would read as a heading (or as this escape itself) is written with
one extra leading backslash, and the parser strips exactly one — so
bodies, summaries, and the rest survive byte-exact whatever Markdown
they hold.

This module holds the data model every reader and writer shares and the
two facts that recognize a file; the parsers are the sibling modules
(`exports`, `imports`, `sillytavern`, `plaintext`, `cards`)."""

from __future__ import annotations

from dataclasses import dataclass

# Bumped when the layout changes in a way an older reader would misread.
#  2: `card` message kind; cast carries card archives
#  3: each scene carries its own **History:** (the arc through it) — a
#     v2 reader would fold the field line into the summary, so the
#     version fences it out.
EXPORT_FORMAT_VERSION = 3

# What recognizes the document — the first line of its metadata block.
EXPORT_MARKER = "<!-- otaku export"


# The classes follow the DDL's table order (schema.py: stories,
# messages, scenes, characters, journals), and each one's fields follow
# its table's columns — the ids and audit columns absent because the
# document has neither: names are its keys, nesting its foreign keys.
# ExportedStory's collections stay in DOCUMENT order (cast, scenes,
# messages), the file's own layout.


@dataclass(frozen=True)
class ExportedStory:
    """One story's transferable whole — what the document holds."""

    title: str = ""
    system: str = ""
    cast: tuple[ExportedCharacter, ...] = ()
    scenes: tuple[ExportedScene, ...] = ()
    messages: tuple[ExportedMessage, ...] = ()


@dataclass(frozen=True)
class ExportedMessage:
    # DDL order puts kind and speaker before body, but a dataclass
    # cannot: they carry defaults and body does not.
    role: str  # 'user' | 'assistant'
    body: str
    kind: str = "dialogue"
    speaker: str | None = None
    template: str | None = None


@dataclass(frozen=True)
class ExportedScene:
    span: tuple[int, int] | None = None  # (first, last) message ordinal, 1-based
    title: str = ""
    summary: str = ""
    history: str = ""  # the story so far through this scene
    journals: tuple[ExportedJournal, ...] = ()


@dataclass(frozen=True)
class ExportedCharacter:
    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    card: str = ""  # the /card archive TOML


@dataclass(frozen=True)
class ExportedJournal:
    character: str
    entry: str = ""
    state: str = ""
    history: str = ""
