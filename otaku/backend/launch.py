"""The launch: one state dir becomes a live Session — as functions, not a
class; the session itself is the product.

`open_session` is the whole composition — paths, config (first-run write
+ convergent migrations), the key ceremony (the content plane's cipher,
shared by the store and the request log), providers (sealed keys opened,
values injected into the registry), store, worker, sample seeding — and
it never prints: what a frontend should say lands in `session.notices`.
The module also carries the launch pieces cli needs alone (`otaku logs
requests` unlocks the way the app does).
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path

from otaku import encryption
from otaku.backend import passwords
from otaku.backend.api import transfer
from otaku.backend.files import RawFile, read_picture, save
from otaku.backend.paths import Paths
from otaku.backend.session import NO_MODEL_HINT, Refused, Session
from otaku.encryption import AskSecret, Cipher, EncryptionError, SealedError
from otaku.formatting import pretty_path
from otaku.logging import ErrorLog, RequestLog, SystemLog
from otaku.providers import ProviderConfig, Registry, autoconfigure
from otaku.settings import Secrets, migrations, write_atomic
from otaku.settings import config as config_file
from otaku.settings import prompts as prompts_file
from otaku.settings import providers as providers_file
from otaku.settings import state as state_file
from otaku.settings.config import Config
from otaku.store import Store, is_encrypted
from otaku.store.schema import Attachment
from otaku.worker import Worker

# A sample's pictures ship beside its document, named for the message
# they belong to: `river.15.1.jpg` is the first picture of river.md's
# 15th message. The document itself carries no picture — the export
# format never does — so the first launch puts them on the message
# through the app's own intake, as a reader's attachment would land.
_SAMPLE_PICTURE = re.compile(r"^(?P<stem>.+)\.(?P<message>\d+)\.(?P<order>\d+)\.[A-Za-z0-9]+$")
_SAMPLES_NOTICE = (
    "Sample stories were imported so you can look around — type to play on, "
    "/stories switches between them, or see every command with /help · "
    "/model chooses a model · /new starts your own play."
)


def open_session(root: str | Path | None = None, *, ask_secret: AskSecret | None = None) -> Session:
    """The launch over `root` (default the standard state dir — but the
    OTAKU_CONFIG_DIR env var is cli's business: a non-cli caller that
    wants it must resolve it itself and pass the root in).
    `ask_secret` answers the passphrase provider — a frontend passes its
    own prompt; None refuses a passphrase-encrypted store rather than
    block headless. Order: config before cipher before store (the key
    ceremony must precede any content); the worker is built but not
    started — the frontend starts it once it can repaint. A fresh
    database is seeded with the shipped sample stories (`otaku/samples`,
    the package's own data; the session lands in the first by name), the
    hint landing in `session.notice`
    (the one bold post-scene line); EVERY settings-file warning (state,
    prompts, providers, keys) joins `session.notices`, and the store's
    admin facts go to the system log — the ones it marks `show` (a
    migration that ran, a failed backup) joining `session.notices` too:
    below backend nothing prints conversation. The one sanctioned stderr voice below cli is the
    append-only logs' own last-resort write-failure warning; everything
    else on stderr is cli's `otaku:` diagnostics. Raises ConfigError,
    EncryptionError, or DatabaseError when a piece refuses; the caller ends
    with `session.close()`."""
    paths = Paths.resolve(root)
    config, providers, notices = _load_config(paths)
    prompts_file.write_stub(paths.prompts_file)
    prompts, prompt_warnings = prompts_file.load(paths.prompts_file)
    notices += prompt_warnings
    cipher = _unlock_cipher(config, paths, ask_secret=ask_secret)
    state, state_warnings = state_file.load(paths.state_file)
    notices += state_warnings
    # ONE providers dict for the whole session: the registry the panel
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
    # A remembered model whose provider is still configured resumes; a
    # stale one is reported and skipped — the session opens modelless
    # and every model-facing door says so until a pick.
    if state.model and (state.provider not in providers or not state.bare_model):
        notices += [
            f"The remembered model ({state.model}) names no configured provider.",
            NO_MODEL_HINT,
        ]
        state = replace(state, model="")

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
    swept = store.stories.sweep_files()
    if swept:
        system_log.record(f"files folder swept: {swept} unreferenced file(s) removed")
    # The worker's own store connection (WAL makes the concurrent write
    # safe), opened lazily on its thread; keep=0 — the session's open
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
        session = Session.start(
            config=config,
            prompts=prompts,
            paths=paths,
            store=store,
            registry=registry,
            worker=worker,
            state=state,
        )
    except BaseException:
        store.close()
        raise
    session.notices = notices + session.notices
    if fresh and config.seed_sample:
        _seed_samples(session)
    return session


def request_log(
    root: str | Path | None = None, *, ask_secret: AskSecret | None = None
) -> RequestLog:
    """The request log, unlocked the way the app unlocks — cli's one door
    to the sealed bodies. `ask_secret` answers the passphrase provider,
    exactly as at launch: a sealed log stays readable to whoever can
    open the store. Raises ConfigError/EncryptionError."""
    paths = Paths.resolve(root)
    config, _, _ = _load_config(paths)
    return RequestLog(paths.logs_dir, _unlock_cipher(config, paths, ask_secret=ask_secret))


def system_log(root: str | Path | None = None) -> SystemLog:
    return SystemLog(Paths.resolve(root).logs_dir)


def error_log(root: str | Path | None = None) -> ErrorLog:
    return ErrorLog(Paths.resolve(root).logs_dir)


# ---------- launch internals ----------


def _load_config(paths: Paths) -> tuple[Config, dict[str, ProviderConfig], list[str]]:
    """The config, the providers (typed by `settings.providers` — no
    conversion anywhere), and the notices to show: the files written at
    first run, migrated to the current shape always — first run
    included, so an autoconfigured plain api key (omlx's, say) is sealed
    by the very launch that wrote it, and a typed web password replaced
    by its hash — and sealed api keys resolved for the session (one
    that will not open is warned about and its provider runs keyless).
    Raises ConfigError when a file does not parse."""
    paths.ensure_tree()
    notices: list[str] = []
    if not paths.config_file.exists():
        write_atomic(paths.config_file, Config().to_toml())
        if not paths.providers_file.exists():
            write_atomic(paths.providers_file, providers_file.render(autoconfigure()))
        notices.append(f"Created {pretty_path(paths.config_file)}")
    secrets = Secrets(
        seal=_sealer(paths),
        is_sealed=encryption.is_sealed,
        hash=passwords.hash,
        is_hashed=passwords.is_hashed,
    )
    migrations.migrate(paths.settings_files, secrets, autoconfigure())
    config = config_file.load(paths.config_file)
    if config.web_password and not passwords.is_hashed(config.web_password):
        # The migration left it plain — the file could not be written, or
        # the value is not a string: hashed in memory for this session, and
        # in the file by the next launch that can. Nothing past this line
        # sees it typed.
        config = replace(config, web_password=passwords.hash(config.web_password))
    providers = providers_file.load(paths.providers_file)
    resolved, key_warnings = _resolve_api_keys(paths, providers)
    return config, resolved, notices + key_warnings


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


def _resolve_api_keys(
    paths: Paths, providers: dict[str, ProviderConfig]
) -> tuple[dict[str, ProviderConfig], list[str]]:
    """The providers with sealed api keys opened for the session, plus a
    warning line per key that would not open — its provider keeps an
    empty key, so requests go out unauthenticated rather than with a
    dead token. The sealing key is fetched once for the whole pass — a
    launch never asks the OS keychain per provider."""
    resolved: dict[str, ProviderConfig] = {}
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


# ---------- the sample stories ----------


def _seed_samples(session: Session) -> None:
    """A database created from scratch is seeded with the shipped sample
    stories, through the import operation's own machinery — native
    imports, so no pass runs and no model is called — and remembered, so
    the user lands (and stays) in the middle of a playable story. Every
    samples/*.md lands, in name order, and the session settles in the
    FIRST of them: the short story is the landing, the rest wait in
    /stories."""
    samples = Path(__file__).parent.parent / "samples"  # the package's own
    landed: list[int] = []
    for file in sorted(samples.glob("*.md")) if samples.is_dir() else []:
        try:
            transfer.import_file(session, file.read_text(encoding="utf-8"), file.name)
        except (Refused, OSError) as e:
            # Best effort, per file: a damaged sample says so and the
            # others still land. A first launch is the worst possible
            # place for a traceback.
            session.notices.append(f"The sample story {file.name} could not be imported ({e}).")
        else:
            if session.story_id is not None:
                landed.append(session.story_id)
                _seed_pictures(session, session.story_id, file)
    if not landed:
        return
    session._switch_to(landed[0])
    session._update_state()
    session.notice = _SAMPLES_NOTICE


def _seed_pictures(session: Session, story_id: int, document: Path) -> None:
    """The sample's pictures, shipped beside its document
    (`_sample_pictures`), into the story the way a reader's land: read
    through the one intake — sniffed, oriented, downsized, thumbnailed
    — saved under the story's number, and put on the message each
    belongs to. Best effort per picture: one that cannot be read, or
    names a message the document lacks, says so and the rest land."""
    names = sorted(p.name for p in document.parent.iterdir()) if document.parent.is_dir() else []
    ids = session._store.stories.get_messages_ids(story_id)
    by_message: dict[int, list[Attachment]] = {}
    for number, name in _sample_pictures(names, document.stem):
        path = document.parent / name
        try:
            if not 1 <= number <= len(ids):
                raise Refused(f"{document.name} has no message {number}")
            picture = read_picture(RawFile(path.read_bytes(), name))
        except (Refused, OSError) as e:
            session.notices.append(f"The sample picture {name} could not be added ({e}).")
            continue
        by_message.setdefault(number, []).append(save(session._store.files, picture, story_id))
    for number, attachments in by_message.items():
        session._store.messages.set_attachments(ids[number - 1], attachments)


def _sample_pictures(names: Iterable[str], stem: str) -> list[tuple[int, str]]:
    """The pictures shipped for the sample `stem` among `names`, each
    with the number of the message it belongs to, in message and then
    picture order (`_SAMPLE_PICTURE`): the shipped layout's one rule."""
    found: list[tuple[int, int, str]] = []
    for name in names:
        match = _SAMPLE_PICTURE.match(name)
        if match is not None and match.group("stem") == stem:
            found.append((int(match.group("message")), int(match.group("order")), name))
    return [(message, name) for message, _order, name in sorted(found)]
