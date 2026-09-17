"""The config.toml move that needs the launch's hands.

`hash_plain_password` promises to replace a typed, non-empty `[web]
password` with what the injected hasher makes of it, keeping the row's
note; to leave alone an empty one and one that is already hashed, so
rerunning it at every launch changes nothing; and to touch nothing else in
the file.
The hasher is a stand-in: this package never reaches the real one.
"""

import tomllib

from otaku.settings.config import Config
from otaku.settings.migrations.config_file import hash_plain_password

CONFIG = (
    "[terminal]\n"
    'theme = "auto"\n'
    "\n"
    "[web]\n"
    'host = "0.0.0.0"              # where it listens\n'
    "https = false\n"
    'password = "hunter2"          # the note\n'
    "\n"
    "[context]\n"
    "head_messages = 20\n"
)


class TestHashPlainPassword:
    def test_a_typed_password_is_replaced_by_its_hash(self) -> None:
        migrated = _hash(CONFIG)
        assert tomllib.loads(migrated)["web"]["password"] == "hashed:hunter2"
        assert 'hunter2"' not in migrated.replace("hashed:hunter2", "")

    def test_the_row_reads_as_a_fresh_config_renders_it(self) -> None:
        hashed = next(row for row in _hash(CONFIG).splitlines() if row.startswith("password"))
        fresh = Config(web_password="hashed:hunter2").to_toml().splitlines()
        assert hashed == next(row for row in fresh if row.startswith("password"))

    def test_nothing_else_in_the_file_moves(self) -> None:
        before = [row for row in CONFIG.splitlines() if not row.startswith("password")]
        after = [row for row in _hash(CONFIG).splitlines() if not row.startswith("password")]
        assert after == before

    def test_a_hash_is_never_hashed_again(self) -> None:
        once = _hash(CONFIG)
        assert _hash(once) is once

    def test_no_password_is_left_as_none(self) -> None:
        empty = CONFIG.replace('"hunter2"', '""')
        assert _hash(empty) is empty

    def test_a_config_with_no_web_section_is_left_alone(self) -> None:
        bare = '[terminal]\ntheme = "auto"\n'
        assert _hash(bare) is bare

    def test_a_config_that_does_not_parse_is_left_alone(self) -> None:
        broken = CONFIG + "[unfinished"
        assert _hash(broken) is broken


def _hash(text: str) -> str:
    return hash_plain_password(
        lambda typed: f"hashed:{typed}", lambda value: value.startswith("hashed:")
    )(text)
