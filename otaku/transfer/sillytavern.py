"""A SillyTavern chat, read into the same shape our own document parses to."""

import json

from otaku.transfer import ExportedMessage, StoryExport

# ST's own .jsonl acceptance test is just "the header parses and has one of
# these keys" — match that tolerance.
_ST_HEADER_KEYS = ("user_name", "character_name", "chat_metadata")
# Names that are roles or defaults, not characters — attributing them would
# put a "You" row in the cast.
_ST_NOT_A_NAME = frozenset({"you", "user", "assistant", "system", "narrator", "unknown"})


def parse_sillytavern(text: str) -> StoryExport | None:
    """A SillyTavern chat (.jsonl) as a `StoryExport` — or None when the
    text isn't one (the first non-empty line must be a JSON object
    carrying ST's header signature and no `mes`).

    Lines ST itself excludes from prompts are skipped silently: `is_system`
    ones (the hidden-from-the-model flag), plus empty and malformed lines;
    only the ACTIVE swipe (`mes`) is taken. Every message carries a
    `name`, so turns arrive attributed — real names become speakers,
    role-words and ST defaults ("You", "User") do not."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip()), None)
    if start is None:
        return None
    try:
        header = json.loads(lines[start])
    except json.JSONDecodeError:
        return None
    if not isinstance(header, dict) or "mes" in header:
        return None
    if not any(key in header for key in _ST_HEADER_KEYS):
        return None
    messages: list[ExportedMessage] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or "mes" not in obj or obj.get("is_system"):
            continue
        body = str(obj.get("mes") or "")
        if not body.strip():
            continue
        name = str(obj.get("name") or "").strip()
        speaker = name if name and name.casefold() not in _ST_NOT_A_NAME else None
        messages.append(
            ExportedMessage(
                role="user" if obj.get("is_user") else "assistant",
                body=body,
                speaker=speaker,
            )
        )
    return StoryExport(messages=tuple(messages))
