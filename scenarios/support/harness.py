"""The application, launched in-process over a throwaway state dir.

`launch` builds the real app — `backend.launch.open_app` owns the config
load, key ceremony, and assembly — pointed at the scripted server, and
scenarios then speak the user's language: `app.play("…")` submits a line
through the terminal's own `submit`, over the terminal's own `Chat`,
which holds the story it plays in as the terminal does. `app.api` is the
interface a frontend holds (`backend.api.Api`) and `app.backend` the App
it is built over, for its life alone; `app.story_id` and `app.messages`
read the story the chat is in, `app.ensure_story()` makes it where a
scenario needs it before its first line. `app.store` is a second read
connection over the same database (WAL makes the concurrent read safe),
so no scenario reaches into the app's package-private handles. The
full-screen surfaces are module functions — a scenario that needs one
patches `screens.stories.pick` (and friends) at its module seam, per
test.
"""

import dataclasses
import os
import secrets
import subprocess
import sys
from pathlib import Path

from otaku import encryption
from otaku.backend import launch as backend_launch
from otaku.backend.api import Api, Layout, ModelRef
from otaku.backend.api import App as BackendApp
from otaku.backend.app.models.schemas import ModelSettings
from otaku.backend.app.paths import Paths
from otaku.backend.story.messages.schemas import Message
from otaku.backend.story.settings.schemas import DEFAULT_LAYOUT
from otaku.encryption import seal
from otaku.settings import config as config_file
from otaku.settings import providers as providers_file
from otaku.settings import write_atomic
from otaku.settings.providers import ProviderConfig
from otaku.store import Store
from otaku.terminal import run
from otaku.terminal.chat import landing
from otaku.terminal.chat.chat import Chat
from scenarios.support.server import ModelServer

PROVIDER = "generic"
MODEL = "test-model"
SPEC = f"{PROVIDER}/{MODEL}"


class App:
    """The launched app plus the scenario's view of it: the interface
    (`api`) and the App it is built over (`backend`), the scripted
    `server` it talks to, the terminal-side `chat` holding the story, a
    read `store`, and `play`."""

    def __init__(self, root: Path, server: ModelServer, *, spec: str | None = SPEC) -> None:
        set_config_provider(root, server)
        self.server = server
        self.paths = Paths.resolve(root)
        self.backend: BackendApp = backend_launch.open_app(root)
        self.api = Api(self.backend)
        # What the launch-time picker would have settled — the same
        # switch the picker executes, and only when nothing is
        # remembered: a remembered model resumes over the pick, exactly
        # as in the real launcher.
        if spec and self.api.app.get().model is None:
            provider, _, model = spec.partition("/")
            self.api.app.update(model=ModelRef(provider, model))
        self.chat = Chat(self.backend)
        # The story the last run landed in, resumed — as `run.run` does.
        if (resumed := self.api.settings.get().last_story) is not None:
            self.chat.land(resumed)
        self.backend.start_worker()
        self.store = read_store(root)

    @property
    def story_id(self) -> int | None:
        """The story the chat is in; None before its first line."""
        return self.chat.story_id

    def ensure_story(self) -> int:
        """The story the chat plays, made now where there is none yet —
        as a first line or a title makes it in the terminal."""
        return self.chat.ensure_story()

    def head(self, story_id: int | None = None) -> int | None:
        """Where a story's head stands now — the chat's own by default,
        made where there is none — as a write that moves it names it
        (`expected_head`)."""
        target = self.ensure_story() if story_id is None else story_id
        return self.api.stories.get(target).head

    def model_settings(self) -> ModelSettings:
        """The model in use's own settings, as the interface reads them."""
        model = self.api.app.get().model
        assert model is not None, "no model in use"
        return self.api.providers.models.settings.get(model.provider, model.name)

    @property
    def messages(self) -> list[Message]:
        """The chat's story as the interface reads it, root to head; []
        while there is none."""
        if self.chat.story_id is None:
            return []
        return self.api.stories.messages.list(self.chat.story_id)

    @property
    def premise(self) -> str:
        """The premise of the chat's story as the interface reads it; none
        while there is no story."""
        if self.chat.story_id is None:
            return ""
        return self.api.stories.get(self.chat.story_id).premise

    @property
    def layout(self) -> Layout:
        """The layout the chat's story is read in, as the interface reads
        it — or, before its first line, the one set for it."""
        if self.chat.story_id is None:
            return self.chat.draft_layout or DEFAULT_LAYOUT
        return self.api.stories.get(self.chat.story_id).layout

    def play(self, line: str) -> None:
        """Submit one line exactly the way the prompt does — the story
        read again first, as before every prompt."""
        landing.catch_up(self.chat)
        run.submit(self.chat, line)

    def close(self) -> None:
        self.chat.stop_serving()
        self.store.close()
        self.backend.close()


