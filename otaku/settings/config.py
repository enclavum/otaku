"""The user's configuration: config.toml. The provider sections are a
sibling surface (`settings.providers`); `Config` deliberately does not
carry them — the Registry does, injected at the launch.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from otaku.formatting import toml_scalar
from otaku.settings import read_settings, row


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class TerminalSettings:
    """The looks the terminal frontend needs at its own launch — its
    slice of config.toml, as `WebSettings` below is the other one's (a
    persisted slice, hence a settings type; run-time bundles live beside
    their consumers instead)."""

    # Which of the shipped themes to paint in: "light" or "dark" says so
    # outright, and anything else — "auto", or a hand-edited typo, which
    # should cost the reader nothing — asks the terminal.
    theme: str
    dialogue_color: str
    dialogue_bold: bool
    show_banner: bool
    # Defaulted where the others are not: "default" is a real value —
    # the platform's own sound — so a caller that has no opinion about
    # sound (the theme's, every time) needs none.
    notification_sound: str = "default"


@dataclass(frozen=True)
class WebSettings:
    """Where the web frontend listens and what it asks of whoever
    reaches it — the other slice of config.toml that is a frontend's
    business, and the only one needed before a session exists. Loopback,
    no TLS and no password by default: this is one person's application,
    and reaching it from another machine is a decision to make on
    purpose — the one decision that makes the other two worth taking."""

    host: str = "localhost"
    port: int = 9600
    https: bool = False
    # The hash of `[web] password` that `backend.passwords` made, never
    # the password itself: the launch hashes a typed one before anything
    # reads it. "" is no password at all.
    password: str = ""


@dataclass(frozen=True)
class Encryption:
    """The [encryption] section. Provider "none" (the default) stores
    content as readable plain text."""

    provider: str = "none"
    retrieve_command: str | None = None


@dataclass(frozen=True)
class Config:
    encryption: Encryption = field(default_factory=Encryption)
    # [settings]
    show_banner: bool = True
    smooth_streaming: bool = True
    notification_sound: str = "default"  # "default" = the platform's own; else a path
    # [terminal]
    theme: str = "auto"  # "auto" asks the terminal; else "light" or "dark"
    dialogue_color: str = "auto"
    dialogue_bold: bool = False
    # [web]
    web_host: str = "localhost"
    web_port: int = 9600
    web_https: bool = False
    web_password: str = ""
    # [context]
    head_messages: int = 20
    min_tail_messages: int = 150
    max_context: int = 0
    # [lore_extraction]
    lore_enabled: bool = True
    idle_seconds: float = 300.0
    scene_min_chars: int = 6000
    scene_min_messages: int = 20
    settle_messages: int = 20
    # [database]
    backups: int = 7
    seed_sample: bool = True

    def to_toml(self) -> str:
        """This configuration rendered as config.toml text: every key
        present with an aligned comment, so the whole surface is
        discoverable and editable in place."""
        # One setting per source line, whatever the width — E501 is off
        # for this file (see pyproject).
        # fmt: off
        lines = [
            "[settings]",
            row(f"show_banner = {toml_scalar(self.show_banner)}", "the session header shown when a chat opens"),
            row(f"smooth_streaming = {toml_scalar(self.smooth_streaming)}", "re-time bursty model output into an even stream"),
            row(f"notification_sound = {toml_scalar(self.notification_sound)}", 'what /set notification plays: "default" is the platform\'s own, else a path'),
            "",
            "[terminal]",
            row(f"theme = {toml_scalar(self.theme)}", '"auto" asks the terminal and takes dark when it will not say; or "light"/"dark"'),
            row(f"dialogue_color = {toml_scalar(self.dialogue_color)}", 'spoken lines: "auto" fits the background; a color name ("cyan") or #rrggbb'),
            row(f"dialogue_bold = {toml_scalar(self.dialogue_bold)}", "also bold the spoken lines"),
            "",
            "[web]",
            row(f"host = {toml_scalar(self.web_host)}", '"localhost": reachable from this machine only; "0.0.0.0": from the whole network — set https and a password first'),
            row(f"port = {self.web_port}", "the port `otaku web` listens on"),
            row(f"https = {toml_scalar(self.web_https)}", "RECOMMENDED to turn on when the host is not local; the certificate lives in cert/: drop in your own, or one is generated"),
            row(f"password = {toml_scalar(self.web_password)}", "RECOMMENDED to set when the host is not local; typed in plain text, it is replaced by its hash at the next launch"),
            "",
            "[context]",
            row(f"head_messages = {self.head_messages}", "opening messages kept verbatim in the prompt"),
            row(f"min_tail_messages = {self.min_tail_messages}", "at least this many recent messages kept verbatim"),
            row(f"max_context = {self.max_context}", "the prompt may use at most this many tokens; 0 = the model's own max context"),
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
            row(f"provider = {toml_scalar(self.encryption.provider)}", "none — content stored as readable plain text"),
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
        return "\n".join(lines) + "\n"

    @property
    def web(self) -> WebSettings:
        """The web frontend's slice, cut like `terminal` below."""
        return WebSettings(
            host=self.web_host,
            port=self.web_port,
            https=self.web_https,
            password=self.web_password,
        )

    @property
    def terminal(self) -> TerminalSettings:
        """The terminal frontend's slice, cut once here."""
        return TerminalSettings(
            theme=self.theme,
            dialogue_color=self.dialogue_color,
            dialogue_bold=self.dialogue_bold,
            show_banner=self.show_banner,
            notification_sound=self.notification_sound,
        )


