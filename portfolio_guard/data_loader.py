from __future__ import annotations

import json
import os
import copy
import time
from pathlib import Path
from typing import Any

from .market_data import refresh_snapshot_with_public_data


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = PROJECT_ROOT / "sample_data" / "demo_snapshot.json"
_CACHE: dict[str, Any] = {"loaded_at": 0.0, "snapshot": None}
_CACHE_TTL_SECONDS = 300


def load_demo_snapshot(path: Path | None = None, refresh_market: bool | None = None) -> dict[str, Any]:
    data_path = path or SAMPLE_DATA_PATH
    snapshot = json.loads(data_path.read_text(encoding="utf-8"))
    if refresh_market is None:
        refresh_market = os.environ.get("PORTFOLIO_GUARD_LIVE_DATA", "1") != "0"
    if refresh_market:
        now = time.time()
        if _CACHE["snapshot"] is not None and now - float(_CACHE["loaded_at"]) < _CACHE_TTL_SECONDS:
            return copy.deepcopy(_CACHE["snapshot"])
        try:
            snapshot = refresh_snapshot_with_public_data(snapshot)
        except Exception as exc:
            snapshot["data_mode"] = {
                "portfolio": "demo/static portfolio input",
                "market": "embedded fallback snapshot",
                "market_symbols_loaded": [],
                "error": str(exc),
            }
        _CACHE["snapshot"] = copy.deepcopy(snapshot)
        _CACHE["loaded_at"] = now
    else:
        snapshot["data_mode"] = {
            "portfolio": "demo/static portfolio input",
            "market": "embedded fallback snapshot",
            "market_symbols_loaded": [],
        }
    return snapshot
