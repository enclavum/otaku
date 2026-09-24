"""Model-facing text templates: prompts.toml.

Every string otaku puts in front of a model is a template here, loaded
once into a `Prompts` value. The direction commands write their template
into a turn's `template` verbatim — nothing is filled at write time, so
the turn keeps the wording this file had when it played; `{name}` and
`{body}` mark where the turn's own name and text slot in at wire time.
The lore templates build the memory; `recap_header` carries the finished
scene summaries back into the request; a tool's prompt is sent while
the tool is switched on, never stored — and is the one text edited from
inside the app (`set_prompt`).

The stub is written on first use with every template active; once the
file exists it is the source — edit a value to change it, delete the
file to regenerate the release defaults. A key absent from the file
falls back to the built-in, and a malformed file or template is IGNORED
with a returned warning — a bad override must never cost a session, and
this module never prints (the launch folds warnings into the session's
notices).
"""

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from otaku.formatting import toml_string
from otaku.settings import commit, read_settings, write_atomic

# The big lore templates, named here so the _DEFAULTS table stays readable.

EXTRACT_DEFAULT = """\
You are a story analyst. Read the scene below — the latest exchange of an
interactive story — and extract its memory.

Known characters so far (use these exact names when referring to them):
{cast}

Character journals so far — their story to date; continue it, do not restart it:
{journals}

LANGUAGE: write every value you produce — the title, the summary, the entries,
the states — in the SAME LANGUAGE the scene below is written in: an English
scene gets English values, a French scene French ones. Match the scene, not
these instructions. Only the JSON keys stay in English.

Extract from THIS SCENE ONLY and reply with ONLY a JSON object, no prose, in this shape:
{
  "scene": {"title": "...",
             "summary": "a detailed narrative recap of the scene, 250-400 words"},
  "speakers": [{"n": 1, "speaker": "who speaks or acts in message [n], or null"}],
  "characters": [{"name": "...", "aliases": ["..."],
                   "description": "one line, or null"}],
  "journals": [{"character": "name",
                 "entry": "their own record of this scene",
                 "state": "their situation right now"}]
}

Rules:
- Reply with ONE flat JSON object: "scene", "speakers", "characters" and
  "journals" are ALL top-level keys of it — never put "characters" or
  "journals" inside "scene".
- "summary": prose, chronological, written like a story recap — not a synopsis.
  This summary is the ONLY record the story keeps of this scene: once it scrolls
  out of the recent messages, nothing else about it reaches the model. Write it
  so someone who never read the scene could continue the story from it. Cover,
  in order: who is present and where; what each of them does and says that
  matters; every decision, promise, threat, or refusal, and who made it; what is
  revealed, and to whom; anything given, taken, shown, or hidden; how moods and
  relationships shift; and what is left unresolved. Quote a line verbatim when
  its exact wording matters. Stay inside 250-400 words — past that the recap
  stops being memory and starts crowding the story itself out of the context.
- "speakers": for EVERY numbered message, the single character who speaks or acts
  in it (their exact name); null when it is narration, several characters, or out
  of character.
- "characters": only NEW characters first appearing in this scene. A character
  worth listing was present: write their "journals" row too.
- "journals": one for EVERY character present in this scene — speaking, acting,
  or silently there; anyone named in "speakers" or "characters" was present and
  gets one. The journal row is the story's record of their presence, so
  a character with nothing to say still gets one.
  "entry" is that character's own record of THIS SCENE ONLY — what they did, saw,
  heard, and felt, in the order they experienced it; when they arrive or leave
  partway through, the entry says so at the point it happens. Up to ~250 words,
  in proportion to how much of the scene is theirs: a silent bystander gets a
  line or two, the character the scene turns on gets the full length. Write only
  what they witnessed or were told — a character does not know what happened
  while they were absent, and a secret kept from them is not in their entry. This
  entry is permanent and is never rewritten, so put everything of theirs into it
  now.
  "state" is a snapshot, not a history: 1-3 sentences — where they are, what they
  wear and carry, how they feel, what they want, right now.
- Lines marked ((OOC: …)) are the players talking out of character: never part of
  the scene's story, but decisions made there belong in the summary and journals.
- A picture the reader attached is marked (picture 1), (picture 2), … at the
  message it came with and, when this request carries pictures, attached in that
  order. The pictures themselves are not kept: put what matters in them — a face,
  a place, an object, a written text — into the summary and the journals, as the
  characters saw it.
- Every value stays in the scene's own language (see LANGUAGE above).
- Empty lists are fine. JSON only.

SCENE (numbered messages):
{chunk}
"""

# The story-so-far rollup over the scene summaries: the narrator's
# ledger, third person like the summaries it combines.
SCENE_HISTORY_DEFAULT = """\
Combine the scene summaries below into one running "story so far" summary —
at most 200 words, chronological, no headings. Output the summary only.

Write it in the SAME LANGUAGE the summaries below are written in — English
summaries get an English summary, French ones a French one. Match the
summaries, not these instructions.

{summaries}
"""

# A character's rollup over their own journal: their memory, so it keeps
# the entries' first-person voice.
JOURNAL_HISTORY_DEFAULT = """\
Write {name}'s history: everything they know of the story so far, drawn from
their own journal entries below.

Rules:
- {name}'s own voice, first person, exactly like the entries themselves:
  "I", never "{name} did". This is their memory, not a report about them.
- Chronological prose, past tense, at most 300 words. No headings, no bullets.
- Compress the earliest entries hardest and keep the recent ones specific.
  Names, promises, debts, injuries, betrayals, and secrets survive compression;
  weather and scenery do not.
- Only what {name} witnessed or was told. Add nothing that is not below.
- Write in the SAME LANGUAGE the entries below are written in — English
  entries get an English history, French ones a French one. Match the
  entries, not these instructions.
- Output the history only.

{name}'s journal, oldest entry first:
{entries}
"""

