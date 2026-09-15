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
_PORT_FLAG = re.compile(r"(?:^|\s)--port[= ](\d+)\b")


def read_home_json(relative: str) -> dict[str, Any]:
    """A JSON object at `~/<relative>`, or {} on any failure — how a local
    app's own config file is consulted at autoconfigure time."""
    try:
        parsed = json.loads((Path.home() / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def launched_port(executable: str, commands: Iterable[str] | None = None) -> int | None:
    """The port a running `executable` was launched on — its `--port`
    flag, else KoboldCpp's positional (`koboldcpp model.gguf 5001`, as
    its own docs spell it) — how an engine configured by launch flags
    is found where it is. The executable is known by its bare name,
    however the command spells it (see `_is_named`); a subcommand may
    follow it ("llama serve"). Where several run, the lowest port: a
    router's children are servers of the same name on ephemeral ports,
    and `ps` lists them in no particular order. None when no such
    process runs, none carries a port, or the processes cannot be read.
    `commands` are the command lines to read, the running processes' by
    default."""
    name, *subcommand = executable.lower().split()
    ports = []
    for command in _command_lines() if commands is None else commands:
        if _runs(command, name, subcommand):
            flag = _PORT_FLAG.search(command)
            positional = next(
                (int(word) for word in _before_flags(command) if word.isdigit()), None
            )
            port = int(flag.group(1)) if flag else positional
            if port is not None:
                ports.append(port)
    return min(ports, default=None)


def _runs(command: str, name: str, subcommand: list[str]) -> bool:
    """Whether `command` is `name` running, with `subcommand` right after
    it. Only the words before the first flag are read, so a path with
    spaces stays whole; a server bundled by another app (Ollama's, LM
    Studio's) is not the engine."""
    words = _before_flags(command)
    if any(mark in " ".join(words) for mark in _NOT_AN_ENGINE):
        return False
    for i, word in enumerate(words):
        following = words[i + 1 : i + 1 + len(subcommand)]  # as many words as the subcommand has
        if _is_named(word, name) and following == subcommand:
            return True
    return False


def _before_flags(command: str) -> list[str]:
    """The command line's words before its first flag: the executable,
    a subcommand, a positional argument; a path with spaces stays whole
    among them."""
    return re.split(r"\s+-", command, maxsplit=1)[0].split()


def _is_named(word: str, name: str) -> bool:
    """Whether a command-line word is the executable `name`: its bare
    name — the last path segment, unquoted, lowercase, without `.exe`
    or `.py` — is `name`, or `name` with a build suffix
    (`koboldcpp-mac-arm64`)."""
    bare = re.split(r"[\\/]", word.strip('"'))[-1].lower()
    bare = bare.removesuffix(".exe").removesuffix(".py")
    return bare == name or bare.startswith((f"{name}-", f"{name}_"))


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
