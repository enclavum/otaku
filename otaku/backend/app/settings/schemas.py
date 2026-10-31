"""What `app.settings.resources` answers: its schemas."""

from __future__ import annotations

from pathlib import Path

from otaku.backend.base import Schema

# Re-exported: each frontend's slice of config.toml, the slices' own types.
from otaku.settings.config import TerminalSettings as TerminalSettings
from otaku.settings.config import WebSettings as WebSettings


class Settings(Schema):
    """The installation's settings as they stand: the story last landed
    in — by either frontend, the one a launch resumes; None for none —
    the /set switches, the context limit (the most tokens a request may
    use; 0 = none, the model's own window) and how it reads, and how long
    the prompt stands idle before the lore pass runs on its own."""

    last_story: int | None
    verbose: bool
    autocorrect: bool
    notification: bool
    context_limit: int
    context_limit_text: str
    idle_seconds: float


class WebFrontendSettings(Schema):
    """What the web frontend is set up with: its `[web]` slice of
    config.toml — where it listens, the sign-in password's hash — the
    reader's own directory it serves last (custom.css, fonts/), which
    otaku never writes, and the directory of the TLS pair it serves
    under."""

    config: WebSettings
    custom_dir: Path
    cert_dir: Path
