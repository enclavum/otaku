#!/usr/bin/env python
"""Regenerate demo/web/fixtures/ from a real session over the shipped
sample stories.

The session is opened over a THROWAWAY state dir this script creates and
deletes — never anyone's real ~/.otaku — so a regeneration can never
capture personal library content into a committed file. The scenario
suite's scripted server stands in for an engine so the session has a
real context window (32K — the demo's own model claims the same in
`demo/web/store.js`), and the context previews are the real assembler's
work over both samples: the river verbatim, the tour as the
head-recap-tail ladder. The captured payloads are exactly what
`otaku/web/api.py` serves; the harness provider's name and the throwaway
path are scrubbed before writing.

Run from the repo root:  conda run -n otaku python demo/capture_fixtures_web.py
"""

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from otaku.backend.api import settings as api_settings
from otaku.backend.api import stories as api_stories
from otaku.backend.tools import TOOLS
from otaku.web import api as web_api
from scenarios.support.harness import launch, set_config, set_config_provider
from scenarios.support.server import ModelServer

FIXTURES = Path(__file__).resolve().parent / "web" / "fixtures"

# The window the previews are captured under. `demo/web/store.js` claims the
# same for its model, so the demo's numbers and the captured ledes agree.
WINDOW = 32768


# Every timestamp in a payload is pinned to this instant, so a rerun over
# an unchanged tree writes the same bytes: the samples are seeded at launch,
# and their clock would otherwise move with every capture.
PINNED_AT = "2026-01-01T00:00:00+00:00"
# A picture's file name carries the day it was stored (`store.files`);
# pinned to PINNED_AT's day in the payloads and on the copied files alike,
# so the fixture's names never move with the capture's date.
_PICTURE_NAME = re.compile(r"(pic-\d{4}-)\d{8}(-[0-9a-f]+(?:-thumb)?\.[a-z0-9]+)")
_PINNED_DAY = PINNED_AT[:10].replace("-", "")


def pin_picture_names(text: str) -> str:
    """`text` with every picture file name's day set to PINNED_AT's."""
    return _PICTURE_NAME.sub(rf"\g<1>{_PINNED_DAY}\g<2>", text)


def pin_timestamps(payload):
    """`payload` with every `*_at` string set to PINNED_AT, recursively."""
    if isinstance(payload, dict):
        return {
            k: (PINNED_AT if k.endswith("_at") and isinstance(v, str) else pin_timestamps(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [pin_timestamps(v) for v in payload]
    return payload


def main() -> None:
    server = ModelServer(managed=True)
    server.contexts["test-model"] = WINDOW
    # A model's window is read off /api/ps, the LOADED models (the Ollama
    # client since 0.4.1): unloaded, the session budgets on the assembler's
    # default and the tour does not fit. So the harness holds it loaded.
    server.loaded.add("test-model")
    try:
        with tempfile.TemporaryDirectory(prefix="otaku-demo-fixtures-") as root_str:
            root = Path(root_str)
            set_config(root, seed_sample=True)
            set_config_provider(root, server, name="ollama")
            app = launch(root, server, spec="ollama/test-model")
            try:
                session = app.session
                rows = web_api.stories.stories(session)
                river_id = next(r["id"] for r in rows if r["open"])
                tour_id = next(r["id"] for r in rows if not r["open"])

                # The tour's dossier and its REAL context preview —
                # captured while it is open, so the assembler's
                # head-recap-tail ladder over the long story is the
                # demo's own context screen.
                tour_messages = api_stories.messages_of(session, tour_id)
                api_stories.land(session, tour_id, tour_messages[-1].id, "resume")
                tour_opened = web_api.story(session, tour_id)
                tour_context = web_api.context(session)

                # Back to the river: the landing story, open in the demo
                # the way a fresh install opens it.
                river_messages = api_stories.messages_of(session, river_id)
                api_stories.land(session, river_id, river_messages[-1].id, "resume")

                facts = web_api.facts(session)
                # The harness provider is scaffolding, not content; the
                # demo names its own model (`demo/web/store.js`).
                facts.update(model="", provider="", max_context="")
                settings = web_api.settings.settings(session)
                settings["model"] = ""
                rows = web_api.stories.stories(session)  # after both landings: river open
                for row in rows:
                    row["model"] = ""

                fixtures = {
                    "syntax": web_api.syntax(),
                    "settings": settings,
                    # What every story's settings read as until it changes
                    # one: the defaults, which the demo copies per story.
                    "story_settings": web_api.story_settings(session, river_id),
                    # Every tool's prompt as shipped, by name.
                    "prompts": {
                        name: api_settings.get_tool_prompt(session, name) for name in TOOLS
                    },
                    "river": {
                        "facts": facts,
                        "story": next(r for r in rows if r["id"] == river_id),
                        # The story WHOLE — the one read the dossier
                        # makes, so the demo seeds from the shape the
                        # page reads.
                        "opened": web_api.story(session, river_id),
                        "context": web_api.context(session),
                    },
                    "tour": {
                        "story": next(r for r in rows if r["id"] == tour_id),
                        "opened": tour_opened,
                        "context": tour_context,
                    },
                }
            finally:
                app.close()

            FIXTURES.mkdir(parents=True, exist_ok=True)
            for name, payload in fixtures.items():
                text = json.dumps(pin_timestamps(payload), ensure_ascii=False, indent=1)
                # The throwaway root must not reach a committed file — nor
                # would any real path belong in a payload the page seeds from.
                scrubbed = text.replace(root_str, "~/.otaku").replace(str(Path.home()), "~")
                scrubbed = pin_picture_names(scrubbed)
                path = FIXTURES / f"{name}.json"
                path.write_text(scrubbed + "\n", encoding="utf-8")
                print(f"wrote {path} ({len(scrubbed):,} chars)")
            # The sample's pictures, as the store holds them — the file and
            # its thumbnail, their day pinned — for the demo's `/api/files`.
            # The folder is rebuilt whole, so a picture the sample dropped
            # does not linger.
            files = FIXTURES / "files"
            shutil.rmtree(files, ignore_errors=True)
            files.mkdir()
            store_files = root / "database" / "files"
            for turn in fixtures["river"]["opened"]["messages"]:
                for picture in turn.get("attachments") or []:
                    stem = Path(picture["file"]).stem
                    for stored in (picture["file"], f"{stem}-thumb.jpg"):
                        shutil.copyfile(store_files / stored, files / pin_picture_names(stored))
            print(f"wrote {files} ({len(list(files.iterdir()))} files)")
    finally:
        server.close()


if __name__ == "__main__":
    main()
