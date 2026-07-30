"""Running the real binary non-interactively: one-shot commands, captured.
The pty driver (`terminal.py`) owns the interactive journeys; this covers
the subcommands that print and exit."""

import os
import subprocess
import sys
from pathlib import Path


def run_otaku(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`otaku ARGS` over the state dir at `root`, captured."""
    return subprocess.run(
        [sys.executable, "-c", "from otaku.cli import main; main()", *args],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "OTAKU_CONFIG_DIR": str(root)},
        check=False,
    )
