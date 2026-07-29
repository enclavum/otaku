"""A scripted OpenAI-compatible server the real app talks to.

Scenario tests need a real protocol peer, not a real model: the unmodified
application — registry, streaming, request log and all — connects to this
server over HTTP and gets deterministic, instant replies. What it answers
comes from `script`, a callable from the request body to the completion
text; `default_script` recognizes the lore prompts by their fixed openings
and answers with canned extraction JSON and rollups, so even the whole
extraction pipeline plays end to end offline. Every request body is kept
in `requests` for assertions — the wire promise is checked against it.
"""

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

CHAT_REPLY = "Погас свет, и в темноте что-то шевельнулось."
STORY_SO_FAR = "История: гость встретил Хранителя."
CHARACTER_HISTORY = "Хранитель помнит гостя."
EXTRACTION = {
    "scene": {"title": "Встреча", "summary": "Гость вошёл и встретил Хранителя."},
    "speakers": [],
    "characters": [{"name": "Хранитель", "aliases": [], "description": "страж врат"}],
    "journals": [{"character": "Хранитель", "entry": "Я видел гостя.", "state": "у врат"}],
}


def default_script(body: dict[str, Any]) -> str:
    """Answers by prompt kind: the extraction prompt gets valid JSON, the
    rollup prompts get one-line rollups, anything else gets the chat
    reply. Recognition is by each lore prompt's fixed opening words."""
    prompt = str(body.get("messages", [{}])[-1].get("content", ""))
    if "You are a story analyst" in prompt:
        return json.dumps(EXTRACTION, ensure_ascii=False)
    if prompt.startswith("Combine the scene summaries"):
        return STORY_SO_FAR
    if prompt.startswith("Write ") and "'s history" in prompt:
        return CHARACTER_HISTORY
    return CHAT_REPLY


class ModelServer:
    """The server, on a free localhost port from construction to `close`.
    `script` is swappable per test; `reset` restores the default and
    clears the recorded requests."""

    def __init__(self, models: tuple[str, ...] = ("test-model",)) -> None:
        self.models = list(models)
        self.requests: list[dict[str, Any]] = []
        self.script: Callable[[dict[str, Any]], str] = default_script
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                pass  # quiet — test output belongs to the tests

            def do_GET(self) -> None:
                if self.path.rstrip("/").endswith("/models"):
                    payload = {"data": [{"id": name} for name in outer.models]}
                    body = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(body)
                text = outer.script(body)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                # A few chunks, so the streaming path is exercised for real.
                third = max(1, len(text) // 3)
                for i in range(0, len(text), third):
                    event = {"choices": [{"delta": {"content": text[i : i + third]}}]}
                    self._event(event)
                self._event(
                    {
                        "choices": [{"delta": {}}],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 5},
                    }
                )
                self.wfile.write(b"data: [DONE]\n\n")

            def _event(self, payload: dict[str, Any]) -> None:
                self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/v1"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def reset(self) -> None:
        self.script = default_script
        self.requests.clear()

    def close(self) -> None:
        self._httpd.shutdown()
