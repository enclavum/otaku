"""Scenario fixtures: the scripted server and an assembled application."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from scenarios.support.harness import App, launch
from scenarios.support.server import ModelServer


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
