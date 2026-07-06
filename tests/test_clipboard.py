"""Tests for clipboard copy: native-tool preference with OSC 52 fallback.

subprocess.run and shutil.which are always mocked so no test touches the real
system clipboard.
"""

from __future__ import annotations

import base64
import subprocess

import pytest

from otaku.chat import clipboard


def test_native_candidates_nonempty() -> None:
    cands = clipboard._native_candidates()
    assert cands
    assert all(isinstance(label, str) and isinstance(argv, list) for label, argv in cands)


def test_copy_uses_first_available_native(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: dict = {}
    monkeypatch.setattr(clipboard, "_native_candidates", lambda: [("faketool", ["faketool", "-x"])])
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/usr/bin/" + name)

    def fake_run(argv, input=None, **kw):
        recorded["argv"] = argv
        recorded["input"] = input

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)
    assert clipboard.copy("hello world") == "faketool"
    assert recorded["argv"] == ["faketool", "-x"]
    assert recorded["input"] == b"hello world"


def test_copy_skips_missing_tool_and_tries_next(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clipboard, "_native_candidates", lambda: [("a", ["a"]), ("b", ["b"])])
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None if name == "a" else "/bin/b")
    used: dict = {}
    monkeypatch.setattr(clipboard.subprocess, "run", lambda argv, **kw: used.update(argv=argv))
    assert clipboard.copy("x") == "b"
    assert used["argv"] == ["b"]


def test_copy_falls_back_to_osc52_when_no_native(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(clipboard, "_native_candidates", lambda: [("a", ["a"])])
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    assert clipboard.copy("hi there") == "osc52"
    out = capsys.readouterr().out
    assert out.startswith("\x1b]52;c;")
    assert out.endswith("\x07")
    assert base64.b64encode(b"hi there").decode() in out


def test_copy_falls_back_when_native_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(clipboard, "_native_candidates", lambda: [("a", ["a"])])
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/bin/a")

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, "a")

    monkeypatch.setattr(clipboard.subprocess, "run", boom)
    assert clipboard.copy("hi") == "osc52"
    assert "\x1b]52;c;" in capsys.readouterr().out


def test_copy_falls_back_on_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(clipboard, "_native_candidates", lambda: [("a", ["a"])])
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: "/bin/a")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("a", 5)

    monkeypatch.setattr(clipboard.subprocess, "run", slow)
    assert clipboard.copy("hi") == "osc52"
    assert "\x1b]52;c;" in capsys.readouterr().out
