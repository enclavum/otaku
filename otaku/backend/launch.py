"""The launch: one state dir becomes a live App — as functions, not a
class; the app itself is the product.

`open_app` is the whole composition — paths, config (first-run write
+ convergent migrations), the key ceremony (the content plane's cipher,
shared by the store and the request log), providers (sealed keys opened,
values injected into the registry), store, worker, the samples seeded
(`backend.samples`) — and
it never prints: what a frontend should say rides on the app, its
`LaunchFacts`, read as `api.app.get().launch_facts`.
The module also carries the launch pieces cli needs alone (`otaku logs
requests` unlocks the way the app does).
"""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TypeVar

from otaku import __version__, encryption
from otaku.agents import Worker
from otaku.backend import samples
from otaku.backend.api import Api
from otaku.backend.app import passwords
from otaku.backend.app.core.services import App, LaunchFacts
from otaku.backend.app.paths import Paths
from otaku.backend.app.settings import services as domain_settings
from otaku.backend.errors import NO_MODEL_HINT
from otaku.encryption import AskSecret, Cipher, SealedError

# What `open_app` raises when a piece refuses, for the caller to catch.
from otaku.encryption import EncryptionError as EncryptionError
from otaku.formatting import pretty_path
from otaku.integrations import Integrations, default_sections
from otaku.logging import ErrorLog, RequestLog, SystemLog
from otaku.providers import ProviderConfig, Registry, autoconfigure
from otaku.settings import Secrets, migrations, write_atomic
from otaku.settings import config as config_file
from otaku.settings import integrations as integrations_file
from otaku.settings import prompts as prompts_file
from otaku.settings import providers as providers_file
from otaku.settings import state as state_file
from otaku.settings.config import Config
from otaku.settings.config import ConfigError as ConfigError
from otaku.settings.integrations import IntegrationConfig, IntegrationSettings
from otaku.store import DatabaseError as DatabaseError
from otaku.store import Store, is_encrypted


