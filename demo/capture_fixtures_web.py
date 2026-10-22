#!/usr/bin/env python
"""Regenerate demo/web/fixtures/ from the real server over the shipped
sample stories.

The app is opened over a THROWAWAY state dir this script creates and
deletes — never anyone's real ~/.otaku — so a regeneration can never
capture personal library content into a committed file. The scenario
suite's scripted server stands in for an engine, so the app has a real
context window (32K — the demo's own model claims the same in
`demo/web/store.js`), and the context previews are the real assembler's
work over both samples: the river verbatim, the tour as the
head-recap-tail ladder. Every fixture is an answer the real server gave
over HTTP — the body as sent and, inside a story, the head its `ETag`
named — so the demo seeds from exactly what the page reads. The harness
provider's names and the throwaway path are scrubbed before writing.

Run from the repo root:  conda run -n otaku python demo/capture_fixtures_web.py
"""

import json
import re
import shutil
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from otaku.backend import launch as backend_launch
from otaku.backend.api import Api, ModelRef
from otaku.web import run_beside
from scenarios.support.harness import set_config, set_config_provider
from scenarios.support.server import ModelServer

FIXTURES = Path(__file__).resolve().parent / "web" / "fixtures"

# The window the previews are captured under. `demo/web/store.js` claims the
# same for its model, so the demo's numbers and the captured ones agree.
WINDOW = 32768
# The engine and the model the throwaway app plays on — scaffolding, not
# content: the demo names its own (`demo/web/store.js`).
ENGINE, MODEL = "ollama", "test-model"

# Every timestamp in a payload is pinned to this instant, so a rerun over
# an unchanged tree writes the same bytes: the samples are seeded at launch,
# and their clock would otherwise move with every capture.
PINNED_AT = "2026-01-01T00:00:00+00:00"
# A picture's file name carries the day it was stored (`store.files`);
# pinned to PINNED_AT's day in the payloads and on the copied files alike,
# so the fixture's names never move with the capture's date.
_PICTURE_NAME = re.compile(r"((?:pic|crd)-\d{4}-)\d{8}(-[0-9a-f]+(?:-thumb)?\.[a-z0-9]+)")
_PINNED_DAY = PINNED_AT[:10].replace("-", "")


def main() -> None:
    server = ModelServer(managed=True)
    server.contexts[MODEL] = WINDOW
    # A model's window is read off /api/ps, the LOADED models: unloaded, the
    # app budgets on the assembler's default and the tour does not fit. So
    # the harness holds it loaded.
    server.loaded.add(MODEL)
    try:
        with tempfile.TemporaryDirectory(prefix="otaku-demo-fixtures-") as root_str:
            root = Path(root_str)
            set_config(root, seed_sample=True)
            set_config_provider(root, server, name=ENGINE)
            with _served(root) as url:
                fixtures = _capture(url)
            _write(fixtures, root_str, server.url)
            _copy_pictures(fixtures, root)
    finally:
        server.close()


def _capture(url: str) -> dict[str, Any]:
    """Every read the page makes, answered by the real server: the app's
    own, and each sample story's — by the paths the page asks."""

    def read(path: str) -> Any:
        return _get(url + path)[1]

    stories = read("/api/stories")["items"]
    by_title = {row["title"]: row["id"] for row in stories}
    river = by_title["The River That Forgot Its Name"]
    tour = by_title["The Vermilion Tour"]
    tools = [row for row in read(f"/api/stories/{river}/settings")["items"] if row["tool"]]
    prompted = [
        row for row in read(f"/api/stories/{river}/settings")["items"]
        if row["prompt_name"] and not row["tool"]
    ]
    # What a story made now starts with — its settings, every switch off —
    # read off one made for it and deleted again, after the reads above.
    request = urllib.request.Request(
        url + "/api/stories", data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=30) as reply:
        fresh = json.loads(reply.read())["value"]["id"]
    defaults = read(f"/api/stories/{fresh}/settings")["items"]
    request = urllib.request.Request(f"{url}/api/stories/{fresh}", method="DELETE")
    with urllib.request.urlopen(request, timeout=30) as reply:
        reply.read()
    return {
        "app": {
            "story_defaults": defaults,
            "app": read("/api/app"),
            "settings": read("/api/settings"),
            "language": read("/api/language"),
            "history": read("/api/history"),
            "web_search": read("/api/integrations/web-search"),
            "shared_reminder": read("/api/shared-reminder"),
            "providers": read("/api/providers?scope=local"),
            "provider": read(f"/api/providers/{ENGINE}"),
            "models": read(f"/api/providers/{ENGINE}/models"),
            "model_settings": read(f"/api/providers/{ENGINE}/models/{MODEL}/settings"),
            "info": read("/api/info"),
            "balance": read("/api/balance?probe=false"),
            "stories": stories,
            # Every tool's prompt and every prompt no tool owns, as shipped.
            "tool_prompts": {row["tool"]: read(f"/api/tools/{row['tool']}/prompt") for row in tools if row["prompt_name"]},
            "prompts": {row["prompt_name"]: read(f"/api/prompts/{row['prompt_name']}") for row in prompted},
        },
        "river": _story(url, river),
        "tour": _story(url, tour),
        "codex": _codex(url),
    }