def read_store(root: Path) -> Store:
    """A second connection over the same database, for assertions —
    unlocked the way the launch unlocks (the scripted `command` KEK
    provider works headless), so no scenario reaches into the app's
    package-private store. WAL makes the concurrent read safe."""
    paths = Paths.resolve(root)
    cfg = config_file.load(paths.config_file)
    cipher = encryption.unlock(
        cfg.encryption.provider,
        keys_file=paths.keys_file,
        kek_file=paths.kek_file,
        service=paths.keychain_service,
        retrieve_command=cfg.encryption.retrieve_command,
    )
    return Store.open(paths.database_file, cipher, backups_dir=paths.backups_dir, keep=0)


def launch(root: Path, server: ModelServer, *, spec: str | None = SPEC) -> App:
    """The application over `root`, talking to `server`. `spec` stands in
    for what the model picker would have returned; None opens the app
    model-less."""
    return App(root, server, spec=spec)


def run_otaku(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`otaku ARGS` over the state dir at `root`, run for real and
    captured — for the subcommands that print and exit; the pty driver
    (`terminal.py`) owns the interactive journeys."""
    return subprocess.run(
        [sys.executable, "-c", "from otaku.cli import main; main()", *args],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        env={
            **os.environ,
            "OTAKU_CONFIG_DIR": str(root),
            "COLUMNS": "200",
            "OTAKU_UPDATE_URL": "http://127.0.0.1:9/",  # never GitHub
        },
        check=False,
    )


def set_config(root: Path, **fields: object) -> None:
    """Set Config fields in the state dir's config file — an update of
    whatever is there (a scenario shrinking the scene thresholds, say).
    Providers live in their own file (`set_config_provider`)."""
    paths = Paths.resolve(root)
    paths.ensure_tree()
    current = (
        config_file.load(paths.config_file) if paths.config_file.exists() else _default_config()
    )
    updated = dataclasses.replace(current, **fields)  # type: ignore[arg-type]
    write_atomic(paths.config_file, updated.to_toml())


def set_config_provider(
    root: Path,
    server: ModelServer,
    *,
    name: str = PROVIDER,
    keep_alive: str = "",
    api_key: str = "scenario-key",
    prompt_cache: str = "",
) -> None:
    """Point a provider at the scripted server's port — set into whatever
    files are there. `name` picks the client the registry builds (a
    provider named "ollama" or "omlx" gets its managing client, the
    default "generic" the generic one)."""
    paths = Paths.resolve(root)
    paths.ensure_tree()
    # The sealing key as a file, pre-seeded: a scenario that seals must
    # never reach the developer's real OS keychain.
    if not paths.config_key_file.exists():
        paths.config_key_file.write_bytes(secrets.token_bytes(32))
    if api_key:
        # Written already sealed, over the file sealing key: the files
        # are in their converged shape, so the launch migration edits
        # nothing and untouched-config assertions keep holding.
        api_key = seal(api_key, key_file=paths.config_key_file, service="scenario:none")
    providers = providers_file.load(paths.providers_file) if paths.providers_file.exists() else {}
    providers = {**_dead_locals(), **providers}
    if name == "openrouter" and not prompt_cache:
        # The converged shape: the launch migration writes this key into
        # every [openrouter] section, so a scenario's file carries it up
        # front and untouched-config assertions keep holding.
        prompt_cache = "5m"
    providers[name] = ProviderConfig(
        name=name,
        url=server.url,
        api_key=api_key,
        keep_alive=keep_alive,
        prompt_cache=prompt_cache,
    )
    write_atomic(paths.providers_file, providers_file.render(providers))
    if not paths.config_file.exists():
        write_atomic(paths.config_file, _default_config().to_toml())


def _default_config() -> config_file.Config:
    """App defaults with smoothing off (deterministic stream timing) and
    no sample seeding (a scenario's story is its own)."""
    return config_file.Config(smooth_streaming=False, seed_sample=False)


def _dead_locals() -> dict[str, ProviderConfig]:
    """The local providers, pre-seeded on a dead port: the launch's
    ensure_providers finds them present and never writes sections that
    point at the developer machine's real engines."""
    return {
        kind: ProviderConfig(name=kind, url="http://127.0.0.1:9/v1")
        for kind in ("llamacpp", "koboldcpp", "ollama", "omlx", "lmstudio")
    }
