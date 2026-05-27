from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional

DATA_PATH = Path("data/latest_sample.json")


class LiveFileSource:
    def __init__(self, path: Path = DATA_PATH):
        self.path = Path(path)
        self.last_good: Optional[Dict] = None

    def next(self) -> Optional[Dict]:
        if not self.path.exists():
            return self.last_good
        try:
            raw = self.path.read_text(encoding="utf-8")
            sample = json.loads(raw)
            sample.setdefault("timestamp", time.time())
            self.last_good = sample
            return sample
        except Exception:
            return self.last_good
