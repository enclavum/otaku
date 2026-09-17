"""Config migrations: the one way the app edits the settings files.

This module holds the shape-change tables themselves and `migrate`, the
whole launch step; `surgery` is the toolkit every edit is built from,
`providers_file` the moves over providers.toml, `prompt_texts` the
refreshed templates for prompts.toml, and `state_file` the thinking
level's move out of state.toml. Everything here is idempotent and
convergent: it all simply reruns at every launch — no version stamp to
trust, no one-shot step whose half-state could stick — so a crash
between writes, a hand edit, or a launch that could not finish heals on
the next one. A file is written only when something actually changed.
"""

import contextlib
from collections.abc import Callable

from otaku.settings import Secrets, SettingsFiles, row, write_atomic
from otaku.settings.migrations.config_file import hash_plain_password
from otaku.settings.migrations.prompt_texts import (
    EXTRACT_0_2_2,
    EXTRACT_0_3_0,
    HISTORY_0_3_0,
    STORY_SO_FAR_0_3_0,
    refresh_template,
    rename_template,
    update_prompts,
)
from otaku.settings.migrations.providers_file import (
    ensure_providers,
    move_providers,
    seal_api_keys,
)
from otaku.settings.migrations.state_file import move_think
from otaku.settings.migrations.surgery import (
    Migration,
    apply_migrations,
    drop_key_everywhere,
    ensure_key,
    ensure_section,
    rename_key,
    rename_section,
    set_key,
    update_config,
    update_providers,
)
from otaku.settings.prompts import (
    EXTRACT_DEFAULT,
    JOURNAL_HISTORY_DEFAULT,
    SCENE_HISTORY_DEFAULT,
)
from otaku.settings.providers import ProviderConfig

__all__ = [
    "PROMPT_CACHE_ROW",
    "Migration",
    "apply_migrations",
    "ensure_key",
    "ensure_section",
    "migrate",
    "set_key",
    "update_providers",
]


# config.toml's shape-change table, oldest first: one factory call per
# change across app versions, each safe to re-run on any config the app
# ever wrote.
_CONFIG_MIGRATIONS: list[Migration] = [
    # 0.4.0 — [ui] becomes [terminal], which is what it always held: one
    # frontend's looks. It ran FIRST so every step below names the new
    # section and finds it — including the one that would otherwise add
    # a second copy beside the old one.
    rename_section("ui", "terminal"),
    # 0.2.2 — dialogue coloring arrives with the section, under the name
    # it has now: a config old enough to lack it never had the old one
    # either, so there is nothing for the rename above to have caught.
    ensure_section(
        "terminal",
        "[terminal]\n"
        + row(
            'dialogue_color = "auto"',
            'spoken lines: "auto" fits the background; a color name ("cyan") or #rrggbb',
        )
        + "\n"
        + row("dialogue_bold = false", "also bold the spoken lines"),
        after="settings",
    ),
    # 0.4.0 — the web frontend arrives with the address it listens on.
    ensure_section(
        "web",
        "[web]\n"
        + row(
            'host = "localhost"',
            '"localhost": reachable from this machine only; "0.0.0.0": from the whole '
            "network — set https and a password first",
        )
        + "\n"
        + row("port = 9600", "the port `otaku web` listens on"),
        after="terminal",
    ),
    # 0.4.0 — the background stops being guessed in silence. The ask
    # cannot work everywhere (Windows has no terminal to interrogate),
    # so the reader gets the say, at the head of [terminal] where the rendered
    # file puts it.
    ensure_key(
        "terminal",
        "theme",
        row(
            'theme = "auto"',
            '"auto" asks the terminal and takes dark when it will not say; or "light"/"dark"',
        ),
        # No `after`: it heads [terminal] in the rendered file.
    ),
    # 0.4.0 — /set notification arrives, and names the sound it plays.
    ensure_key(
        "settings",
        "notification_sound",
        row(
            'notification_sound = "default"',
            'what /set notification plays: "default" is the platform\'s own, else a path',
        ),
        after="smooth_streaming",
    ),
    # 0.4.0 — the context budget arrives. Founded at 0: the window a
    # model advertises is the one it can use, and a reader who wants the
    # prompt kept smaller than that says so.
    ensure_key(
        "context",
        "max_context",
        row(
            "max_context = 0",
            "the prompt may use at most this many tokens; 0 = the model's own max context",
        ),
        after="min_tail_messages",
    ),
    # 0.4.0 — tail_messages says what it always meant: a MINIMUM. The
    # value the user set carries over under the new name.
    rename_key(
        "context",
        "tail_messages",
        "min_tail_messages",
        lambda value: row(
            f"min_tail_messages = {value}", "at least this many recent messages kept verbatim"
        ),
    ),
    # 0.5.0 — the web frontend learns TLS and a password, both off. A
    # config written before them was written for a loopback server,
    # where neither buys anything; the keys land so an upgrader SEES
    # that the choice exists once their host stops being loopback.
    ensure_key(
        "web",
        "https",
        row(
            "https = false",
            "RECOMMENDED to turn on when the host is not local; the certificate lives in "
            "cert/: drop in your own, or one is generated",
        ),
        after="port",
    ),
    ensure_key(
        "web",
        "password",
        row(
            'password = ""',
            "RECOMMENDED to set when the host is not local; typed in plain text, it is "
            "replaced by its hash at the next launch",
        ),
        after="https",
    ),
]


