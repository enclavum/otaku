"""The user's configuration: configs/config.toml.

Read-only for the app — bootstrap writes it once at first run, the user
edits it thereafter, and the one exception is `settings.migrations`:
surgical shape updates applied at launch when the file is from an older
build. Everything the app itself changes lives in state.toml
(`settings.state`) or models.toml instead.

This module owns the config surface: the dataclasses, the reader, and the
rendering — `Config.to_toml()` renders any instance as the file. The
provider sections come from the backend classes (each backend's
`autoconfigure`), assembled at the first-run write by the CLI.
"""

import tomllib
from dataclasses import dataclass, field

from otaku.paths import Paths
from otaku.settings.files import row, toml_key, toml_scalar


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Provider:
    """One [providers.NAME] section: an OpenAI-compatible server."""

    name: str
    url: str
    api_key: str = ""
    supports_thinking: bool = False
    keep_alive: str = ""  # how long an explicitly loaded model stays resident (ollama)

    @property
    def base_url(self) -> str:
        """The URL without a trailing /v1 — where a backend's native
        management endpoints live."""
        return self.url[: -len("/v1")] if self.url.endswith("/v1") else self.url

    @property
    def headers(self) -> dict[str, str]:
        """Auth headers for every request to this provider."""
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


@dataclass(frozen=True)
class Encryption:
    """The [encryption] section. Provider "none" (the default) stores content
    as readable plain text; the others name where the key-encryption key
    comes from — see `otaku.crypto`."""

    provider: str = "none"
    retrieve_command: str | None = None


@dataclass(frozen=True)
class Config:
    providers: dict[str, Provider]
    encryption: Encryption = field(default_factory=Encryption)
    # [settings]
    show_banner: bool = True
    smooth_streaming: bool = True
    # [ui]
    dialogue_color: str = "auto"
    dialogue_bold: bool = False
    # [context]
    head_messages: int = 20
    tail_messages: int = 150
    # [lore_extraction]
    lore_enabled: bool = True
    idle_seconds: float = 300.0
    scene_min_chars: int = 6000
    scene_min_messages: int = 20
    settle_messages: int = 20
    # [database]
    backups: int = 7
    seed_sample: bool = True

    def serves(self, spec: str) -> bool:
        """Whether `spec` ("provider/model") names a configured provider —
        what the launcher asks before resuming a remembered model."""
        provider_name, _, model = spec.partition("/")
        return bool(model) and provider_name in self.providers

    def to_toml(self) -> str:
        """This configuration rendered as config.toml text: every key present
        with an aligned comment, so the whole surface is discoverable and
        editable in place."""
        # One setting per source line, whatever the width — E501 is off
        # for this file (see pyproject).
        # fmt: off
        lines = [
            "[settings]",
            row(f"show_banner = {toml_scalar(self.show_banner)}", "the session header shown when a chat opens"),
            row(f"smooth_streaming = {toml_scalar(self.smooth_streaming)}", "re-time bursty model output into an even stream"),
            "",
            "[ui]",
            row(f"dialogue_color = {toml_scalar(self.dialogue_color)}", 'spoken lines: "auto" fits the background; a color name ("cyan") or #rrggbb'),
            row(f"dialogue_bold = {toml_scalar(self.dialogue_bold)}", "also bold the spoken lines"),
            "",
            "[context]",
            row(f"head_messages = {self.head_messages}", "opening messages kept verbatim in the prompt"),
            row(f"tail_messages = {self.tail_messages}", "recent messages kept verbatim"),
            "",
            "[lore_extraction]",
            row(f"enabled = {toml_scalar(self.lore_enabled)}", "extract lore on idle (/extract always works)"),
            row(f"idle_seconds = {toml_scalar(self.idle_seconds)}", "extraction runs after this long idle at the prompt"),
            row(f"scene_min_chars = {self.scene_min_chars}", "a scene closes once it holds this much text…"),
            row(f"scene_min_messages = {self.scene_min_messages}", "…and at least this many messages"),
            row(f"settle_messages = {self.settle_messages}", "newest messages a scene never closes over"),
            "",
            "[database]",
            row(f"backups = {self.backups}", "daily snapshots kept in database/backups/ (0 disables)"),
            row(f"seed_sample = {toml_scalar(self.seed_sample)}", "import the sample story into a freshly created database"),
            "",
            "[encryption]",
            row(f'provider = "{self.encryption.provider}"', "none — content stored as readable plain text"),
            row("", "keychain — key in the OS keychain"),
            row("", "command — key from retrieve_command's stdout"),
            row("", "passphrase — key derived from a passphrase, asked every launch"),
            row("", "disk — key in configs/kek.key"),
        ]
        # fmt: on
        if self.encryption.retrieve_command is not None:
            command = toml_scalar(self.encryption.retrieve_command)
            lines.append(row(f"retrieve_command = {command}", 'only for provider = "command"'))
        else:
            lines.append(
                row('# retrieve_command = "pass otaku/kek"', 'only for provider = "command"')
            )
        for provider in self.providers.values():
            lines += [
                "",
                f"[providers.{toml_key(provider.name)}]",
                f"url = {toml_scalar(provider.url)}",
                f"api_key = {toml_scalar(provider.api_key)}",
                f"supports_thinking = {toml_scalar(provider.supports_thinking)}",
            ]
            if provider.keep_alive:
                lines.append(f"keep_alive = {toml_scalar(provider.keep_alive)}")
        return "\n".join(lines) + "\n"


