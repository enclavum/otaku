"""prompts.toml's migrations: the keys a release renamed, then the
refreshes — a template replaced with the current built-in ONLY where the
file still holds a superseded shipped text, byte-exact as the stub wrote
it: an edited template never matches and is never touched, which is the
right precedence by definition. Each superseded text is a file here,
named as its template's (`prompts.file_of`) with the version it shipped
in appended — `framing_card-0.7.0.txt` — and frozen from that release's
tag (the DB steps' convention) when a change to its template lands; the
refreshes are built from this folder, every one straight to the current
text, so their order is free. Convergent like the settings' own
migrations (`settings.migrations`), and run by the launch beside them.
"""

from collections.abc import Mapping
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType

from otaku.formatting import toml_string
from otaku.prompts import DEFAULTS, file_of, read_template
from otaku.settings.migrations.surgery import Migration, update_settings_file

# Keys a release renamed, old → new, applied before the refreshes, whose
# keys are the new names. 0.4.0: the two rollups say WHOSE history each
# is — the story-so-far over scene summaries, a character's own over
# their journal.
_RENAMED = (
    ("story_so_far_prompt", "scene_history_prompt"),
    ("history_prompt", "journal_history_prompt"),
)


def _superseded() -> dict[tuple[str, str], str]:
    """This folder's texts read whole: each by its template's key and the
    version it shipped in. A file named for no current template fails the
    import."""
    keys = {file_of(key): key for key in DEFAULTS}
    found = {}
    for entry in files(__name__).iterdir():
        stem = entry.name.removesuffix(".txt")
        if stem != entry.name:
            name, _, version = stem.rpartition("-")
            found[(keys[name], version)] = read_template(entry)
    return found


# Every superseded shipped text, by its key and the version it shipped in.
PAST: Mapping[tuple[str, str], str] = MappingProxyType(_superseded())


def migrate(prompts_path: Path, backups_dir: Path) -> bool:
    """prompts.toml brought up to date in one committed edit — the keys
    renamed, then every superseded text it still holds refreshed to the
    current built-in. Returns whether the file changed."""
    changes = [rename_template(old, new) for old, new in _RENAMED]
    changes += [
        refresh_template(key, stale, DEFAULTS[key]) for (key, _), stale in sorted(PAST.items())
    ]
    return update_settings_file(prompts_path, backups_dir, changes)


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
    """`key`'s shipped `stale` text carried to `current`. Matching the WHOLE
    stub-written block — `key = \'\'\'…\'\'\'` — is what makes it safe with
    no parsing: a value the user reworded, however slightly, is a different
    block."""

    def apply(text: str) -> str:
        return text.replace(f"{key} = {toml_string(stale)}", f"{key} = {toml_string(current)}")

    return apply