def open_app(root: str | Path | None = None, *, ask_secret: AskSecret | None = None) -> App:
    """The launch over `root` (default the standard state dir — but the
    OTAKU_CONFIG_DIR env var is cli's business: a non-cli caller that
    wants it must resolve it itself and pass the root in).
    `ask_secret` answers the passphrase provider — a frontend passes its
    own prompt; None refuses a passphrase-encrypted store rather than
    block headless. Order: config before cipher before store (the key
    ceremony must precede any content); the worker is built but not
    started — the frontend starts it once it can repaint. A fresh
    database is seeded with the shipped sample stories (`backend.samples`;
    the first by name remembered as the one to land in), the hint
    landing in `LaunchFacts.hint` (the one bold post-scene line); EVERY
    settings-file warning (state, prompts, providers, keys) joins
    `LaunchFacts.notices`, and the store's admin facts go to the system
    log — the ones it marks `show` (a migration that ran, a failed
    backup) joining the notices too: below backend nothing prints
    conversation. The one sanctioned stderr voice below cli is the
    append-only logs' own last-resort write-failure warning; everything
    else on stderr is cli's `otaku:` diagnostics. Raises ConfigError,
    EncryptionError, or DatabaseError when a piece refuses; the caller ends
    with `app.close()`."""
    paths = Paths.resolve(root)
    config, providers, integration_settings, notices = _load_config(paths)
    prompts_file.write_stub(paths.prompts_file)
    _, prompt_warnings = prompts_file.load(paths.prompts_file)
    notices += prompt_warnings
    cipher = _unlock_cipher(config, paths, ask_secret=ask_secret)
    state, state_warnings = state_file.load(paths.state_file)
    notices += state_warnings
    # ONE providers dict for the whole app: the registry the panel
    # updates is what every resolution reads, so a provider added or
    # edited there is visible everywhere at once. The invariant: names
    # are the stable handle — a config may swap under a running pass,
    # which resolves its client by name.
    errors = ErrorLog(paths.logs_dir)
    registry = Registry(
        providers,
        request_sink=RequestLog(paths.logs_dir, cipher),
        error_sink=errors,
        smooth=config.smooth_streaming,
    )
    # A section named for no supported provider is not served, and the
    # file is the user's: it stays, and the launch says it is passed over.
    notices += [
        f"Ignoring provider section [{name}]: no supported provider is named so."
        for name in registry.ignored
    ]
    integrations = Integrations.bind(integration_settings)
    # A remembered model whose provider is still configured resumes; a
    # stale one is reported and cleared — the app opens modelless and
    # every model-facing door says so until a pick.
    stale: dict[str, str] = {}
    remembered = state.model_ref
    if state.model and (remembered is None or remembered.provider not in providers):
        notices += [
            f"The remembered model ({state.model}) names no configured provider.",
            NO_MODEL_HINT,
        ]
        stale["model"] = ""
    # A pinned extraction model the same way — dropped with a notice, and
    # extraction follows the model played with.
    pinned = state.extraction_ref
    if state.extraction_model and (pinned is None or pinned.provider not in providers):
        notices.append(
            f"The remembered extraction model ({state.extraction_model}) names no configured "
            "provider; lore extraction uses the model you play with."
        )
        stale["extraction_model"] = ""

    fresh = not paths.database_file.exists()
    store = Store.open(
        paths.database_file, cipher, backups_dir=paths.backups_dir, keep=config.backups
    )
    # The store's admin facts are the system log's — except the ones it
    # marks `show`: a ladder that ran, a backup that failed. Those the
    # user is told, before the banner, with the launch's other reports.
    system_log = SystemLog(paths.logs_dir)
    for note in store.notes:
        system_log.record(note.text)
        if note.show:
            notices.append(note.show)
    # The files folder, swept against what the rows still name — what a
    # crash between a delete's rows and its files left behind.
    swept = store.sweep_files()
    if swept:
        system_log.record(f"files folder swept: {swept} unreferenced file(s) removed")
    # The worker's own store connection (WAL makes the concurrent write
    # safe), opened lazily on its thread; keep=0 — the store's open
    # above owns the daily snapshot. It exists whatever [lore_extraction]
    # says: `enabled` only gates the idle scheduling, so a forced pass
    # always has its one path.
    worker = Worker(
        lambda: Store.open(paths.database_file, cipher, backups_dir=paths.backups_dir, keep=0),
        registry,
        system_log,
        errors=errors,
        idle_seconds=config.idle_seconds,
    )
    try:
        app = App.start(
            config=config,
            paths=paths,
            store=store,
            providers=registry,
            integrations=integrations,
            worker=worker,
        )
    except BaseException:
        store.close()
        raise
    if stale:
        app._update_state_config(best_effort=True, **stale)
    notices += app._make_current()
    notices += domain_settings.unusable_variables()
    hint = ""
    if fresh and config.seed_sample:
        notices += samples.seed(app)
        hint = samples.HINT if Api(app).settings.get().last_story is not None else ""
    app._launch_facts = LaunchFacts(
        tuple(notices), hint, _newer_version(state.update_latest_version)
    )
    # Everything above ran on the caller's thread; from here every call
    # runs on the app's own (`backend.app.thread`).
    app._thread.start()
    return app


def request_log(
    root: str | Path | None = None, *, ask_secret: AskSecret | None = None
) -> RequestLog:
    """The request log, unlocked the way the app unlocks — cli's one door
    to the sealed bodies. `ask_secret` answers the passphrase provider,
    exactly as at launch: a sealed log stays readable to whoever can
    open the store. Raises ConfigError/EncryptionError."""
    paths = Paths.resolve(root)
    config, _, _, _ = _load_config(paths)
    return RequestLog(paths.logs_dir, _unlock_cipher(config, paths, ask_secret=ask_secret))


def system_log(root: str | Path | None = None) -> SystemLog:
    return SystemLog(Paths.resolve(root).logs_dir)


def error_log(root: str | Path | None = None) -> ErrorLog:
    return ErrorLog(Paths.resolve(root).logs_dir)


# ---------- launch internals ----------


def _newer_version(latest_version: str) -> str:
    """`latest_version` where it is a later version than this otaku's,
    compared number by number — 0.10.0 is later than 0.9.0; "" otherwise,
    and where either is not plain numbers."""
    theirs, ours = _version_numbers(latest_version), _version_numbers(__version__)
    return latest_version if theirs and ours and theirs > ours else ""


def _version_numbers(version: str) -> tuple[int, ...]:
    parts = version.split(".")
    return tuple(int(part) for part in parts) if all(part.isdigit() for part in parts) else ()