# prompts.toml's shape-change table: the stub materializes every template,
# so a changed built-in must be carried to existing files — and only into
# files still holding the superseded shipped text, byte-exact (an edited
# template never matches and is never touched).
_PROMPT_MIGRATIONS: list[Migration] = [
    # 0.3.0 — journals become the record of presence: one per character
    # present, silent bystanders included, arrivals and departures named.
    refresh_template("extract_prompt", EXTRACT_0_2_2, EXTRACT_DEFAULT),
    # 0.4.0 — the two rollups say WHOSE history each is: the story-so-far
    # over scene summaries, and a character's own over their journal.
    # Values ride along untouched, edited or shipped.
    rename_template("story_so_far_prompt", "scene_history_prompt"),
    rename_template("history_prompt", "journal_history_prompt"),
    # 0.4.0 — the journal rollup keeps the entries' first-person voice; a
    # file still holding the shipped third-person text follows. AFTER the
    # rename, so one launch heals a file however far it got.
    refresh_template("journal_history_prompt", HISTORY_0_3_0, JOURNAL_HISTORY_DEFAULT),
    # 0.4.0 — the language rule stops spelling "do not answer in English":
    # run without thinking (as extraction is), a model can read that
    # negation as the command and answer an English story in another
    # tongue. Every lore template now states the rule positively. The
    # journal template's ride arrives with the refresh above; these carry
    # the other two, whose 0.2.2 and 0.3.0 texts are identical.
    refresh_template("extract_prompt", EXTRACT_0_3_0, EXTRACT_DEFAULT),
    refresh_template("scene_history_prompt", STORY_SO_FAR_0_3_0, SCENE_HISTORY_DEFAULT),
]


# The prompt_cache row as the file spells it — ONE rendering, shared by
# the migration below and the picker's section founding
# (`backend.api.providers.save_field`), so an upgraded file and a
# freshly founded section carry the same line.
PROMPT_CACHE_ROW = row(
    'prompt_cache = "5m"', 'prompt caching: "off" | "5m" | "1h" — 1h suits slow-paced play'
)


def _provider_migrations(
    seal: Callable[[str], str], is_sealed: Callable[[str], bool]
) -> list[Migration]:
    """providers.toml's shape-change table — a function, unlike the
    config table above, because its entries need the launch's sealer.
    Its sections carry the user's own names, so an entry here sweeps all
    of them — and runs after the move from an old config, so it cleans a
    section the same way wherever the section came from."""
    return [
        # 0.2.2 — thinking support became class knowledge of the provider.
        drop_key_everywhere("supports_thinking"),
        # 0.2.2 — api keys live sealed; a plain one (hand-typed, or left
        # by a launch that could not seal) is sealed as soon as possible.
        seal_api_keys(seal, is_sealed),
        # 0.4.0 — prompt caching arrives, on where the provider honours
        # cache breakpoints: the key lands in the file so an upgrader
        # SEES the setting exists; what a user already set stays. Named
        # sections only — the section's name is what picks the marking
        # client, so [openrouter] is exactly the section the key governs.
        # After keep_alive, which is the last key a provider section
        # renders before this one — and optional, so a section without it
        # falls through to the section's end, which is the same place.
        ensure_key("openrouter", "prompt_cache", PROMPT_CACHE_ROW, after="keep_alive"),
    ]


def migrate(
    files: SettingsFiles, secrets: Secrets, provider_defaults: dict[str, ProviderConfig]
) -> None:
    """The whole launch step over the settings files, in order: the
    config table (a typed web password replaced by its hash last), the
    provider move, the given providers' sections ensured, the providers
    table (plain api keys sealed; an unsealable line stays for the next
    launch — and a section founded just above gaining what the table
    adds, in the same launch), the prompt-template refreshes, and the
    thinking level an older state.toml still holds moved to its model.
    providers.toml itself converges too: missing beside an existing
    config — a crash between the first-run writes, a hand deletion — it
    is founded empty here, for the ensured sections to fill. A missing
    config is bootstrap's business, and failures are swallowed — a
    migration is never worth a launch."""
    # The password is hashed after the shape moves, so a config that is
    # still gaining its [web] rows has them before this looks for one.
    update_config(
        files.config,
        files.backups_dir,
        [*_CONFIG_MIGRATIONS, hash_plain_password(secrets.hash, secrets.is_hashed)],
    )
    move_providers(files.config, files.providers, files.backups_dir)
    if files.config.exists() and not files.providers.exists():
        with contextlib.suppress(OSError):
            write_atomic(files.providers, "")
    ensure_providers(files.providers, files.backups_dir, provider_defaults)
    update_providers(
        files.providers,
        files.backups_dir,
        _provider_migrations(secrets.seal, secrets.is_sealed),
    )
    update_prompts(files.prompts, files.backups_dir, _PROMPT_MIGRATIONS)
    move_think(files.state, files.models)
