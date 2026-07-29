"""Import and export: one story as one file, both directions.

The package owns the story document so the round-trip is a law:
`imports.parse_story(exports.render_story(x))` returns `x` exactly. The
document is one Markdown file, readable as prose and parseable as data —
a metadata comment (recognition + versions), the optional `# title`
heading, a `## Story` section (story so far, system, cast), `## Scenes`
(span, summary, per-character journals), and `## Messages` — one
`### n · role (kind) · speaker · "framing"` header per message, the kind,
speaker, and JSON-quoted framing present only when they exist, with the
verbatim body starting on the very next line. Empty parts are simply
absent, an untitled story has no heading, and message bodies keep their
interior blank lines.

Importing accepts exactly two shapes: this document and a SillyTavern
chat (.jsonl), both parsed into the same `StoryExport`. Prose files go
through `imports.split_segments` instead. The writers only put records in
the store — building the missing memory is the extraction pass's job,
which the import command triggers exactly like `/extract`.
"""

from dataclasses import dataclass

# Bumped when the layout changes in a way an importer must know about —
# recorded in every file's metadata block so a reader can dispatch on it.
EXPORT_FORMAT_VERSION = 1

# What recognizes the document — the first line of its metadata block.
EXPORT_MARKER = "<!-- otaku export"


@dataclass(frozen=True)
class ExportedCharacter:
    name: str
    aliases: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class ExportedJournal:
    character: str
    entry: str = ""
    state: str = ""
    history: str = ""


@dataclass(frozen=True)
class ExportedScene:
    title: str = ""
    span: tuple[int, int] | None = None  # (first, last) message ordinal, 1-based
    summary: str = ""
    journals: tuple[ExportedJournal, ...] = ()


@dataclass(frozen=True)
class ExportedMessage:
    role: str  # 'user' | 'assistant'
    body: str
    kind: str = "dialogue"
    speaker: str | None = None
    framing: str | None = None


@dataclass(frozen=True)
class StoryExport:
    """One story's transferable whole — what the document holds."""

    title: str = ""
    system: str = ""
    story_so_far: str = ""
    cast: tuple[ExportedCharacter, ...] = ()
    scenes: tuple[ExportedScene, ...] = ()
    messages: tuple[ExportedMessage, ...] = ()
