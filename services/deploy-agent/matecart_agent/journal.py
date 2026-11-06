"""Durable local state: executed commands and the outbox of unsent reports.

- A command id is executed at most once: the journal records it before execution starts.
- Reports made while Control is unreachable wait in a bounded outbox. Command events are never
  dropped; observations are (oldest first) when the bound is reached, because only the newest
  observation can become authoritative anyway (D-115).
"""

import json
from pathlib import Path
from typing import Any

from .config import write_private

MAX_OUTBOX = 500


class Journal:
    def __init__(self, state_dir: Path) -> None:
        self.path = state_dir / "journal.json"
        self.data: dict[str, Any] = {"commands": {}, "outbox": [], "stores": {}, "backups": {}}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self.data.update(loaded)

    def save(self) -> None:
        write_private(self.path, json.dumps(self.data, indent=1, sort_keys=True, default=str))

    # commands
    def command(self, command_id: str) -> dict[str, Any] | None:
        entry = self.data["commands"].get(command_id)
        return entry if isinstance(entry, dict) else None

    def start(self, command_id: str, kind: str, handle: str, sequence: int) -> None:
        self.data["commands"][command_id] = {
            "state": "started",
            "kind": kind,
            "handle": handle,
            "sequence": sequence,
            "stage": "started",
        }
        self.save()

    def stage(self, command_id: str, stage: str, sequence: int) -> None:
        entry = self.data["commands"][command_id]
        entry["stage"] = stage
        entry["sequence"] = sequence
        self.save()

    def finish(self, command_id: str, result: dict[str, Any], sequence: int) -> None:
        entry = self.data["commands"][command_id]
        entry.update({"state": "finished", "result": result, "sequence": sequence})
        commands = self.data["commands"]
        if len(commands) > 200:  # keep the most recent command records
            for key in list(commands)[: len(commands) - 200]:
                if commands[key].get("state") == "finished":
                    commands.pop(key)
        self.save()

    def unfinished(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (key, value)
            for key, value in self.data["commands"].items()
            if isinstance(value, dict) and value.get("state") == "started"
        ]

    # outbox
    def enqueue(self, kind: str, path: str, body: dict[str, Any]) -> None:
        outbox: list[dict[str, Any]] = self.data["outbox"]
        outbox.append({"kind": kind, "path": path, "body": body})
        while len(outbox) > MAX_OUTBOX:
            index = next((i for i, item in enumerate(outbox) if item["kind"] == "observation"), 0)
            outbox.pop(index)
        self.save()

    def outbox(self) -> list[dict[str, Any]]:
        return list(self.data["outbox"])

    def drop_first(self) -> None:
        self.data["outbox"].pop(0)
        self.save()

    # per-store state (applied configuration revision, last backup)
    def store_state(self, handle: str) -> dict[str, Any]:
        stores: dict[str, dict[str, Any]] = self.data["stores"]
        state: dict[str, Any] = stores.setdefault(handle, {})
        return state

    def set_store_state(self, handle: str, **values: Any) -> None:
        self.store_state(handle).update(values)
        self.save()
