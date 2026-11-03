"""Model-facing text templates: prompts.toml.

Every string otaku puts in front of a model is a template here, loaded
once into a `Prompts` value. The direction commands write their template
into a turn's `template` verbatim — nothing is filled at write time, so
the turn keeps the wording this file had when it played; `{name}` and
`{body}` mark where the turn's own name and text slot in at wire time.
The lore templates build the memory; `recap_header` carries the finished
scene summaries back into the request, and `pinned_header` opens the
pinned messages quoted among them; a tool's prompt is sent while the
tool is switched on, never stored — and is the one text edited from
inside the app (`set_prompt`).

The built-in texts are files in `templates/`, one per key, read once at
import (`DEFAULTS`), each named for its key with the type first
(`file_of`): `card_framing` is `framing_card.txt`. A template is its
file's text but for the one final newline an editor leaves, so one that
ends in a newline of its own ends its file with a blank line.
`migrations` carries a changed built-in to the files still holding the
old one.

The stub is written on first use with every template active; once the
file exists it is the source — edit a value to change it, delete the
file to regenerate the release defaults. A key absent from the file
falls back to the built-in, and a malformed file or template is IGNORED
with a returned warning — a bad override must never cost a session, and
this module never prints (the launch folds warnings into the app's
notices).
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType

from otaku.formatting import toml_string
from otaku.settings import commit, read_settings, write_atomic

_HEADER = [
    "# otaku prompt templates — every prompt otaku sends, active and editable.",
    "# Edit a value to change it; delete this file to regenerate it with the",
    "# current release's built-in defaults. Placeholders in {braces} are",
    "# required — a template missing one is reported and ignored.",
]


def file_of(key: str) -> str:
    """The stem of `key`'s file: the key with its type — the word after
    its last underscore, `framing`, `prompt` or `header` — moved first."""
    name, _, kind = key.rpartition("_")
    return f"{kind}_{name}"


def read_template(path: Traversable) -> str:
    """A template as its file holds it: the text but for the one final
    newline an editor leaves."""
    return path.read_text(encoding="utf-8").removesuffix("\n")


def template(key: str, *required: str) -> str:
    """A field of `Prompts`, before it so the class can call it: `key`'s
    built-in text, read from its file, and the placeholders a template
    for it cannot do without (`load`), the key itself beside them."""
    built_in = read_template(files(__name__) / "templates" / f"{file_of(key)}.txt")
    return field(default=built_in, metadata={"key": key, "required": required})


@dataclass(frozen=True)
class Prompts:
    """Every template, in the order the stub writes them: its built-in
    text, and the placeholders an override cannot drop — every one the
    built-in uses, unless a comment says otherwise. `{name}` is what the
    command is about, and `{body}` decides where the turn's own text sits
    relative to the ((OOC:)) enclosure: dropping either silently changes
    what the model is told."""

    me_framing: str = template("me_framing", "name", "body")
    you_framing: str = template("you_framing", "name")
    ooc_framing: str = template("ooc_framing", "body")
    # {dice} is the roll itself — without it the numbers never reach the
    # model and the command is a no-op wearing a frame.
    roll_framing: str = template("roll_framing", "dice", "body")
    # A chapter's start, as the model reads the row that opens one: out of
    # character, so it is read as the story's structure and never as a line
    # to answer; its title (`{body}`), unnumbered — without it the model
    # reads a heading naming nothing.
    chapter_framing: str = template("chapter_framing", "body")
    # A card's block, a line per field — a line whose fields are all empty is
    # left out (`context.cards.card_to_wire`), so the block's close stands on
    # a line of its own. Only {name} is required: a line whose OTHER
    # placeholders are absent is a choice — omitting {examples} is how a
    # reader keeps examples off the wire.
    card_framing: str = template("card_framing", "name")
    # The text wire's standing instruction, before the premise in the turn
    # that precedes the story: the model turn must follow SOME turn — alone
    # it degenerates — and a premise may be empty; without {premise} the
    # story's own text never reaches the model over the text wire.
    story_framing: str = template("story_framing", "premise")
    extract_prompt: str = template("extract_prompt", "cast", "journals", "chunk")
    # The story-so-far rollup over the scene summaries: the narrator's
    # ledger, third person like the summaries it combines.
    scene_history_prompt: str = template("scene_history_prompt", "summaries")
    # A character's rollup over their own journal: their memory, so it keeps
    # the entries' first-person voice.
    journal_history_prompt: str = template("journal_history_prompt", "name", "entries")
    # A codex card written from its pictures, every one riding the request;
    # `{name}` the card's name, empty while it has none of its own yet, and
    # not required: a template may leave the naming to the model.
    card_from_picture_prompt: str = template("card_from_picture_prompt")
    # The line over the recap's summaries — the story so far, the scene
    # summaries — that stand in for the middle of the story (`context.assembler`).
    recap_header: str = template("recap_header")
    # The line over the pinned messages the recap quotes after a summary.
    pinned_header: str = template("pinned_header")
    # The tools' prompts: what tells the model how a tool is used
    # (`backend.story.tools`). Sent as an injection while the tool is switched
    # on for the story — bare in the system message, inside the OOC enclosure
    # in chat — so neither carries an enclosure of its own.
    tool_questions_prompt: str = template("tool_questions_prompt")
    tool_assistant_notes_prompt: str = template("tool_assistant_notes_prompt")
    tool_system_info_prompt: str = template("tool_system_info_prompt")
    tool_web_search_prompt: str = template("tool_web_search_prompt")
    # What a tool's RESULT is framed as, as the model reads it: the tool
    # turn's text, out of character, the tool named — without {body} the
    # model reads a label and nothing more.
    tool_framing: str = template("tool_framing", "body")
    # The canvas over the chat wire: the story is one text the model goes on
    # with, the instruct model told to behave as a text completion would
    # (`StorySettingEmulateTextCompletion`).
    emulate_text_completion_prompt: str = template("emulate_text_completion_prompt")


# Every built-in template, by key, in the order the stub writes them.
DEFAULTS: Mapping[str, str] = MappingProxyType({f.name: str(f.default) for f in fields(Prompts)})


def load(path: Path) -> tuple[Prompts, list[str]]:
    """The templates, with the file's overrides applied over the
    built-ins, plus the warnings to show — a malformed file, an unknown
    key, a template missing a required placeholder (ignored, each with
    its sentence)."""
    if not path.exists():
        return Prompts(), []
    warnings: list[str] = []
    try:
        raw = tomllib.loads(read_settings(path))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return Prompts(), [f"Ignoring {path.name} ({e})."]
    required = {f.name: f.metadata["required"] for f in fields(Prompts)}
    unknown = sorted(set(raw) - set(required))
    if unknown:
        # A key otaku no longer reads (or a typo) would otherwise sit
        # there looking active while doing nothing — say so once.
        warnings.append(f"{path.name}: ignoring unknown prompt key(s): {', '.join(unknown)}.")
    overrides: dict[str, str] = {}
    for key, placeholders in required.items():
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            warnings.append(f"{path.name}: {key} must be a string — ignored.")
            continue
        missing = [p for p in placeholders if "{" + p + "}" not in value]
        if missing:
            placeholders = ", ".join("{" + p + "}" for p in missing)
            warnings.append(f"{path.name}: {key} is missing {placeholders} — ignored.")
            continue
        overrides[key] = value
    return Prompts(**overrides), warnings


def write_stub(path: Path) -> bool:
    """Write the first-run file — every template active, round-trip
    exact. True when it wrote; an existing file is never overwritten."""
    if path.exists():
        return False
    lines = [*_HEADER, ""]
    for key, value in DEFAULTS.items():
        lines.append(f"{key} = {toml_string(value)}")
        lines.append("")
    write_atomic(path, "\n".join(lines))
    return True


def set_prompt(path: Path, backups_dir: Path, key: str, text: str) -> bool:
    """Write one prompt into the file — the in-app edit: the block
    `key = '''…'''` replaced whole, or appended when the file has no
    such key; an empty `text` DROPS the key, so the file reads as the
    built-in again. The pre-edit file is kept as a dated backup first,
    as every settings edit is (`settings.commit`); a file not there yet
    is simply written. True once the file holds it; False when it could
    not be read or written."""
    try:
        before = read_settings(path) if path.exists() else None
    except OSError:
        return False
    after = with_prompt(before or "", key, text)
    if before == after:
        return True
    if before is None:
        try:
            write_atomic(path, after)
        except OSError:
            return False
        return True
    return commit(path, backups_dir, before, after)


def with_prompt(text: str, key: str, value: str) -> str:
    """The file's text with `key`'s block set to `value` — replaced,
    appended, or dropped for an empty value: `set_prompt`'s pure half.
    Line-wise, tracking the `'''` literals `toml_string` writes, so a
    prompt BODY that spells `key = ` at the head of a line is never
    taken for the key."""
    out: list[str] = []
    inside = False  # …a triple-quoted value, where keys cannot begin
    dropping = False  # …the old block, being left out
    found = False
    for line in text.split("\n"):
        opens = line.count("'''") % 2 == 1
        if dropping:
            dropping = not opens
            continue
        if not inside and line.startswith(f"{key} = "):
            found = True
            if value:
                out.append(f"{key} = {toml_string(value)}")
            dropping = opens
            continue
        if opens:
            inside = not inside
        out.append(line)
    if value and not found:
        if out and out[-1]:
            out.append("")
        out += [f"{key} = {toml_string(value)}", ""]
    return "\n".join(out)
