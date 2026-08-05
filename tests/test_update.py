"""The update dispatcher — the pure install detection.

The contract: "source" wins over everything (a git checkout is updated
by git, wherever its venv lives); otherwise the interpreter's prefix
names the installer — Homebrew's Cellar, uv's tools dir, pipx's venvs —
and anything unrecognized falls back to pip.
"""

from pathlib import Path

from otaku.update import install_kind


class TestInstallKind:
    def test_a_source_checkout_wins(self) -> None:
        assert install_kind(Path("/opt/homebrew/Cellar/otaku/0.2.2"), source=True) == "source"

    def test_homebrew_by_its_cellar(self) -> None:
        prefix = Path("/opt/homebrew/Cellar/otaku/0.2.2/libexec")
        assert install_kind(prefix, source=False) == "brew"

    def test_uv_by_its_tools_dir(self) -> None:
        prefix = Path("/home/u/.local/share/uv/tools/otaku")
        assert install_kind(prefix, source=False) == "uv"

    def test_pipx_by_its_venvs(self) -> None:
        prefix = Path("/home/u/.local/pipx/venvs/otaku")
        assert install_kind(prefix, source=False) == "pipx"

    def test_anything_else_is_pip(self) -> None:
        assert install_kind(Path("/opt/miniconda3/envs/play"), source=False) == "pip"
