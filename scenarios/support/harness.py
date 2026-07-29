"""The application, assembled in-process the way `otaku.cli.main` does it.

`launch` wires everything the launcher would — config file, prompts stub,
store, provider registry, worker, session — against the scripted server
and a throwaway state dir, and hands back an `App`. Scenarios then speak
the user's language: `app.play("…")` submits a prompt line exactly like
the REPL (a command dispatches, anything else becomes a turn and runs
inference), and the store and the server's recorded requests are open for
assertions.
"""

from dataclasses import dataclass
from pathlib import Path

from otaku.chat.commands import dispatch
from otaku.chat.inference import run_inference
from otaku.chat.state import TUI, Session
from otaku.crypto import PlainCipher
from otaku.logs.requests import RequestLog
from otaku.logs.system import SystemLog
from otaku.lore.worker import LoreWorker
from otaku.paths import Paths
from otaku.providers.registry import Registry
from otaku.settings import config as config_mod
from otaku.settings import prompts as prompts_file
from otaku.settings import state as state_mod
from otaku.settings.files import write_atomic
from otaku.store import Store
from otaku.store.schema import Message
from scenarios.support.server import ModelServer

PROVIDER = "test"
MODEL = "test-model"
SPEC = f"{PROVIDER}/{MODEL}"


@dataclass
class App:
    """One running application: the session, its store, and the server it
    talks to."""

    paths: Paths
    session: Session
    store: Store
    worker: LoreWorker
    server: ModelServer

    def play(self, line: str) -> None:
        """Submit one prompt line the way the REPL does: a slash command
        dispatches; anything else is recorded as the user's turn and the
        model answers."""
        if dispatch(line, self.session, self.store):
            return
        self.session.record_turn(self.store, Message(role="user", body=line))
        run_inference(self.session, self.store)

    def close(self) -> None:
        self.worker.shutdown()
        self.store.close()


def write_config(root: Path, server: ModelServer, **overrides: object) -> None:
    """The state dir's config.toml, pointing the one provider at the
    scripted server — what a first run would have written. `overrides` are
    Config fields (a scenario shrinking the scene thresholds, say)."""
    paths = Paths.resolve(root)
    paths.ensure_tree()
    providers = {PROVIDER: config_mod.Provider(name=PROVIDER, url=server.url)}
    config = config_mod.Config(providers=providers, **overrides)  # type: ignore[arg-type]
    write_atomic(paths.config_file, config.to_toml())


def launch(
    root: Path, server: ModelServer, *, idle_seconds: float = 999.0, spec: str = SPEC
) -> App:
    """The application over `root`, mirroring `otaku.cli.main`'s wiring.
    The worker is real and started; its idle is long by default so nothing
    fires mid-scenario unless a test wants exactly that."""
    paths = Paths.resolve(root)
    if not paths.config_file.exists():
        write_config(root, server)
    cfg = config_mod.load(paths)
    prompts_file.write_stub(paths)
    cipher = PlainCipher()
    registry = Registry(cfg.providers, request_log=RequestLog(paths, cipher))
    store = Store.open(paths, cipher, backups=0)
    worker = LoreWorker(
        lambda: Store.open(paths, cipher, backups=0),
        registry,
        SystemLog(paths),
        idle_seconds=idle_seconds,
        min_dwell=0.0,
    )
    worker.start()
    session = Session.start(
        config=cfg,
        paths=paths,
        providers=registry,
        spec=spec,
        state=state_mod.load(paths),
        store=store,
        tui=TUI(),
        worker=worker,
    )
    return App(paths=paths, session=session, store=store, worker=worker, server=server)
