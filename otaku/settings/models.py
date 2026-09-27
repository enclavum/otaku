"""Per-model settings: models.toml — the inference parameters written by
/set parameter and the thinking level written by /set think, one entry
per bare model name, and the ONE key a user writes by hand:
`text_template`, the text template the model plays story mode with,
which no command sets. Rewritten wholesale on every change; the session carries the
template line so a rewrite keeps it."""

import tomllib
from pathlib import Path

from otaku.formatting import toml_key, toml_scalar
from otaku.settings import read_settings, write_atomic

# The thinking level is the engine's own word for an effort, and which
# words exist is the provider layer's (`providers.reasoning.EFFORT_LEVELS`),
# read back against it by the session: this file only holds one. "unset"
# is not a level but is saved like one: nothing is sent, and the engine
# decides. A model with no think row starts OFF where the provider says
# it thinks (`api.settings.default_think` writes the row as the model
# becomes current), and has no row otherwise.
THINK_UNSET = "unset"
# The entry's two keys that are not inference parameters.
THINK_KEY = "think"
# The text template a model plays story mode with
# (`providers.templates.TEXT_TEMPLATES` names them); written by hand,
# kept by every save.
TEMPLATE_KEY = "text_template"

_HEADER = [
    "# Per-model settings, written by /set parameter and /set think.",
    "# Keyed by bare model name. A model with no think row starts with its",
    "# thinking off where the provider says it thinks; unset sends nothing.",
    "# text_template = the template story mode wraps the story in, by hand:",
    "# gemma4, gemma, mistral-tekken, chatml, llama3, or plain for none.",
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
