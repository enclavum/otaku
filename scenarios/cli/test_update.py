"""`otaku update` — the self-updater. Offline, only one of its paths can
play: this suite runs from the source checkout, which the command points
at git instead of touching."""

from pathlib import Path

from scenarios.support.harness import run_otaku


class TestUpdate:
    def test_a_source_checkout_is_pointed_at_git(self, tmp_path: Path) -> None:
        result = run_otaku(tmp_path / "state", "update")
        assert result.returncode == 0
        assert "git pull" in result.stdout
