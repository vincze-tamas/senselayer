from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

DATA_PATH = Path(os.environ.get("SENSELAYER_DATA_DIR", "data")) / "latest_sample.json"


class LiveFileSource:
    def __init__(self, path: Path = DATA_PATH, max_age_seconds: float = 15.0):
        self.path = Path(path)
        self.max_age_seconds = max_age_seconds
        self.last_good: Optional[dict] = None

    def next(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            sample = json.loads(self.path.read_text(encoding="utf-8"))
            received_at = float(sample["received_at"])
            if time.time() - received_at > self.max_age_seconds:
                return None
            sample.setdefault("timestamp", received_at)
            self.last_good = sample
            return sample
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
