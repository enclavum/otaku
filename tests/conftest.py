"""Shared fixtures + isolation for the otaku test suite.

The autouse `_isolate` fixture is the safety net: it redirects every
`~/.otaku` path constant into a per-test tmp dir, points the database at a
throwaway sqlite file, replaces the OS-keychain shell-outs with an in-memory
store, and clears the per-process client cache. No test may touch the real
home directory, the real keychain, or the network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from otaku import config
from otaku.storage import crypto
from otaku.storage.crypto import Cipher
from otaku.storage.store import Store


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect all persistent state into `tmp_path` and neutralise the
    keychain + client cache. Autouse — every test gets a clean world."""
    home = tmp_path / "home"
    otaku_dir = home / ".otaku"
    otaku_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    cfg_path = otaku_dir / "config.toml"
    monkeypatch.setattr(config, "CONFIG_DIR", otaku_dir)
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config, "LAST_MODEL_PATH", otaku_dir / "last_model")
    monkeypatch.setattr(config, "MODEL_DEFAULTS_PATH", otaku_dir / "model_defaults.json")
    # `config.load(path=CONFIG_PATH)` captured the old default at def time;
    # rebind it so the no-arg call sites (cli.py) hit the isolated path too.
    monkeypatch.setattr(config.load, "__defaults__", (cfg_path,))

    monkeypatch.setattr(crypto, "CONFIG_DIR", otaku_dir)
    monkeypatch.setattr(crypto, "KEYSTORE_PATH", otaku_dir / "keys.json")
    monkeypatch.setattr(crypto, "DISK_KEK_PATH", otaku_dir / "kek.key")

    # Database goes to a throwaway file, overriding whatever config.toml says.
    monkeypatch.setenv(config.DATABASE_URL_ENV, f"sqlite:///{tmp_path / 'history.db'}")

    # In-memory keychain so the default `keychain` provider works in-process.
    _kc: dict[str, bytes] = {}
    monkeypatch.setattr(crypto, "_keychain_tool", lambda: "/usr/bin/fake-keychain")
    monkeypatch.setattr(crypto, "_keychain_get", lambda: _kc.get("kek"))

    def _kc_put(kek: bytes) -> None:
        _kc["kek"] = kek

    monkeypatch.setattr(crypto, "_keychain_put", _kc_put)

    # A cached probe result from a previous test must never leak forward.
    from otaku.client import _client_cache

    _client_cache.clear()

    yield otaku_dir

    _client_cache.clear()


# ---------- fixtures ----------


@pytest.fixture
def cipher() -> Cipher:
    return Cipher(b"k" * 32)


@pytest.fixture
def store(tmp_path: Path, cipher: Cipher) -> Iterator[Store]:
    s = Store.open(f"sqlite:///{tmp_path / 'store.db'}", cipher)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def ro_store(tmp_path: Path, cipher: Cipher) -> Iterator[Store]:
    s = Store.open(f"sqlite:///{tmp_path / 'ro.db'}", cipher, read_only=True)
    try:
        yield s
    finally:
        s.close()
