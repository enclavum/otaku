"""The launch's reading of the update check: the version found announced
is named on the banner only where it is later than this otaku, compared
number by number."""

import pytest

from otaku.backend import launch


class TestNewerVersion:
    @pytest.fixture(autouse=True)
    def _running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(launch, "__version__", "0.9.0")

    def test_a_later_version_is_named(self) -> None:
        assert launch._newer_version("0.9.1") == "0.9.1"
        assert launch._newer_version("1.0.0") == "1.0.0"

    def test_each_number_is_compared_as_a_number(self) -> None:
        assert launch._newer_version("0.10.0") == "0.10.0"

    def test_the_same_or_an_earlier_version_is_not(self) -> None:
        assert launch._newer_version("0.9.0") == ""
        assert launch._newer_version("0.8.9") == ""

    def test_nothing_known_or_not_numbers_is_not(self) -> None:
        assert launch._newer_version("") == ""
        assert launch._newer_version("0.10.0rc1") == ""
