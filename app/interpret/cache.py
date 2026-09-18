"""Small thread-safe LRU cache of accepted intents, keyed by normalized note text.

Intents do not depend on battery capacity (unit conversion happens later), so the
note text alone is a safe key.
"""
import hashlib
import threading
from collections import OrderedDict
from typing import Optional


def note_key(text: str) -> str:
    norm = " ".join(text.lower().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


class IntentCache:
    def __init__(self, size: int = 512):
        self.size = max(0, size)
        self._data: "OrderedDict[str, dict]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, text: str) -> Optional[dict]:
        if self.size == 0:
            return None
        k = note_key(text)
        with self._lock:
            if k in self._data:
                self._data.move_to_end(k)
                return dict(self._data[k])
        return None

    def put(self, text: str, intent: dict) -> None:
        if self.size == 0:
            return
        k = note_key(text)
        with self._lock:
            self._data[k] = dict(intent)
            self._data.move_to_end(k)
            while len(self._data) > self.size:
                self._data.popitem(last=False)
