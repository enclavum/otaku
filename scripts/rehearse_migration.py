#!/usr/bin/env python3
"""Upgrade rehearsal: a PUBLISHED otaku creates real state, this build opens it.

The three migration frameworks — settings, database schema, export format —
are only ever exercised against synthetic fixtures by the suites. This drives
the real thing: an old release from PyPI plays a real story through a pty,
then the local build launches on that state dir and every promise is checked.

    python scripts/rehearse_migration.py                # the whole matrix
    python scripts/rehearse_migration.py 0.2.2          # one version, plain
    python scripts/rehearse_migration.py 0.2.2 --encrypted

Needs `uv` on PATH and a network connection (PyPI, once per version). Nothing
touches the developer's own state dir or model servers: every run gets a
throwaway OTAKU_CONFIG_DIR, and every provider url in the seeded config is
dead-ended except the scripted server the suites already use.

What it proves, per release and per encryption mode: the ladder migrates the
database it finds and says so, the result passes its integrity and foreign-key
checks, and the story reads back THROUGH THE APP (title, system prompt, cast,
summaries, journals — sealed or not); the settings migrations move providers
into their own file, seal a plain api key, back up what they edit, and carry
an unedited prompt template to the new built-in; a second launch migrates
nothing and rewrites nothing; and a document the old release exported still
imports. Run it before every release, and after any change to a schema step,
a settings migration, or the export format.
"""

import fcntl
import json
import os
import pty
import re
import select
import shutil
import sqlite3
import struct
import subprocess
import sys
import termios
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from otaku.store.schema import SCHEMA_VERSION as CURRENT_SCHEMA  # noqa: E402
from scenarios.support.server import ModelServer  # noqa: E402

# Every published release this build claims to migrate. 0.2.0 and earlier
# require Python >= 3.14 and are out of scope. A release already on the
# current schema is still rehearsed — the settings, the export and the
# read-back all matter — it simply has no ladder to run.
VERSIONS = ("0.2.1", "0.2.2", "0.3.0", "0.4.0", "0.4.1", "0.4.2", "0.4.3")
PROVIDER, MODEL = "generic", "test-model"
WORK = Path(os.environ.get("REHEARSAL_DIR", "/tmp/otaku-rehearsal"))

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[78]|\x1b\][^\x07]*\x07")
_BG_QUERY = b"\x1b]11;?"
ENTER = b"\r"


class Otaku:
    """One otaku run in a pty — any binary, so an old release and this build
    are driven the same way."""

    def __init__(self, binary: str, state_dir: str, *, rows: int = 40, cols: int = 100) -> None:
        self._master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        env = os.environ | {
            "OTAKU_CONFIG_DIR": state_dir,
            "TERM": "xterm-256color",
            "COLORFGBG": "",
        }
        self._proc = subprocess.Popen(
            [binary], stdin=slave, stdout=slave, stderr=slave, close_fds=True, env=env
        )
        os.close(slave)
        self._raw = b""

    @property
    def transcript(self) -> str:
        return _ANSI.sub("", self._raw.decode("utf-8", "replace"))

    def send(self, data, settle: float = 0.6) -> None:
        os.write(self._master, data.encode() if isinstance(data, str) else data)
        self._drain(settle)

    def line(self, text: str, settle: float = 1.2) -> None:
        self.send(text, 0.3)
        self.send(ENTER, settle)

    def expect(self, *markers: str, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if all(m in self.transcript for m in markers):
                return
            self._drain(0.3)
        missing = [m for m in markers if m not in self.transcript]
        raise AssertionError(
            f"never saw {missing!r}\n--- last output ---\n{self.transcript[-2000:]}"
        )

    def settle(self, seconds: float = 1.0) -> None:
        self._drain(seconds)

    def quit(self, timeout: float = 20.0) -> int:
        # Right after streamed output the tty is briefly canonical, and a line
        # typed into that window can be swallowed — settle before quitting.
        self._drain(1.5)
        try:
            self.send("/bye", 0.3)
            self.send(ENTER, 0.5)
        except OSError:
            pass  # already gone
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self._proc.poll() is None:
            self._drain(0.3)
        if self._proc.poll() is None:
            self._proc.kill()
        self._drain(0.3)
        os.close(self._master)
        return self._proc.wait()

    def _drain(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            ready, _, _ = select.select([self._master], [], [], left)
            if not ready:
                continue
            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                return
            if not chunk:
                return
            self._raw += chunk
            if _BG_QUERY in chunk:  # answer the theme probe instead of waiting it out
                os.write(self._master, b"\x1b]11;rgb:fafa/fafa/fafa\x07")


class Report:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        (self.passed if condition else self.failed).append(label)
        print(f"  [{'PASS' if condition else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")


# ---------- the old release ----------


def run_in_venv(python: str, code: str, *, check: bool = False) -> subprocess.CompletedProcess:
    """Run `code` under a venv's own interpreter. `-P` and a neutral cwd keep
    the working tree off sys.path — without them a venv python started in the
    repo imports the SOURCE otaku, and the rehearsal silently tests this
    build against itself."""
    return subprocess.run(
        [python, "-P", "-c", code], cwd=str(WORK), check=check, capture_output=True, text=True
    )


def _fresh_venv(venv: Path) -> None:
    """(Re)create `venv` unless it is a REAL venv. macOS prunes /tmp by
    file age, which can gut a reused venv into a husk — a bin/python
    symlink with no pyvenv.cfg. Installing --python against that symlink
    resolves into the BASE interpreter's environment (the developer's
    conda env), overwriting its entry scripts with husk shebangs."""
    if not (venv / "pyvenv.cfg").exists():
        shutil.rmtree(venv, ignore_errors=True)
        subprocess.run(["uv", "venv", "--python", "3.11", "-q", str(venv)], check=True)


def install(version: str) -> tuple[str, str]:
    """The published release in its own venv; returns (binary, python)."""
    venv = WORK / f"v{version.replace('.', '')}"
    if not (venv / "bin/otaku").exists() or not (venv / "pyvenv.cfg").exists():
        _fresh_venv(venv)
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "-q",
                "--python",
                str(venv / "bin/python"),
                f"otaku=={version}",
            ],
            check=True,
        )
    return str(venv / "bin/otaku"), str(venv / "bin/python")