def _load_config(
    paths: Paths,
) -> tuple[Config, dict[str, ProviderConfig], IntegrationSettings, list[str]]:
    """The config, the providers and the integrations (typed by
    `settings.providers` and `settings.integrations` — no conversion
    anywhere), and the notices to show: the files written at first run,
    migrated to the current shape always — first run included, so an
    autoconfigured plain api key (omlx's, say) is sealed by the very
    launch that wrote it, and a typed web password replaced by its hash
    — and sealed api keys resolved for the app, the providers' and
    the integrations' alike (one that will not open is warned about and
    its section runs keyless). Raises ConfigError when a file does not
    parse."""
    paths.ensure_tree()
    notices: list[str] = []
    if not paths.config_file.exists():
        write_atomic(paths.config_file, Config().to_toml())
        if not paths.providers_file.exists():
            write_atomic(paths.providers_file, providers_file.render(autoconfigure()))
        if not paths.integrations_file.exists():
            write_atomic(
                paths.integrations_file,
                integrations_file.render(default_sections()),
            )
        notices.append(f"Created {pretty_path(paths.config_file)}")
    secrets = Secrets(
        seal=_sealer(paths),
        is_sealed=encryption.is_sealed,
        hash=passwords.hash,
        is_hashed=passwords.is_hashed,
    )
    migrations.migrate(paths.settings_files, secrets, autoconfigure(), default_sections())
    config = config_file.load(paths.config_file)
    if config.web_password and not passwords.is_hashed(config.web_password):
        # The migration left it plain — the file could not be written, or
        # the value is not a string: hashed in memory for this run, and
        # in the file by the next launch that can. Nothing past this line
        # sees it typed.
        config = replace(config, web_password=passwords.hash(config.web_password))
    providers, key_warnings = _resolve_api_keys(paths, providers_file.load(paths.providers_file))
    integration_settings = integrations_file.load(paths.integrations_file)
    integrations, more_warnings = _resolve_api_keys(paths, integration_settings.integrations)
    integration_settings = replace(integration_settings, integrations=integrations)
    return config, providers, integration_settings, notices + key_warnings + more_warnings


def _unlock_cipher(config: Config, paths: Paths, *, ask_secret: AskSecret | None = None) -> Cipher:
    """The content-plane cipher over the configured encryption. Refuses
    an encrypted database whose keystore is missing BEFORE the ceremony
    could mint a fresh key over it — unlock would otherwise make every
    sealed row permanently unreadable. Raises EncryptionError."""
    # `section`, not `encryption`: that name is the package's here.
    section = config.encryption
    if (
        section.provider != "none"
        and is_encrypted(paths.database_file) is True
        and not paths.keys_file.exists()
    ):
        raise EncryptionError(
            f"{pretty_path(paths.keys_file)} is missing, but "
            f"{pretty_path(paths.database_file)} is encrypted. Its content can only "
            "be read with the key that keystore holds — restore it from backup "
            "(together with its KEK), or move the database aside."
        )
    try:
        return encryption.unlock(
            section.provider,
            keys_file=paths.keys_file,
            kek_file=paths.kek_file,
            service=paths.keychain_service,
            retrieve_command=section.retrieve_command,
            ask=ask_secret,
        )
    except EncryptionError as e:
        raise EncryptionError(f"Could not unlock encryption: {e}") from e


def _sealer(paths: Paths) -> Callable[[str], str]:
    """The seal function migrations run under: best-effort, per their
    contract — a value that cannot seal comes back unchanged and stays
    plain in the file for the next launch to retry."""

    def best_effort(value: str) -> str:
        try:
            return encryption.seal(
                value, key_file=paths.config_key_file, service=paths.keychain_service
            )
        except SealedError:
            return value

    return best_effort


# The two settings types that carry a sealed api key, resolved alike.
_Keyed = TypeVar("_Keyed", ProviderConfig, IntegrationConfig)


def _resolve_api_keys(
    paths: Paths, providers: dict[str, _Keyed]
) -> tuple[dict[str, _Keyed], list[str]]:
    """The providers with sealed api keys opened for the app, plus a
    warning line per key that would not open — its provider keeps an
    empty key, so requests go out unauthenticated rather than with a
    dead token. The sealing key is fetched once for the whole pass — a
    launch never asks the OS keychain per provider."""
    resolved: dict[str, _Keyed] = {}
    warnings: list[str] = []
    unseal = encryption.opener(key_file=paths.config_key_file, service=paths.keychain_service)
    for name, config in providers.items():
        if encryption.is_sealed(config.api_key):
            try:
                config = replace(config, api_key=unseal(config.api_key))
            except SealedError as e:
                warnings.append(
                    f"The api key for {name!r} cannot be unsealed ({e}); "
                    "enter it again in the model picker."
                )
                config = replace(config, api_key="")
        resolved[name] = config
    return resolved, warnings