# The tools' prompts: what tells the model how a tool is used
# (`backend.tools`). Sent as an injection while the tool is switched on
# for the story — bare in the system message, inside the OOC enclosure
# in chat — so neither carries an enclosure of its own.
TOOL_QUESTIONS_DEFAULT = """\
You may ask the reader ONE question when a choice is theirs to make rather than
yours. Write it as the last thing in your reply, inside <otk-question> tags: the
question on the first line, then the answers for the reader to pick from as
numbered lines, at least two. Like this, from an unrelated story:

<otk-question>
Does Mara confess tonight, or wait for the ball?
1. She confesses tonight
2. She waits for the ball
3. She confesses, but to the wrong person
</otk-question>

The reader's next message is the answer; then go on from where you stopped,
without repeating what you wrote. Ask rarely, at most once per reply, and never
inside your reasoning.

Close the block before anything else follows, never put a block inside another,
and stop after the closing tag."""

TOOL_ASSISTANT_NOTES_DEFAULT = """\
You may end a reply with a <otk-note>...</otk-note> block: a note to yourself
that the reader never sees.

Write there only what your reply does not say and you will need, or find
useful, in later turns: something you decided but did not state, why you
answered as you did, what you are holding back or mean to bring up later,
where you intend this to go. Any notes you wrote before are in your earlier
replies — add what is new or what changed, never what is already there or in
the visible text. Many turns have nothing to add; then write no block at all.

Write the notes in whatever form is clear to you later — shorthand, fragments,
a list — and keep them brief: they cost the same context as everything else.

Two examples, from unrelated exchanges. The form is free — these only show the
range.

<otk-note>
Toln recognized the seal. Saying nothing yet — he wants to see if she offers it
first.
</otk-note>

<otk-note>
Third time they've asked for the short version: one paragraph from now on
unless asked for more. The figure they gave earlier was 40k, not 4k — a slip,
not worth correcting unless it comes to matter.
</otk-note>

Close the block before anything else follows, and never put a block inside
another."""

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
    "roll_framing": (
        "((OOC: Dice roll {dice}. The dice are already rolled and the result is "
        "final — say what was rolled and the total in your reply, then narrate "
        "the outcome of exactly this result; never reroll it, change it, or "
        "roll on your own.))\n{body}"
    ),
    "card_framing": (
        "((OOC: {name} joins the story. Their card, to play them by:\n"
        "Description: {description}\n"
        "Personality: {personality}\n"
        "Scenario: {scenario}\n"
        "Example dialogue (voice reference only, never story events): {examples}\n"
        "Standing note: {depth_note}))"
    ),
    "extract_prompt": EXTRACT_DEFAULT,
    "scene_history_prompt": SCENE_HISTORY_DEFAULT,
    "journal_history_prompt": JOURNAL_HISTORY_DEFAULT,
    "recap_header": "[The story so far — the scenes between these moments:]",
    "tool_questions_prompt": TOOL_QUESTIONS_DEFAULT,
    "tool_assistant_notes_prompt": TOOL_ASSISTANT_NOTES_DEFAULT,
}

# Placeholders a template cannot do without: every one its built-in text
# uses. `{name}` is what the command is about, and `{body}` decides where
# the turn's own text sits relative to the ((OOC:)) enclosure — dropping
# either silently changes what the model is told.
_REQUIRED = {
    "me_framing": ("name", "body"),
    "you_framing": ("name",),
    "ooc_framing": ("body",),
    # {dice} is the roll itself — without it the numbers never reach the
    # model and the command is a no-op wearing a frame.
    "roll_framing": ("dice", "body"),
    # Only {name}: a card template line whose OTHER placeholders are absent
    # is a choice — omitting {examples} is how a user keeps examples off
    # the wire — and compose drops the lines of fields a card lacks.
    "card_framing": ("name",),
    "extract_prompt": ("cast", "journals", "chunk"),
    "scene_history_prompt": ("summaries",),
    "journal_history_prompt": ("name", "entries"),
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
    roll_framing: str = _DEFAULTS["roll_framing"]
    card_framing: str = _DEFAULTS["card_framing"]
    extract_prompt: str = _DEFAULTS["extract_prompt"]
    scene_history_prompt: str = _DEFAULTS["scene_history_prompt"]
    journal_history_prompt: str = _DEFAULTS["journal_history_prompt"]
    recap_header: str = _DEFAULTS["recap_header"]
    tool_questions_prompt: str = _DEFAULTS["tool_questions_prompt"]
    tool_assistant_notes_prompt: str = _DEFAULTS["tool_assistant_notes_prompt"]


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
    known = {f.name for f in fields(Prompts)}
    unknown = sorted(set(raw) - known)
    if unknown:
        # A key otaku no longer reads (or a typo) would otherwise sit
        # there looking active while doing nothing — say so once.
        warnings.append(f"{path.name}: ignoring unknown prompt key(s): {', '.join(unknown)}.")
    overrides: dict[str, str] = {}
    for key in known:
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            warnings.append(f"{path.name}: {key} must be a string — ignored.")
            continue
        missing = [p for p in _REQUIRED.get(key, ()) if "{" + p + "}" not in value]
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
    for key, value in _DEFAULTS.items():
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