def install_current() -> str:
    """This working tree, built and installed into its own venv."""
    venv = WORK / "current"
    subprocess.run(["uv", "build", "--quiet"], cwd=REPO, check=True)
    version = (REPO / "otaku/__init__.py").read_text().split('__version__ = "')[1].split('"')[0]
    wheel = REPO / f"dist/otaku-{version}-py3-none-any.whl"
    _fresh_venv(venv)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "-q",
            "--reinstall",
            "--python",
            str(venv / "bin/python"),
            str(wheel),
        ],
        check=True,
    )
    return str(venv / "bin/otaku")


def seed_config(python: str, root: Path, server_url: str, *, version: str, encrypted: bool) -> None:
    """The old release writes its OWN default config (its shape, no network),
    then every provider url is dead-ended and the scripted one added. The
    snippet is the OLD release's api — never this build's: `otaku.app`
    up to 0.3.0, the backend's launch module from 0.4.0 on."""
    if tuple(int(n) for n in version.split(".")) < (0, 4, 0):
        code = (
            "from otaku.paths import Paths;"
            "from otaku.app import load_config;"
            f"load_config(Paths.resolve({str(root)!r}))"
        )
    else:
        code = (
            "from otaku.backend.paths import Paths;"
            "from otaku.backend.launch import _load_config;"
            f"_load_config(Paths.resolve({str(root)!r}))"
        )
    run_in_venv(python, code, check=True)

    for name in ("config.toml", "providers.toml"):
        path = root / "configs" / name
        if not path.exists():
            continue
        lines = []
        for line in path.read_text().splitlines():
            if line.startswith("url = "):
                line = 'url = "http://127.0.0.1:1/v1"'  # never the developer's engines
            if encrypted and line.startswith('provider = "none"'):
                line = 'provider = "disk"'  # the zero-friction provider: a key file, no prompt
            lines.append(line)
        path.write_text("\n".join(lines) + "\n")

    # The scripted provider goes wherever this release keeps providers.
    providers_file = root / "configs/providers.toml"
    if providers_file.exists():
        with providers_file.open("a") as f:
            f.write(f'\n[{PROVIDER}]\nurl = "{server_url}"\napi_key = ""\n')
    else:
        with (root / "configs/config.toml").open("a") as f:
            f.write(f'\n[providers.{PROVIDER}]\nurl = "{server_url}"\napi_key = ""\n')

    (root / "configs/state.toml").write_text(f'model = "{PROVIDER}/{MODEL}"\n')


def old_session(binary: str, root: Path, version: str, export_to: Path) -> None:
    """A real story on the old release: messages, a title, a system prompt, a
    forced extraction (scene + cast + journals), and an export."""
    print(f"\n--- {version}: playing a real story ---")
    t = Otaku(binary, str(root))
    t.expect("otaku")
    t.line("The keeper meets me at the gate.")
    t.expect("light went out")
    t.line("I follow her inside.")
    t.expect("light went out")
    t.line("/rename The Gate" if version == "0.2.1" else "/title The Gate")
    t.line("/system You are the narrator of a quiet, careful story.")
    t.line("/extract", settle=6.0)
    t.expect("closed", timeout=45)
    t.line(f"/export {export_to}", settle=3.0)
    print(f"    played and exported (exit {t.quit()})")