def _codex(url: str) -> dict[str, Any]:
    """All characters as the page reads it — the cards as listed, each one
    whole, and the stories' characters no card claims."""
    cards = _get(f"{url}/api/cards")[1]["items"]
    return {
        "cards": cards,
        "card": {str(row["id"]): _get(f"{url}/api/cards/{row['id']}")[1] for row in cards},
        "unlinked": _get(f"{url}/api/cards/unlinked")[1]["items"],
    }


def _story(url: str, story: int) -> dict[str, Any]:
    """One story as the page reads it — its facts with the head its `ETag`
    names, its messages, its settings, its lore and its context."""
    tag, facts = _get(f"{url}/api/stories/{story}")
    inside = f"{url}/api/stories/{story}"
    return {
        "etag": tag,
        "story": facts,
        "messages": _get(f"{inside}/messages")[1]["items"],
        "settings": _get(f"{inside}/settings")[1]["items"],
        "lore": _get(f"{inside}/lore")[1],
        "context": _get(f"{inside}/context")[1],
        "fill": _get(f"{inside}/context/fill")[1],
        # Refused while no pass was asked for: the answer is the problem
        # body under its status, which the demo gives back the same way.
        "extraction": _answer(f"{inside}/extraction"),
    }


def _get(url: str) -> tuple[str | None, Any]:
    with urllib.request.urlopen(url, timeout=30) as reply:
        return reply.headers.get("ETag"), json.loads(reply.read())


def _answer(url: str) -> dict[str, Any]:
    """A read that may be refused: its status and its body either way."""
    try:
        with urllib.request.urlopen(url, timeout=30) as reply:
            return {"status": reply.status, "body": json.loads(reply.read())}
    except urllib.error.HTTPError as refused:
        return {"status": refused.code, "body": json.loads(refused.read())}


def _write(fixtures: dict[str, Any], root_str: str, engine_url: str) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for stale in FIXTURES.glob("*.json"):
        stale.unlink()  # the fixtures are this capture's alone
    for name, payload in fixtures.items():
        text = json.dumps(_pin_timestamps(payload), ensure_ascii=False, indent=1)
        # The throwaway root must not reach a committed file — nor would any
        # real path belong in a payload the page seeds from.
        text = text.replace(root_str, "~/.otaku").replace(str(Path.home()), "~")
        # The scripted engine's port is the OS's pick: the engine's own
        # default stands in, so a rerun writes the same bytes.
        text = text.replace(engine_url, "http://localhost:11434/v1")
        text = _PICTURE_NAME.sub(rf"\g<1>{_PINNED_DAY}\g<2>", text)
        path = FIXTURES / f"{name}.json"
        path.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(text):,} chars)")


def _copy_pictures(fixtures: dict[str, Any], root: Path) -> None:
    """The samples' pictures as the store holds them — the river's turns'
    and the cards' portraits, each file and its thumbnail, their day
    pinned — for the demo's `/api/pictures`. The folder is rebuilt whole,
    so a picture the samples dropped does not linger."""
    files = FIXTURES / "files"
    shutil.rmtree(files, ignore_errors=True)
    files.mkdir()
    held = root / "database" / "files"
    pictures = [p for message in fixtures["river"]["messages"] for p in message.get("attachments") or []]
    pictures += [p for card in fixtures["codex"]["card"].values() for p in card["pictures"]]
    for picture in pictures:
        stem = Path(picture["file"]).stem
        for stored in (picture["file"], f"{stem}-thumb.jpg"):
            shutil.copyfile(held / stored, files / _PICTURE_NAME.sub(rf"\g<1>{_PINNED_DAY}\g<2>", stored))
    print(f"wrote {files} ({len(list(files.iterdir()))} files)")


def _pin_timestamps(payload: Any) -> Any:
    """`payload` with every `*_at` string set to PINNED_AT, recursively."""
    if isinstance(payload, dict):
        return {
            k: (PINNED_AT if k.endswith("_at") and isinstance(v, str) else _pin_timestamps(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [_pin_timestamps(v) for v in payload]
    return payload


class _served:
    """`otaku serve` over `root` on a free port, served beside the capture
    (`web.run_beside`) and stopped on exit."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._app: Any = None
        self._server: Any = None

    def __enter__(self) -> str:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        set_config(self._root, web_host="127.0.0.1", web_port=port)
        self._app = backend_launch.open_app(self._root)
        Api(self._app).app.update(model=ModelRef(ENGINE, MODEL))
        self._app.start_worker()
        self._server = run_beside(self._app)
        url = f"http://127.0.0.1:{port}"
        for _ in range(150):
            try:
                with urllib.request.urlopen(url + "/api/app/status", timeout=1) as reply:
                    reply.read()
                return url
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise SystemExit("the server never answered")

    def __exit__(self, *exc: object) -> None:
        if self._server is not None:
            self._server.stop()
        if self._app is not None:
            self._app.close()


if __name__ == "__main__":
    main()
