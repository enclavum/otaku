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
import os
import secrets
import subprocess
import sys
from pathlib import Path

from otaku import app as app_mod
from otaku.chat import repl
from otaku.chat.session import TUI
from otaku.paths import Paths
from otaku.settings import config as config_mod
from otaku.settings import sealed
from otaku.settings.files import write_atomic
from scenarios.support.server import ModelServer

PROVIDER = "test"
MODEL = "test-model"
SPEC = f"{PROVIDER}/{MODEL}"

# The break rule the screen ledger draws where the played sequence breaks,
# at its narrowest — a prefix of the run at any terminal width.
RULE = "┈" * 20


class App(app_mod.App):
    """The real app plus the scenario's view of it: the scripted `server`
    it talks to, and `play`."""

    def __init__(self, root: Path, server: ModelServer, *, spec: str | None = SPEC) -> None:
        set_config_provider(root, server)
        super().__init__(root, tui=TUI(), pick=lambda registry: spec)
        self.server = server
        self.worker.start()

    def play(self, line: str) -> None:
        """Submit one prompt line exactly the way the REPL does."""
        repl.submit(line, self.session, self.store)


def launch(root: Path, server: ModelServer, *, spec: str | None = SPEC) -> App:
    """The application over `root`, talking to `server`. `spec` stands in
    for what the model picker would have returned; a remembered model
    resumes over it, exactly as in the real launcher."""
    return App(root, server, spec=spec)


def run_otaku(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`otaku ARGS` over the state dir at `root`, run for real and
    captured — for the subcommands that print and exit; the pty driver
    (`terminal.py`) owns the interactive journeys."""
    return subprocess.run(
        [sys.executable, "-c", "from otaku.cli import main; main()", *args],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "OTAKU_CONFIG_DIR": str(root), "COLUMNS": "200"},
        check=False,
    )


def set_config(root: Path, **fields: object) -> None:
    """Set Config fields in the state dir's config file — an update of
    whatever is there (a scenario shrinking the scene thresholds, say)."""
    paths = Paths.resolve(root)
    cfg = dataclasses.replace(_load_or_default(paths), **fields)  # type: ignore[arg-type]
    write_atomic(paths.config_file, cfg.to_toml())
    write_atomic(paths.providers_file, config_mod.providers_toml(cfg.providers))


def set_config_provider(
    root: Path,
    server: ModelServer,
    *,
    name: str = PROVIDER,
    keep_alive: str = "",
    api_key: str = "scenario-key",
) -> None:
    """Point a provider at the scripted server's port — set into whatever
    config is there. `name` picks the client the registry builds (a
    provider named "ollama" or "omlx" gets its managed backend, the
    default "test" the generic one)."""
    paths = Paths.resolve(root)
    cfg = _load_or_default(paths)
    if api_key:
        # Written already sealed, over the file sealing key: the config
        # is in its converged shape, so the launch migration edits
        # nothing and untouched-config assertions keep holding.
        if not paths.config_key_file.exists():
            paths.ensure_tree()
            paths.config_key_file.write_bytes(secrets.token_bytes(32))
        api_key = sealed.seal(paths, api_key)
    cfg.providers[name] = config_mod.ProviderConfig(
        name=name, url=server.url, keep_alive=keep_alive, api_key=api_key
    )
    write_atomic(paths.config_file, cfg.to_toml())
    write_atomic(paths.providers_file, config_mod.providers_toml(cfg.providers))
    # The sealing key as a file, pre-seeded: a scenario that saves an api
    # key must never reach the developer's real OS keychain.
    if not paths.config_key_file.exists():
        paths.config_key_file.write_bytes(secrets.token_bytes(32))


def _load_or_default(paths: Paths) -> config_mod.Config:
    """The config to update: the file when there is one, else app defaults
    with smoothing off (deterministic stream timing), no sample seeding
    (a scenario's story is its own), and the test provider on a dead
    port — a config needs a provider section to be valid, and the
    autoconfigured ones read the machine, which a scenario config must
    not. `launch` points the provider at the live server."""
    paths.ensure_tree()
    if paths.config_file.exists():
        return config_mod.load(paths)
    placeholder = config_mod.ProviderConfig(name=PROVIDER, url="http://127.0.0.1:9/v1")
    # The local backends, pre-seeded on the same dead port: the launch's
    # ensure_providers finds them present and never writes sections that
    # point at the developer machine's real engines.
    locals_dead = {
        kind: config_mod.ProviderConfig(name=kind, url="http://127.0.0.1:9/v1")
        for kind in ("llamacpp", "koboldcpp", "ollama", "omlx", "lmstudio")
    }
    return config_mod.Config(
        providers={PROVIDER: placeholder, **locals_dead},
        smooth_streaming=False,
        seed_sample=False,
    )