# ---------- the checks ----------


def read_through_app(current: str, root: Path) -> dict:
    """The story as THIS build reads it — config, cipher, store. Proves the
    migrated database is not just structurally sound but readable."""
    python = str(Path(current).parent / "python")
    code = (
        "import json;"
        "from otaku.backend.paths import Paths;"
        "from otaku.encryption import unlock;"
        "from otaku.settings import config as config_file;"
        "from otaku.store import Store;"
        f"p = Paths.resolve({str(root)!r});"
        "cfg = config_file.load(p.config_file);"
        "c = unlock(cfg.encryption.provider, keys_file=p.keys_file, kek_file=p.kek_file,"
        " service=p.keychain_service, retrieve_command=cfg.encryption.retrieve_command);"
        "s = Store.open(p.database_file, c, backups_dir=p.backups_dir, keep=0);"
        "st = s.stories.get(1);"
        "ms = s.stories.get_messages(1);"
        "ids = s.stories.get_messages_ids(1);"
        "print(json.dumps({'title': st.title, 'system': st.system, 'messages': len(ms),"
        " 'cast': [c.name for c in s.characters.list(1)],"
        " 'scenes': [sc.summary[:30] for sc in s.scenes.get_current(1, ids)],"
        " 'journals': len(s.journals.list(1))}))"
    )
    out = run_in_venv(python, code)
    rows = [line for line in out.stdout.splitlines() if line.startswith("{")]
    if not rows:
        raise AssertionError(f"the build could not read the store:\n{out.stdout}\n{out.stderr}")
    return json.loads(rows[-1])


def verify(
    current: str,
    root: Path,
    report: Report,
    *,
    stamped: str,
    encrypted: bool,
    settings_before: dict[str, str],
) -> None:
    print("\n--- the store ---")
    conn = sqlite3.connect(root / "database/history.db")
    try:
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        report.check(
            "the schema is at the current version", version == CURRENT_SCHEMA, f"v{version}"
        )
        scenes_ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='scenes'").fetchone()[0]
        chars_ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='characters'"
        ).fetchone()[0]
        report.check(
            "scenes lost UNIQUE (story_id, start_message_id)",
            "UNIQUE (story_id, start_message_id)" not in scenes_ddl,
        )
        report.check(
            "characters gained UNIQUE (story_id, id)", "UNIQUE (story_id, id)" in chars_ddl
        )
        report.check(
            "integrity holds", conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        )
        report.check(
            "foreign keys hold", conn.execute("PRAGMA foreign_key_check").fetchone() is None
        )
    finally:
        conn.close()

    read = read_through_app(current, root)
    report.check(
        "the build reads the story back", read["messages"] >= 4, f"{read['messages']} messages"
    )
    report.check("the title survived", read["title"] == "The Gate", read["title"])
    report.check("the system prompt survived", "narrator" in read["system"])
    report.check("the cast survived", bool(read["cast"]), str(read["cast"]))
    report.check("the scene summaries survived", bool(read["scenes"]), str(read["scenes"]))
    report.check("the journals survived", read["journals"] >= 1, str(read["journals"]))

    if stamped != CURRENT_SCHEMA:
        backups = sorted((root / "database/backups").glob("*schema*"))
        report.check(
            f"a pre-migration backup of v{stamped} exists",
            bool(backups),
            str([b.name for b in backups]),
        )

    if encrypted:
        print("\n--- encryption at rest ---")
        report.check("the keystore exists", (root / "configs/keys.toml").exists())
        kek = root / "configs/kek.key"
        report.check(
            "the disk key exists at 0600",
            kek.exists() and oct(kek.stat().st_mode)[-3:] == "600",
        )
        report.check(
            "story text is not readable on disk",
            b"The keeper meets me" not in (root / "database/history.db").read_bytes(),
        )

    print("\n--- the settings files ---")
    providers = root / "configs/providers.toml"
    report.check("providers.toml exists", providers.exists())
    if providers.exists():
        data = tomllib.loads(providers.read_text())
        report.check("the scripted provider came across", PROVIDER in data, str(list(data)))
        plain = [
            name
            for name, block in data.items()
            if isinstance(block, dict)
            and block.get("api_key")
            and not str(block["api_key"]).startswith("sealed:")
        ]
        report.check("no api key is left in plain text", not plain, str(plain))
    config = tomllib.loads((root / "configs/config.toml").read_text())
    report.check("providers left config.toml", "providers" not in config, str(list(config)))
    # A backup is owed only when the launch edited a settings file — a
    # release already in the converged shape leaves them alone.
    if settings_texts(root) != settings_before:
        report.check(
            "a pre-edit config backup exists",
            bool(sorted((root / "configs/backups").glob("*.toml"))),
        )
    else:
        print("    (the settings needed no edit — no backup owed)")
    prompts = tomllib.loads((root / "configs/prompts.toml").read_text())
    report.check(
        "an unedited template followed the new built-in",
        'anyone named in "speakers" or "characters"' in prompts.get("extract_prompt", ""),
    )


