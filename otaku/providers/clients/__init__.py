"""One module per engine; the registry's `ALL_CLIENTS` maps their ids to
the classes in the panel's canonical order. Beside them, what a local
engine's `autoconfigure` consults about this machine: an app's own
config file, and the command lines of the engines running now."""

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
    """The `--port` a running `executable` was launched with — how an
    engine configured by launch flags is found where it is. The
    executable is known by its bare name, however the command spells
    it: a path with either separator, quoted or not, spaces and all;
    Windows' `.exe`; a release build's suffix (`koboldcpp-mac-arm64`);
    a script run by python (`python koboldcpp.py`); a positional model
    beside it. None when no such process runs, it carries no flag, or
    the processes cannot be read; a server that is Ollama's or LM
    Studio's own is passed over. `commands` are the command lines to
    read, the running processes' by default."""
    for command in _command_lines() if commands is None else commands:
        # The program part — everything before the first flag — so a
        # path with spaces in it stays whole and its last word can end
        # in the executable's name.
        program = re.split(r"\s+-", command, maxsplit=1)[0]
        if any(mark in program for mark in _NOT_AN_ENGINE):
            continue
        if not any(_is_named(word, executable) for word in program.split()):
            continue
        flag = re.search(r"(?:^|\s)--port[= ](\d+)\b", command)
        if flag:
            return int(flag.group(1))
    return None


def _is_named(word: str, executable: str) -> bool:
    """Whether a command-line word is the executable: its bare name with
    either separator, quotes, `.exe` or `.py` stripped, and a build
    suffix after a dash or an underscore allowed."""
    name = re.split(r"[\\/]", word.strip('"'))[-1].lower()
    for extension in (".exe", ".py"):
        name = name.removesuffix(extension)
    wanted = executable.lower()
    return name == wanted or name.startswith((wanted + "-", wanted + "_"))


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
