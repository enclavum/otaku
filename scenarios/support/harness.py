"""The application, launched in-process over a throwaway state dir.

`launch` builds the real `otaku.app.App` — config load, key ceremony,
model resolution, assembly are all its own — pointed at the scripted
server, and scenarios then speak the user's language: `app.play("…")`
submits a prompt line through the REPL's own `submit`, and the store and
the server's recorded requests are open for assertions. The only
stand-ins for the interactive shell are the stub `TUI` (scenarios inject
per-test pickers), `pick` answering with the wanted model, and the
worker being started bare, without the REPL's status wiring.
"""

import dataclasses
from pathlib import Path

from otaku import app as app_mod
from otaku.chat import repl
from otaku.chat.session import TUI
from otaku.paths import Paths
from otaku.settings import config as config_mod
from otaku.settings.files import write_atomic
from scenarios.support.server import ModelServer

PROVIDER = "test"
MODEL = "test-model"
SPEC = f"{PROVIDER}/{MODEL}"


class App(app_mod.App):
    """The real app plus the scenario's view of it: the scripted `server`
    it talks to, and `play`."""

    def __init__(self, root: Path, server: ModelServer, *, spec: str = SPEC) -> None:
        set_config_provider(root, server)
        super().__init__(root, tui=TUI(), pick=lambda registry: spec)
        self.server = server
        self.worker.start()

    def play(self, line: str) -> None:
        """Submit one prompt line exactly the way the REPL does."""
        repl.submit(line, self.session, self.store)


def launch(root: Path, server: ModelServer, *, spec: str = SPEC) -> App:
    """The application over `root`, talking to `server`. `spec` stands in
    for what the model picker would have returned; a remembered model
    resumes over it, exactly as in the real launcher."""
    return App(root, server, spec=spec)


def set_config(root: Path, **fields: object) -> None:
    """Set Config fields in the state dir's config file — an update of
    whatever is there (a scenario shrinking the scene thresholds, say)."""
    paths = Paths.resolve(root)
    cfg = dataclasses.replace(_load_or_default(paths), **fields)  # type: ignore[arg-type]
    write_atomic(paths.config_file, cfg.to_toml())


def set_config_provider(
    root: Path, server: ModelServer, *, supports_thinking: bool | None = None
) -> None:
    """Point the test provider at the scripted server's port — set into
    whatever config is there. `supports_thinking` keeps its current value
    unless specified; a fresh provider supports thinking, so every knob
    is exercisable."""
    paths = Paths.resolve(root)
    cfg = _load_or_default(paths)
    existing = cfg.providers.get(PROVIDER)
    if supports_thinking is None:
        supports_thinking = existing.supports_thinking if existing else True
    cfg.providers[PROVIDER] = config_mod.Provider(
        name=PROVIDER, url=server.url, supports_thinking=supports_thinking
    )
    write_atomic(paths.config_file, cfg.to_toml())


def _load_or_default(paths: Paths) -> config_mod.Config:
    """The config to update: the file when there is one, else app defaults
    with smoothing off (deterministic stream timing) and the test provider
    on a dead port — a config needs a provider section to be valid, and
    the autoconfigured ones read the machine, which a scenario config must
    not. `launch` points the provider at the live server."""
    paths.ensure_tree()
    if paths.config_file.exists():
        return config_mod.load(paths)
    placeholder = config_mod.Provider(
        name=PROVIDER, url="http://127.0.0.1:9/v1", supports_thinking=True
    )
    return config_mod.Config(providers={PROVIDER: placeholder}, smooth_streaming=False)
