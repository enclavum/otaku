"""Model-facing text templates: configs/prompts.toml.

Every string otaku puts in front of a model is a template here, loaded once
into a `Prompts` value, in two groups. The `/me`, `/you`, and `/ooc`
commands write their template into a turn's `framing` verbatim, filling
only `{name}`; the `((OOC: …))` enclosure lives IN the template — the code
wraps nothing, so what you edit is exactly what the model sees. `{body}`,
where a template has it, marks where the turn's own text is slotted at wire
time. The lore templates build the memory — one scene (`extract_prompt`),
a character's rolled-up history, the story-so-far — and `recap_header` is
the line that carries the finished scene summaries back into the request.

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

# The big lore templates, named here so the _DEFAULTS table stays readable.

EXTRACT_DEFAULT = """\
You are a story analyst. Read the scene below — the latest exchange of an
interactive story — and extract its memory.

Known characters so far (use these exact names when referring to them):
{cast}

Character journals so far — their story to date; continue it, do not restart it:
{journals}

LANGUAGE: write every value you produce — the title, the summary, the entries,
the states — in the SAME LANGUAGE the scene below is written in. Do not
translate it, and do not answer in English because these instructions are in
English. Only the JSON keys stay in English.

Extract from THIS SCENE ONLY and reply with ONLY a JSON object, no prose, in this shape:
{{
  "scene": {{"title": "...",
             "summary": "a detailed narrative recap of the scene, 250-400 words"}},
  "speakers": [{{"n": 1, "speaker": "who speaks or acts in message [n], or null"}}],
  "characters": [{{"name": "...", "aliases": ["..."],
                   "description": "one line, or null"}}],
  "journals": [{{"character": "name",
                 "entry": "their own record of this scene",
                 "state": "their situation right now"}}]
}}

Rules:
- "summary": prose, chronological, written like a story recap — not a synopsis.
  This summary is the ONLY record the story keeps of this scene: once it scrolls
  out of the recent messages, nothing else about it reaches the model. Write it
  so someone who never read the scene could continue the story from it. Cover,
  in order: who is present and where; what each of them does and says that
  matters; every decision, promise, threat, or refusal, and who made it; what is
  revealed, and to whom; anything given, taken, shown, or hidden; how moods and
  relationships shift; and what is left unresolved. Quote a line verbatim when
  its exact wording matters.
- "speakers": for EVERY numbered message, the single character who speaks or acts
  in it (their exact name); null when it is narration, several characters, or out
  of character.
- "characters": only NEW characters first appearing in this scene.
- "journals": one for EVERY character who appears or acts in this scene.
  "entry" is that character's own record of THIS SCENE ONLY — what they did, saw,
  heard, and felt, in the order they experienced it. Up to ~250 words, in
  proportion to how much of the scene is theirs: a bystander gets a few lines,
  the character the scene turns on gets the full length. Write only what they
  witnessed or were told — a character does not know what happened while they
  were absent, and a secret kept from them is not in their entry. This entry is
  permanent and is never rewritten, so put everything of theirs into it now.
  "state" is a snapshot, not a history: 1-3 sentences — where they are, what they
  wear and carry, how they feel, what they want, right now.
- Lines marked ((OOC: …)) are the players talking out of character: never part of
  the scene's story, but decisions made there belong in the summary and journals.
- Every value stays in the scene's own language (see LANGUAGE above).
- Empty lists are fine. JSON only.

SCENE (numbered messages):
{chunk}
"""

HISTORY_DEFAULT = """\
Write {name}'s history: everything they know of the story so far, drawn from
their own journal entries below.

Rules:
- Chronological prose, past tense, about 300 words. No headings, no bullets.
- Compress the earliest entries hardest and keep the recent ones specific.
  Names, promises, debts, injuries, betrayals, and secrets survive compression;
  weather and scenery do not.
- Only what {name} witnessed or was told. Add nothing that is not below.
- Write in the SAME LANGUAGE as the entries below — do not translate them, and
  do not answer in English because these instructions are in English.
- Output the history only.

{name}'s journal, oldest entry first:
{entries}
"""

ARC_DEFAULT = """\
Combine the scene summaries below into one running "story so far" summary
(4-8 sentences, chronological, no headings). Output the summary only.

Write it in the SAME LANGUAGE as the summaries below — do not translate it,
and do not answer in English because these instructions are in English.

{summaries}
"""

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
    "extract_prompt": EXTRACT_DEFAULT,
    "history_prompt": HISTORY_DEFAULT,
    "arc_prompt": ARC_DEFAULT,
    "recap_header": "[The story so far — the scenes between these moments:]",
}

# Placeholders a template cannot do without: every one its built-in text
# uses. `{name}` is what the command is about, and `{body}` decides where
# the turn's own text sits relative to the ((OOC:)) enclosure — dropping
# either silently changes what the model is told.
_REQUIRED = {
    "me_framing": ("name", "body"),
    "you_framing": ("name",),
    "ooc_framing": ("body",),
    "extract_prompt": ("cast", "journals", "chunk"),
    "history_prompt": ("name", "entries"),
    "arc_prompt": ("summaries",),
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
    extract_prompt: str = _DEFAULTS["extract_prompt"]
    history_prompt: str = _DEFAULTS["history_prompt"]
    arc_prompt: str = _DEFAULTS["arc_prompt"]
    recap_header: str = _DEFAULTS["recap_header"]


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
