"""prompts.toml refreshes: replace a template with the current built-in
ONLY when the file still holds a superseded shipped default, byte-exact
as the stub wrote it — an edited template never matches and is never
touched, which is the right precedence by definition. Each shipped
change of a template adds its superseded text here, frozen from the
release tag (the DB steps' convention)."""

from pathlib import Path

from otaku.formatting import toml_string
from otaku.settings.migrations.surgery import Migration, update_settings_file

EXTRACT_0_2_2 = """\
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


HISTORY_0_3_0 = """\
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


EXTRACT_0_3_0 = """\
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
- Every value stays in the scene's own language (see LANGUAGE above).
- Empty lists are fine. JSON only.

SCENE (numbered messages):
{chunk}
"""


STORY_SO_FAR_0_3_0 = """\
Combine the scene summaries below into one running "story so far" summary
(4-8 sentences, chronological, no headings). Output the summary only.

Write it in the SAME LANGUAGE as the summaries below — do not translate it,
and do not answer in English because these instructions are in English.

{summaries}
"""


EXTRACT_0_5_0 = """\
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
- Every value stays in the scene's own language (see LANGUAGE above).
- Empty lists are fine. JSON only.

SCENE (numbered messages):
{chunk}
"""


def rename_template(old: str, new: str) -> Migration:
    """Renames a template's KEY, leaving whatever value it holds — edited
    or shipped — untouched. Line-wise, tracking the `'''` literals
    `toml_string` writes, so a template BODY that happens to spell
    `old = ` at the head of a line is never renamed."""

    def apply(text: str) -> str:
        out = []
        inside = False  # …a triple-quoted value, where keys cannot begin
        for line in text.split("\n"):
            if not inside and line.startswith(f"{old} = "):
                line = f"{new} = " + line[len(f"{old} = ") :]
            if line.count("'''") % 2 == 1:
                inside = not inside
            out.append(line)
        return "\n".join(out)

    return apply


def refresh_template(key: str, stale: str, current: str) -> Migration:
    """The one move this table knows. Matching the WHOLE stub-written
    block — `key = \'\'\'…\'\'\'` — is what makes it safe with no parsing:
    a value the user reworded, however slightly, is a different block."""

    def apply(text: str) -> str:
        return text.replace(f"{key} = {toml_string(stale)}", f"{key} = {toml_string(current)}")

    return apply


def update_prompts(prompts_path: Path, backups_dir: Path, changes: list[Migration]) -> bool:
    """One committed edit of prompts.toml — the same read-heal-commit
    every settings file gets. Returns whether the file changed."""
    return update_settings_file(prompts_path, backups_dir, changes)
