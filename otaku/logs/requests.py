"""The request log: every model-bound request body, one file per day.

Appends to logs/requests-YYYYMMDD.jsonl. The envelope — timestamp,
provider, purpose — is plaintext; the body is sealed with the session
cipher, or stored as readable inline JSON when encryption is off, so the
log protects exactly what the database protects. Always on; read back with
`otaku logs requests`. No pruning — the log is the audit trail of what the
models were actually sent.
"""

import base64
import json
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from otaku.crypto import Cipher, PlainCipher
from otaku.logs.daily import DailyLog
from otaku.paths import Paths


@dataclass(frozen=True)
class Entry:
    ts: str
    provider: str
    purpose: str
    body: dict[str, object] | None  # None when the body cannot be read back


class RequestLog(DailyLog):
    _prefix = "requests-"
    _suffix = ".jsonl"

    def __init__(self, paths: Paths, cipher: Cipher) -> None:
        super().__init__(paths)
        self._cipher = cipher
        self._lock = threading.Lock()  # appends come from worker and REPL threads

    def record(self, provider: str, purpose: str, body: dict[str, object]) -> None:
        """Append one request. Best-effort: a logging failure warns on
        stderr and never fails the request it describes."""
        now = datetime.now().astimezone()
        entry: dict[str, object] = {
            "ts": now.isoformat(timespec="seconds"),
            "provider": provider,
            "purpose": purpose,
        }
        if isinstance(self._cipher, PlainCipher):
            entry["body"] = body
        else:
            sealed = self._cipher.seal(json.dumps(body, ensure_ascii=False).encode("utf-8"))
            entry["body_sealed"] = base64.b64encode(sealed).decode()
        try:
            path = self.get_path(now.strftime("%Y%m%d"))
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            print(f"otaku: request log failed: {e}", file=sys.stderr)

    def read(self, day: str) -> Iterator[Entry]:
        """The day's entries, in order. A body the cipher cannot open (or a
        corrupt line) yields an Entry with body=None rather than failing
        the whole day."""
        for line in self.get_path(day).read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                yield Entry(ts="?", provider="?", purpose="?", body=None)
                continue
            yield Entry(
                ts=str(raw.get("ts", "?")),
                provider=str(raw.get("provider", "?")),
                purpose=str(raw.get("purpose", "?")),
                body=self.get_body(raw),
            )

    def get_body(self, raw: dict[str, object]) -> dict[str, object] | None:
        """One raw JSON line's body, opened with the session cipher — None
        when it cannot be read back (wrong key, corrupt line)."""
        body = raw.get("body")
        if isinstance(body, dict):
            return body
        sealed = raw.get("body_sealed")
        if not isinstance(sealed, str):
            return None
        try:
            plain = self._cipher.unseal(base64.b64decode(sealed))
            parsed = json.loads(plain.decode("utf-8"))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
