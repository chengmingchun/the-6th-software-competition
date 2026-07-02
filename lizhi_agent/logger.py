from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any


class DecisionLogger:
    """Small stderr/file logger that never breaks the game loop."""

    def __init__(self, player_id: str, log_dir: str = "logs") -> None:
        self.player_id = player_id
        self.log_dir = log_dir
        self.enabled = os.environ.get("LIZHI_DEBUG", "1") != "0"
        self.file_enabled = os.environ.get("LIZHI_FILE_LOG", "0") == "1"
        self._file = None
        if self.file_enabled:
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(log_dir, f"{player_id}.jsonl")
            self._file = open(path, "a", encoding="utf-8")

    def info(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "playerId": self.player_id,
            "event": event,
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        try:
            print(line, file=sys.stderr, flush=True)
            if self._file is not None:
                self._file.write(line + "\n")
                self._file.flush()
        except Exception:
            # Debug output must never crash the client.
            pass

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
