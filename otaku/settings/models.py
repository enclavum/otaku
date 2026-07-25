"""Per-model inference parameters: configs/models.toml.

App-owned, written by `/set parameter` — one table per bare model name
holding that model's parameter overrides, applied when a session starts on
the model. Rewritten atomically, preserving every other model's entry; a
file that cannot be parsed is refused rather than rewritten from scratch,
which would silently drop the other models' settings.
"""

import tomllib

from otaku.paths import Paths
from otaku.settings.files import toml_key, toml_scalar, write_atomic

_HEADER = [
    "# Per-model inference parameters, written by /set parameter.",
    "# Keyed by bare model name.",
    "",
]


def load(paths: Paths) -> dict[str, dict[str, object]]:
    """Every model's saved parameters. Best effort: a missing or malformed
    file just yields no overrides."""
    path = paths.models_file
    if not path.exists():
        return {}
    try:
        raw = tomllib.loads(path.read_text())
    except OSError, tomllib.TOMLDecodeError:
        return {}
    return {str(name): dict(entry) for name, entry in raw.items() if isinstance(entry, dict)}


def save_parameters(paths: Paths, model: str, parameters: dict[str, object]) -> None:
    """Replace one model's saved parameters (empty = remove its entry),
    keeping every other model's. Raises ValueError on an unreadable file."""
    path = paths.models_file
    data: dict[str, dict[str, object]] = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise ValueError(f"{path} is unreadable ({e}); fix or move it") from e
        data = {str(name): dict(entry) for name, entry in raw.items() if isinstance(entry, dict)}
    if parameters:
        data[model] = dict(parameters)
    else:
        data.pop(model, None)
    lines = list(_HEADER)
    for name in sorted(data):
        lines.append(f"[{toml_key(name)}]")
        lines += [f"{toml_key(k)} = {toml_scalar(v)}" for k, v in sorted(data[name].items())]
        lines.append("")
    write_atomic(path, "\n".join(lines))
