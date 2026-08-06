"""The assembled application over one state dir.

`App` is the composition root: construction is the whole launch short of
the chat loop — config, cipher, providers, model resolution, store, lore
worker, and the session over them — and `run` is the loop. `cli.main`
builds it for the terminal; scenario tests build it over a throwaway
state dir with stub surfaces. The module functions are the launch pieces
other entry points need on their own (`otaku logs requests` unlocks the
way the app does).
"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from otaku import crypto
from otaku.chat import repl
from otaku.chat.commands.transfer import import_story
from otaku.chat.session import TUI, Session
from otaku.formatting import pretty_path
from otaku.logs.errors import ErrorLog
from otaku.logs.requests import RequestLog
from otaku.logs.system import SystemLog
from otaku.lore.worker import LoreWorker
from otaku.paths import Paths
from otaku.providers.registry import Registry, autoconfigure_providers
from otaku.settings import config as config_mod
from otaku.settings import migrations, sealed
from otaku.settings import prompts as prompts_file
from otaku.settings import state as state_mod
from otaku.settings.files import write_atomic
from otaku.store import Store, is_encrypted
from otaku.tui import lore as lore_browser
from otaku.tui import models as model_picker
from otaku.tui import stories as story_picker

_SAMPLE_NOTICE = (
    "A sample story was imported so you can look around — type to play on, "
    "or see every command with /help · /new starts your own play."
)


class App:
    """The running application: `paths`, `store`, `worker`, and the
    `session` over them, ready for the chat loop."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        tui: TUI | None = None,
        pick: Callable[[Registry], str | None] | None = None,
    ) -> None:
        """The launch, short of the chat loop, over `root` (default the
        standard state dir). `tui` and `pick` are the interactive
        surfaces, the real terminal ones unless a test passes stubs.
        Raises `ConfigError`, `CryptoError`, or `DatabaseError` when a
        piece refuses."""
        self.paths = Paths.resolve(root)
        cfg = load_config(self.paths)
        prompts_file.write_stub(self.paths)
        cipher = unlock_cipher(cfg, self.paths)
        state = state_mod.load(self.paths)
        # Sealed api keys open here, once, into the session's providers;
        # one that will not open is reported and its provider runs keyless.
        providers, key_warnings = sealed.resolve_api_keys(self.paths, cfg.providers)
        for line in key_warnings:
            print(line)
        # ONE providers dict for the whole session: the config the session
        # reads and the registry the picker's panel updates share it, so a
        # provider added or edited there is visible everywhere at once.
        # The invariant: names are the stable handle — a config may swap
        # under a running pass, which resolves its client by name.
        cfg = replace(cfg, providers=providers)
        registry = Registry(
            cfg.providers, request_log=RequestLog(self.paths, cipher), smooth=cfg.smooth_streaming
        )

        # The picker runs before the store, so the key ceremony is done
        # and a passphrase prompt never lands after the model is chosen.
        # A remembered model whose provider is still configured skips the
        # screen. Esc is not a cancel: "" opens the session without a
        # model — the same state /model's Esc leaves behind.
        choose = pick or (lambda r: model_picker.pick(r, paths=self.paths))
        remembered = state.model if cfg.serves(state.model) else None
        model_spec = remembered or choose(registry) or ""

        fresh = not self.paths.database_file.exists()
        self.store = Store.open(self.paths, cipher, backups=cfg.backups)
        # The worker's own store connection (WAL makes the concurrent write
        # safe), opened lazily on its thread; backups=0 — the session's open
        # above owns the daily snapshot. It exists whatever [lore_extraction]
        # says: `enabled` only gates the idle scheduling, so /extract always
        # has its one path into a pass.
        self.worker = LoreWorker(
            lambda: Store.open(self.paths, cipher, backups=0),
            registry,
            SystemLog(self.paths),
            errors=ErrorLog(self.paths),
            idle_seconds=cfg.idle_seconds,
        )
        if tui is None:
            tui = TUI(
                pick_model=lambda current: model_picker.pick(
                    registry, initial_spec=current, paths=self.paths
                ),
                pick_story=lambda store, rows, current: story_picker.pick(
                    store,
                    rows,
                    current,
                    dialogue_color=cfg.dialogue_color,
                    dialogue_bold=cfg.dialogue_bold,
                ),
                browse_lore=lore_browser.browse,
            )
        try:
            self.session = Session.start(
                config=cfg,
                paths=self.paths,
                providers=registry,
                model_spec=model_spec,
                state=state,
                store=self.store,
                tui=tui,
                worker=self.worker,
            )
        except BaseException:
            self.store.close()
            raise
        if fresh and cfg.seed_sample:
            # A database created from scratch is seeded with the shipped
            # sample story, through the command's own machinery — a native
            # import, so no pass runs and no model is called — and
            # remembered, so the user lands (and stays) in the middle of a
            # playable story. `import_story`, not the full command: its
            # scene echo belongs to a live session, and at launch the
            # REPL's own resume echo shows the scene.
            import_story(
                self.session,
                self.store,
                str(Path(__file__).parent / "samples" / "story.md"),
            )
            self.session.save_state()
            self.session.notice = _SAMPLE_NOTICE

    def run(self) -> None:
        """The chat loop over the assembled session."""
        repl.run(self.session, self.store)

    def close(self) -> None:
        self.worker.shutdown()  # non-blocking: exit is immediate
        self.store.close()


def load_config(paths: Paths) -> config_mod.Config:
    """The config — written, with providers.toml beside it, when this is
    a first run; migrated to the current shape always, first run
    included, so an autoconfigured plain api key (omlx's, say) is sealed
    by the very launch that wrote it. Raises `ConfigError` when a file
    does not parse."""
    paths.ensure_tree()
    if not paths.config_file.exists():
        first_run = config_mod.Config(providers=autoconfigure_providers())
        write_atomic(paths.config_file, first_run.to_toml())
        if not paths.providers_file.exists():
            write_atomic(paths.providers_file, config_mod.providers_toml(first_run.providers))
        print(f"Created {pretty_path(paths.config_file)}")
    migrations.migrate(paths, autoconfigure_providers())
    return config_mod.load(paths)


def unlock_cipher(cfg: config_mod.Config, paths: Paths) -> crypto.Cipher:
    """The cipher over the configured encryption. Raises `CryptoError`;
    an encrypted database without its keystore is refused BEFORE the key
    ceremony — unlock would otherwise mint a fresh key over it, making
    every sealed row permanently unreadable."""
    if (
        cfg.encryption.provider != "none"
        and is_encrypted(paths.database_file) is True
        and not paths.keys_file.exists()
    ):
        raise crypto.CryptoError(
            f"{pretty_path(paths.keys_file)} is missing, but "
            f"{pretty_path(paths.database_file)} is encrypted. Its content can only "
            "be read with the key that keystore holds — restore it from backup "
            "(together with its KEK), or move the database aside."
        )
    try:
        return crypto.unlock(cfg.encryption, paths)
    except crypto.CryptoError as e:
        raise crypto.CryptoError(f"Could not unlock encryption: {e}") from e
