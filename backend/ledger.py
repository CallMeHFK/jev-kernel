"""The ledger: one JSONL record per verdict (mu's ``mu ledger``).

Lives in the durable plugin-data home so it survives plugin reinstalls.
Single-generation rotation like dispatch-guard's audit trail: the ledger
is evidence, not a forever-growing file.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from .config import plugin_data_dir

logger = logging.getLogger("jev_kernel.ledger")

LEDGER_MAX_BYTES = 1 << 20
_LOCK = threading.Lock()


def ledger_path() -> Path:
    return plugin_data_dir() / "ledger.jsonl"


def append(record: dict[str, Any]) -> None:
    try:
        path = ledger_path()
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LEDGER_MAX_BYTES:
                path.replace(path.with_suffix(".jsonl.1"))
            with path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps({"ts": time.time(), "plugin": "jev-kernel", **record},
                               ensure_ascii=False) + "\n"
                )
    except Exception:  # noqa: BLE001 - logging must never wedge the agent
        logger.exception("jev-kernel: ledger write failed")


def tail(limit: int = 20) -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out[-max(1, int(limit)):]