def load(paths: Paths) -> Config:
    """Read and validate config.toml. Raises ConfigError with a message that
    names the file — the config is hand-edited, so errors must be human."""
    path = paths.config_file
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"{path} does not exist") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML — {e}") from e

    providers_raw = raw.get("providers")
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise ConfigError(f"{path}: at least one [providers.NAME] section is required")
    providers: dict[str, Provider] = {}
    for name, entry in providers_raw.items():
        if not isinstance(entry, dict) or "url" not in entry:
            raise ConfigError(f"{path}: [providers.{name}] must be a table with a 'url' key")
        providers[name] = Provider(
            name=str(name),
            url=str(entry["url"]).rstrip("/"),
            api_key=str(entry.get("api_key", "")),
            supports_thinking=bool(entry.get("supports_thinking", False)),
            keep_alive=str(entry.get("keep_alive", "")),
        )

    enc_raw = _table(raw, "encryption", path)
    command = enc_raw.get("retrieve_command")
    encryption = Encryption(
        provider=str(enc_raw.get("provider", "none")),
        retrieve_command=str(command) if command is not None else None,
    )

    settings = _table(raw, "settings", path)
    ui = _table(raw, "ui", path)
    context = _table(raw, "context", path)
    lore = _table(raw, "lore_extraction", path)
    database = _table(raw, "database", path)
    try:
        return Config(
            providers=providers,
            encryption=encryption,
            show_banner=bool(settings.get("show_banner", True)),
            smooth_streaming=bool(settings.get("smooth_streaming", True)),
            dialogue_color=str(ui.get("dialogue_color", "auto")),
            dialogue_bold=bool(ui.get("dialogue_bold", False)),
            head_messages=max(0, _int(context, "head_messages", 20)),
            tail_messages=max(1, _int(context, "tail_messages", 150)),
            lore_enabled=bool(lore.get("enabled", True)),
            idle_seconds=_float(lore, "idle_seconds", 300.0),
            scene_min_chars=max(1, _int(lore, "scene_min_chars", 6000)),
            scene_min_messages=max(1, _int(lore, "scene_min_messages", 20)),
            settle_messages=max(0, _int(lore, "settle_messages", 20)),
            backups=max(0, _int(database, "backups", 7)),
            seed_sample=bool(database.get("seed_sample", True)),
        )
    except ValueError as e:
        raise ConfigError(f"{path}: {e}") from e


def _table(raw: dict[str, object], name: str, path: object) -> dict[str, object]:
    section = raw.get(name, {})
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: [{name}] must be a table")
    return section


def _int(section: dict[str, object], key: str, default: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer")
    return value


def _float(section: dict[str, object], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"'{key}' must be a number")
    return float(value)
