"""One module per engine; `CLIENTS` in the registry maps section names
to these classes in the panel's canonical order. Beside them, what an
engine's `autoconfigure` consults about this machine: a local app's
own config file, and the command lines of the engines running now."""

import functools
import json
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# The bundles whose own llama-server children listen on ports of their
# own: theirs, never an engine to configure.
_NOT_AN_ENGINE = ("Ollama.app", "LM Studio.app", ".lmstudio")


def read_home_json(relative: str) -> dict[str, Any]:
    """A JSON object at `~/<relative>`, or {} on any failure — how a local
    app's own config file is consulted at autoconfigure time."""
    try:
        parsed = json.loads((Path.home() / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def launched_port(executable: str, commands: Iterable[str] | None = None) -> int | None:
    """The `--port` a running `executable` (its bare name) was launched
    with — how an engine configured by launch flags is found where it
    is. None when no such process runs, it carries no flag, or the
    processes cannot be read; a server that is Ollama's or LM Studio's
    own is passed over. `commands` are the command lines to read, the
    running processes' by default."""
    for command in _command_lines() if commands is None else commands:
        head = command.split(maxsplit=1)[0].strip('"')
        # The bare name however the path is spelled — either separator,
        # with or without Windows' extension.
        name = re.split(r"[\\/]", head)[-1]
        name = name[: -len(".exe")] if name.lower().endswith(".exe") else name
        if name != executable or any(mark in head for mark in _NOT_AN_ENGINE):
            continue
        flag = re.search(r"(?:^|\s)--port[= ](\d+)\b", command)
        if flag:
            return int(flag.group(1))
    return None


@functools.cache
def _command_lines() -> tuple[str, ...]:
    """Every running process's command line, read once per launch —
    `ps` where there is one, PowerShell on Windows; () when it cannot
    be read, so a launch never waits on it."""
    if sys.platform == "win32":
        script = "Get-CimInstance Win32_Process | ForEach-Object { $_.CommandLine }"
        argv = ["powershell", "-NoProfile", "-Command", script]
    else:
        argv = ["ps", "-axww", "-o", "args="]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ()
    return tuple(line.strip() for line in out.stdout.splitlines() if line.strip())
