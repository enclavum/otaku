"""Scenario fixtures: the scripted server and an assembled application."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from otaku.providers import ALL_CLIENTS
from scenarios.support.harness import App, launch
from scenarios.support.server import ModelServer


@pytest.fixture(autouse=True)
def _no_shell_keys(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The developer's own shell keys stay out of every offline scenario:
    a catalog whose variable is set is founded at launch and would then
    be listed over the real internet. The live smokes read them on
    purpose, and a scenario that wants one sets it itself."""
    if request.node.get_closest_marker("live"):
        return
    for cls in ALL_CLIENTS.values():
        if cls.env_key:
            monkeypatch.delenv(cls.env_key, raising=False)


@pytest.fixture
def server() -> Iterator[ModelServer]:
    """A scripted model server, fresh per test — a worker outliving its
    test (shutdown never joins) talks to a dead port instead of leaking
    requests into the next test's recording."""
    fresh = ModelServer()
    yield fresh
    fresh.close()


@pytest.fixture
def app(server: ModelServer, tmp_path: Path) -> Iterator[App]:
    """A freshly launched application over a throwaway state dir."""
    application = launch(tmp_path / "state", server)
    yield application
    application.close()
