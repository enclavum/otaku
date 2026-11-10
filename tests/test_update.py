"""The update dispatcher — the install detection.

The contract: "source" wins over everything (a git checkout is updated
by git, wherever its venv lives); otherwise the interpreter's prefix
names the installer — Homebrew's Cellar, uv's tools dir, pipx's venvs —
and anything unrecognized falls back to pip.
"""

import sys
from pathlib import Path

import pytest

from otaku import update
from otaku.update import install_kind


class TestInstallKind:
    def test_a_source_checkout_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _runs_from(monkeypatch, "/opt/homebrew/Cellar/otaku/0.2.2", checkout=Path("/src/otaku"))
        assert install_kind() == "source"

    def test_homebrew_by_its_cellar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _runs_from(monkeypatch, "/opt/homebrew/Cellar/otaku/0.2.2/libexec")
        assert install_kind() == "brew"

    def test_uv_by_its_tools_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _runs_from(monkeypatch, "/home/u/.local/share/uv/tools/otaku")
        assert install_kind() == "uv"

    def test_pipx_by_its_venvs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _runs_from(monkeypatch, "/home/u/.local/pipx/venvs/otaku")
        assert install_kind() == "pipx"

    def test_anything_else_is_pip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _runs_from(monkeypatch, "/opt/miniconda3/envs/play")
        assert install_kind() == "pip"


def _runs_from(monkeypatch: pytest.MonkeyPatch, prefix: str, checkout: Path | None = None) -> None:
    """This otaku as if run by the interpreter at `prefix`, from the git
    checkout `checkout` or from an install."""
    monkeypatch.setattr(sys, "prefix", prefix)
    monkeypatch.setattr(update, "checkout_folder", lambda: checkout)
