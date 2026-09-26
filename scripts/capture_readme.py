#!/usr/bin/env python3
"""The README's screenshot, taken the same way every release.

`images/capture-web.png` is the web demo on a first launch: the river
story open at its classic ending (its first `SHOWN_TURNS`), the attach
button showing, the light theme, a 1400 by 865 viewport at twice the
scale. The demo is the one deterministic source — built from this
checkout alone (`demo/build_web.sh` over the committed fixtures), served
here, and shot by headless Chrome — so a capture made today matches one
made at the last release, differing only where the page did.

    python scripts/capture_readme.py            # writes images/capture-web.png
    python scripts/capture_readme.py --keep     # also leaves the built demo behind, says where

Writes the picture, then bumps the `?v=` on the README's image link to
the tree's version, so GitHub's cache serves the new file. Needs Chrome
(or Chromium) installed; nothing else beyond the demo build's own needs.
Run it with the version final, after the fixtures were regenerated.
"""

import argparse
import http.server
import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "images" / "capture-web.png"
README = REPO / "README.md"
WIDTH, HEIGHT, SCALE = 1400, 865, 2
SHOWN_TURNS = 14  # the river up to its classic ending, before the pictured turn and the question
SETTLE_MS = 8_000  # how long the page's scripts may run before the shot
SHOT_TIMEOUT = 60.0  # how long to wait for the picture, in seconds
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", action="store_true", help="leave the built demo behind")
    args = parser.parse_args()
    chrome = _chrome()
    if chrome is None:
        print("capture: no Chrome or Chromium found", file=sys.stderr)
        return 1
    work = Path(tempfile.mkdtemp(prefix="otaku-capture-"))
    try:
        target = work / "demo-web"
        subprocess.run(["bash", str(REPO / "demo" / "build_web.sh"), str(target)], check=True)
        _dress(target)
        with _Served(target) as port:
            shot = work / "capture.png"
            _shoot(chrome, work / "profile", f"http://127.0.0.1:{port}/index.html", shot)
            shutil.copyfile(shot, OUT)
        version = _version()
        text = README.read_text(encoding="utf-8")
        bumped = re.sub(r"(images/capture-web\.png\?v=)[^)\s]+", rf"\g<1>{version}", text)
        if bumped != text:
            README.write_text(bumped, encoding="utf-8")
        print(f"wrote {OUT.relative_to(REPO)} ({OUT.stat().st_size:,} bytes), README ?v={version}")
        if args.keep:
            print(f"demo left at {target}")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


def _dress(target: Path) -> None:
    """The built copy made the README's, in this copy alone: the demo's
    corner ribbon hidden (`demo/web/demo.js` adds it); the attach button
    shown — the demo's model cannot see (`store.js`), the README's may;
    and the river cut to its first `SHOWN_TURNS`, the ending the picture
    has always shown, before the pictured turn and the question."""
    with (target / "custom.css").open("a", encoding="utf-8") as css:
        css.write("\n.demo-ribbon { display: none; }\n")
    store = target / "store.js"
    text = store.read_text(encoding="utf-8")
    if "vision: false" not in text:
        raise SystemExit("capture: store.js no longer says `vision: false` — update this script")
    store.write_text(text.replace("vision: false", "vision: true"), encoding="utf-8")
    fixture = target / "fixtures" / "river.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    opened = data["opened"]
    opened["messages"] = opened["messages"][:SHOWN_TURNS]
    last = opened["messages"][-1]["id"]
    for key in ("read_through",):
        if isinstance(opened.get(key), int):
            opened[key] = min(opened[key], last)
    data["story"]["turns"] = len(opened["messages"])
    fixture.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _shoot(chrome: str, profile: Path, url: str, out: Path) -> None:
    """Chrome headless writes the screenshot and then, being the Google
    Chrome app, keeps its updater and crash handler alive instead of
    exiting — so the picture is waited for, not the process: the file
    must appear and hold its size, and Chrome is stopped after."""
    process = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--no-first-run",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--user-data-dir={profile}",
            f"--window-size={WIDTH},{HEIGHT}",
            f"--force-device-scale-factor={SCALE}",
            f"--timeout={SETTLE_MS}",
            f"--screenshot={out}",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + SHOT_TIMEOUT
        size = -1
        while time.monotonic() < deadline:
            time.sleep(0.5)
            now = out.stat().st_size if out.exists() else -1
            if now > 0 and now == size:
                return
            size = now
        raise SystemExit(f"capture: no screenshot within {SHOT_TIMEOUT:.0f} s")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def _chrome() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _version() -> str:
    text = (REPO / "otaku" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    if match is None:
        raise SystemExit("capture: no __version__ in otaku/__init__.py")
    return match.group(1)


class _Served:
    """The built demo on a free localhost port for the duration of a `with`."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def __enter__(self) -> int:
        handler = type(
            "Quiet",
            (http.server.SimpleHTTPRequestHandler,),
            {
                "directory": str(self._directory),
                "log_message": lambda self, *args: None,
            },
        )
        # A directory-bound handler: Python's takes it as a keyword.
        directory = str(self._directory)
        self._server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), lambda *args, **kw: handler(*args, directory=directory, **kw)
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        port: int = self._server.server_address[1]
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            pass
        return port

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


if __name__ == "__main__":
    sys.exit(main())