def load(path: Path) -> Config:
    """Read and validate config.toml. Raises ConfigError with a message
    that names the file — it is hand-edited, so errors must be human."""
    try:
        raw = tomllib.loads(read_settings(path))
    except FileNotFoundError as e:
        raise ConfigError(f"{path} does not exist") from e
    except UnicodeDecodeError as e:
        # TOML is UTF-8 by specification, and this file is hand-edited:
        # an editor that saved it in the machine's own codepage is the
        # likely cause, and is something the reader can act on.
        raise ConfigError(f"{path}: not valid UTF-8 — save the file as UTF-8") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML — {e}") from e

    enc_raw = _table(raw, "encryption", path)
    command = enc_raw.get("retrieve_command")
    encryption = Encryption(
        provider=str(enc_raw.get("provider", "none")),
        retrieve_command=str(command) if command is not None else None,
    )

    settings = _table(raw, "settings", path)
    terminal = _table(raw, "terminal", path)
    web = _table(raw, "web", path)
    context = _table(raw, "context", path)
    lore = _table(raw, "lore_extraction", path)
    database = _table(raw, "database", path)
    try:
        return Config(
            encryption=encryption,
            show_banner=bool(settings.get("show_banner", True)),
            smooth_streaming=bool(settings.get("smooth_streaming", True)),
            notification_sound=str(settings.get("notification_sound", "default")),
            dialogue_color=str(terminal.get("dialogue_color", "auto")),
            dialogue_bold=bool(terminal.get("dialogue_bold", False)),
            web_host=str(web.get("host", "localhost")),
            # Clamped to the range a socket accepts, 0 excluded: a port
            # of 0 asks the OS to pick one, and `otaku web` says where
            # the page is BEFORE it binds — an address nobody can be
            # told is no use for a page somebody has to open. A second
            # otaku on one machine names its own port here.
            web_port=min(65535, max(1, _int(web, "port", 9600))),
            web_https=bool(web.get("https", False)),
            web_password=str(web.get("password", "")),
            head_messages=max(0, _int(context, "head_messages", 20)),
            min_tail_messages=max(1, _int(context, "min_tail_messages", 150)),
            max_context=max(0, _int(context, "max_context", 0)),
            lore_enabled=bool(lore.get("enabled", True)),
            idle_seconds=max(0.0, _float(lore, "idle_seconds", 300.0)),
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