def settings_texts(root: Path) -> dict[str, str]:
    """The settings files as they are, by name — what a launch may edit."""
    return {p.name: p.read_text() for p in sorted((root / "configs").glob("*.toml"))}


def verify_convergence(current: str, root: Path, report: Report) -> None:
    print("\n--- a second launch changes nothing ---")
    before = {p.name: p.read_bytes() for p in sorted((root / "configs").glob("*.toml"))}
    t = Otaku(current, str(root))
    t.expect("otaku")
    t.settle(1.0)
    transcript = t.transcript
    t.quit()
    report.check("no second migration is announced", "Database migrated" not in transcript)
    after = {p.name: p.read_bytes() for p in sorted((root / "configs").glob("*.toml"))}
    changed = [name for name in before if before[name] != after.get(name)]
    report.check("the config files are untouched", not changed, str(changed))


def verify_export(current: str, root: Path, doc: Path, report: Report) -> None:
    print("\n--- the old export, imported by this build ---")
    report.check("the old release wrote an export", doc.exists())
    if not doc.exists():
        return
    lines = doc.read_text().splitlines()
    declared = [row for row in lines if row.startswith("format-version:")]
    print(f"    the document declares {declared}")
    t = Otaku(current, str(root))
    t.expect("otaku")
    t.line(f"/import {doc}", settle=4.0)
    imported = True
    try:
        t.expect("Imported", timeout=45)
    except AssertionError:
        imported = False
    t.quit()
    report.check("the old document imports", imported)


# ---------- the run ----------


def rehearse(version: str, current: str, *, encrypted: bool) -> Report:
    label = f"{version}{' (encrypted)' if encrypted else ''}"
    print(f"\n{'=' * 70}\n=== {label} -> this build\n{'=' * 70}")
    root = WORK / f"state-{version}{'-enc' if encrypted else ''}"
    shutil.rmtree(root, ignore_errors=True)
    doc = WORK / f"export-{version}.md"
    doc.unlink(missing_ok=True)

    report = Report()
    binary, python = install(version)
    server = ModelServer()
    try:
        seed_config(python, root, server.url, version=version, encrypted=encrypted)
        old_session(binary, root, version, doc)
        conn = sqlite3.connect(root / "database/history.db")
        stamped = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
        conn.close()
        settings_before = settings_texts(root)

        print(f"\n--- this build opens the state {version} left (schema v{stamped}) ---")
        t = Otaku(current, str(root))
        t.expect("otaku")
        t.settle(1.5)
        transcript = t.transcript
        report.check("the app launches on the migrated state", "otaku" in transcript)
        if stamped != CURRENT_SCHEMA:
            report.check(
                f"the ladder reports v{stamped} → v{CURRENT_SCHEMA}",
                f"Database migrated (v{stamped} → v{CURRENT_SCHEMA})" in transcript,
            )
        else:
            print(f"    (schema v{stamped} is already current — no ladder to run)")
        t.line("The story continues after the upgrade.")
        t.expect("light went out")
        code = t.quit()
        report.check("a turn plays after the upgrade", code == 0, f"exit {code}")

        verify(
            current,
            root,
            report,
            stamped=stamped,
            encrypted=encrypted,
            settings_before=settings_before,
        )
        verify_convergence(current, root, report)
        verify_export(current, root, doc, report)
    finally:
        server.close()

    print(f"\n=== {label}: {len(report.passed)} passed, {len(report.failed)} failed ===")
    for name in report.failed:
        print(f"    FAILED: {name}")
    return report


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    encrypted_only = "--encrypted" in sys.argv
    WORK.mkdir(parents=True, exist_ok=True)
    current = install_current()

    if args:
        runs = [(v, encrypted_only) for v in args]
    else:
        runs = [(v, enc) for v in VERSIONS for enc in (False, True)]

    reports = [(v, enc, rehearse(v, current, encrypted=enc)) for v, enc in runs]
    print(f"\n{'=' * 70}")
    failed = 0
    for version, enc, report in reports:
        failed += len(report.failed)
        label = f"{version}{' encrypted' if enc else ' plain':>10}"
        print(f"  {label}: {len(report.passed):>2} passed, {len(report.failed)} failed")
    print(f"{'=' * 70}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
