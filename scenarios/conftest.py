"""Scenario fixtures: the scripted server and an assembled application."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from scenarios.support.harness import App, launch
from scenarios.support.server import ModelServer


@pytest.fixture(scope="session")
def _session_server() -> Iterator[ModelServer]:
    server = ModelServer()
    yield server
    server.close()


@pytest.fixture
def server(_session_server: ModelServer) -> Iterator[ModelServer]:
    """The scripted model server, reset to the default script per test."""
    _session_server.reset()
    yield _session_server
    _session_server.reset()


@pytest.fixture
def app(server: ModelServer, tmp_path: Path) -> Iterator[App]:
    """A freshly launched application over a throwaway state dir."""
    application = launch(tmp_path / "state", server)
    yield application
    application.close()
