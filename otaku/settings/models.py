"""Per-model settings: models.toml — the inference parameters written by
/set parameter and the thinking level written by /set think, one entry
per bare model name. Rewritten wholesale on every change; there are no
user edits to preserve."""

import tomllib
from pathlib import Path

from otaku.formatting import toml_key, toml_scalar
from otaku.settings import read_settings, write_atomic

# The thinking level is the engine's own word for an effort, and which
# words exist is the provider layer's (`providers.reasoning.EFFORTS`),
# read back against it by the session: this file only holds one. "unset"
# is not a level: no level is saved, nothing is sent, and the engine
# decides — what a model with no think row runs at, so an entry carries
# the key only for a level.
THINK_UNSET = "unset"
# The entry's one key that is not an inference parameter.
THINK_KEY = "think"

_HEADER = [
    "# Per-model settings, written by /set parameter and /set think.",
    "# Keyed by bare model name. A model without a think row runs unset:",
    "# nothing sent, the engine decides.",
    "",
]


def load(path: Path) -> dict[str, dict[str, object]]:
    """Every model's saved entry — its parameters, and its thinking
    level under `THINK_KEY`. Best effort: a missing or malformed file
    yields no entries."""
    if not path.exists():
        return {}
    try:
        raw = tomllib.loads(read_settings(path))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    return {str(name): dict(entry) for name, entry in raw.items() if isinstance(entry, dict)}


def save(path: Path, model: str, entry: dict[str, object]) -> None:
    """Replace one model's entry (empty = remove it), keeping every
    other model's. Raises ValueError on an unreadable file rather than
    silently dropping the other models' settings."""
    data: dict[str, dict[str, object]] = {}
    if path.exists():
        try:
            raw = tomllib.loads(read_settings(path))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
            raise ValueError(f"{path} is unreadable ({e}); fix or move it") from e
        data = {str(name): dict(entry) for name, entry in raw.items() if isinstance(entry, dict)}
    if entry:
        data[model] = dict(entry)
    else:
        data.pop(model, None)
    lines = list(_HEADER)
    for name in sorted(data):
        lines.append(f"[{toml_key(name)}]")
        lines += [f"{toml_key(k)} = {toml_scalar(v)}" for k, v in sorted(data[name].items())]
        lines.append("")
    write_atomic(path, "\n".join(lines))
