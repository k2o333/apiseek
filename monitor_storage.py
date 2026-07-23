"""Shared storage helpers for New-API collector (and future Sub2API migration).

Tail-event dedup (not full-file hash scan). Atomic JSON write. Instance flock.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

LOG = logging.getLogger("monitor-storage")

BACKEND_NEWAPI = "newapi"
SCHEMA_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_bytes_atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            raise
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: Any, mode: int = 0o644) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    write_bytes_atomic(path, payload.encode("utf-8"), mode=mode)


def append_jsonl_fsync(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def parse_ratio(value: Any) -> float:
    """Validate non-negative finite ratio. Raises ValueError on bad input."""
    if isinstance(value, bool):
        raise ValueError("ratio must not be bool")
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty ratio string")
        number = float(text)
    else:
        raise ValueError(f"unsupported ratio type {type(value).__name__}")
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"ratio out of range: {number!r}")
    return number


def normalize_groups_dict(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """New-API dict data -> sorted list of normalized groups. Raises ValueError."""
    if not isinstance(data, Mapping) or not data:
        raise ValueError("groups data must be a non-empty object")
    groups: list[dict[str, Any]] = []
    normalized_names: set[str] = set()
    for name, info in data.items():
        key = str(name).strip()
        if not key:
            raise ValueError("group name must be non-empty after trimming")
        if key in normalized_names:
            raise ValueError(f"duplicate normalized group name {key!r}")
        normalized_names.add(key)
        if not isinstance(info, Mapping):
            raise ValueError(f"group {key!r} value must be object")
        ratio = parse_ratio(info.get("ratio"))
        desc = info.get("desc")
        if desc is None:
            description = ""
        else:
            description = str(desc)
        groups.append(
            {
                "id": key,
                "name": key,
                "rate_multiplier": ratio,
                "description": description,
            }
        )
    groups.sort(key=lambda g: g["id"])
    return groups


def group_content_json(group: Mapping[str, Any]) -> str:
    payload = {
        "id": group.get("id"),
        "name": group.get("name"),
        "rate_multiplier": group.get("rate_multiplier"),
        "description": group.get("description", ""),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash_groups(groups: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "id": g["id"],
            "name": g["name"],
            "rate_multiplier": g["rate_multiplier"],
            "description": g.get("description", ""),
        }
        for g in groups
    ]
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def diff_groups(
    old_groups: list[dict[str, Any]] | None,
    new_groups: list[dict[str, Any]],
) -> dict[str, list[Any]]:
    old_map = {str(g["id"]): g for g in (old_groups or [])}
    new_map = {str(g["id"]): g for g in new_groups}
    added_ids = sorted(new_map.keys() - old_map.keys())
    removed_ids = sorted(old_map.keys() - new_map.keys())
    modified: list[dict[str, Any]] = []
    for gid in sorted(old_map.keys() & new_map.keys()):
        if group_content_json(old_map[gid]) != group_content_json(new_map[gid]):
            modified.append(
                {
                    "id": gid,
                    "before": {
                        "rate_multiplier": old_map[gid].get("rate_multiplier"),
                        "description": old_map[gid].get("description", ""),
                    },
                    "after": {
                        "rate_multiplier": new_map[gid].get("rate_multiplier"),
                        "description": new_map[gid].get("description", ""),
                    },
                }
            )

    def full_list(ids: list[str], source: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in ids:
            g = source[i]
            out.append(
                {
                    "id": g["id"],
                    "name": g["name"],
                    "rate_multiplier": g["rate_multiplier"],
                    "description": g.get("description", ""),
                }
            )
        return out

    return {
        "added": full_list(added_ids, new_map),
        "removed": full_list(removed_ids, old_map),
        "modified": modified,
    }


def load_latest(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        LOG.warning("Ignoring unreadable latest snapshot at %s", path)
    return None


def _truncate_half_line(path: Path) -> None:
    """If file ends mid-line, truncate to last newline (or empty)."""
    if not path.exists():
        return
    try:
        raw = path.read_bytes()
    except OSError:
        return
    if not raw:
        return
    if raw.endswith(b"\n"):
        return
    idx = raw.rfind(b"\n")
    if idx < 0:
        # entire file is one incomplete line
        write_bytes_atomic(path, b"", mode=0o644)
        return
    write_bytes_atomic(path, raw[: idx + 1], mode=0o644)


def last_complete_event(path: Path) -> dict[str, Any] | None:
    """Return last complete JSON object line; repair half-line tail first."""
    if not path.exists():
        return None
    _truncate_half_line(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    last: dict[str, Any] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            last = rec
    return last


@dataclass
class PersistResult:
    changed: bool
    content_hash: str
    count: int
    added: int
    removed: int
    modified: int


class SnapshotStore:
    """New-API snapshot writer with tail-event dedup."""

    def __init__(self, data_dir: Path, site_id: str, backend: str = BACKEND_NEWAPI) -> None:
        self.data_dir = data_dir
        self.site_id = site_id
        self.backend = backend
        self.latest_path = data_dir / "groups_latest.json"
        self.events_path = data_dir / "groups_events.jsonl"

    def persist_success(self, groups: list[dict[str, Any]]) -> PersistResult:
        digest = content_hash_groups(groups)
        fetched_at = utc_now_iso()
        previous = load_latest(self.latest_path)

        if previous is not None:
            prev_site = previous.get("site_id")
            prev_backend = previous.get("backend")
            if prev_site is not None and prev_site != self.site_id:
                raise ValueError(
                    f"latest site_id mismatch: file={prev_site!r} config={self.site_id!r}"
                )
            if prev_backend is not None and prev_backend != self.backend:
                raise ValueError(
                    f"latest backend mismatch: file={prev_backend!r} config={self.backend!r}"
                )

        prev_hash = previous.get("content_hash") if previous else None
        prev_groups = previous.get("groups") if previous else None
        if prev_groups is not None and not isinstance(prev_groups, list):
            prev_groups = None

        record = {
            "schema_version": SCHEMA_VERSION,
            "site_id": self.site_id,
            "backend": self.backend,
            "fetched_at": fetched_at,
            "count": len(groups),
            "content_hash": digest,
            "groups": groups,
        }

        added_n = removed_n = modified_n = 0
        changed = prev_hash != digest

        if not changed:
            write_json_atomic(self.latest_path, record, mode=0o644)
            return PersistResult(
                changed=False,
                content_hash=digest,
                count=len(groups),
                added=0,
                removed=0,
                modified=0,
            )

        # Content changed: tail-event dedup
        last_ev = last_complete_event(self.events_path)
        last_after = None
        if last_ev is not None:
            last_after = last_ev.get("after_hash") or last_ev.get("content_hash")

        if last_after != digest:
            if previous is None or prev_hash is None:
                event_name = "initial"
                diff = diff_groups(None, groups)
                # Full initial set in added
                diff["added"] = [
                    {
                        "id": g["id"],
                        "name": g["name"],
                        "rate_multiplier": g["rate_multiplier"],
                        "description": g.get("description", ""),
                    }
                    for g in groups
                ]
                diff["removed"] = []
                diff["modified"] = []
                before_hash = None
            else:
                event_name = "groups_changed"
                old_list = prev_groups if isinstance(prev_groups, list) else []
                # ensure old entries look like normalized dicts
                old_norm: list[dict[str, Any]] = []
                for g in old_list:
                    if isinstance(g, dict) and "id" in g:
                        old_norm.append(g)
                diff = diff_groups(old_norm, groups)
                before_hash = prev_hash

            event = {
                "schema_version": SCHEMA_VERSION,
                "site_id": self.site_id,
                "backend": self.backend,
                "observed_at": fetched_at,
                "event": event_name,
                "before_hash": before_hash,
                "after_hash": digest,
                "added": diff["added"],
                "removed": diff["removed"],
                "modified": diff["modified"],
            }
            append_jsonl_fsync(self.events_path, event)
            added_n = len(diff["added"])
            removed_n = len(diff["removed"])
            modified_n = len(diff["modified"])

        write_json_atomic(self.latest_path, record, mode=0o644)
        return PersistResult(
            changed=True,
            content_hash=digest,
            count=len(groups),
            added=added_n,
            removed=removed_n,
            modified=modified_n,
        )


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            try:
                self._fh.close()
            finally:
                self._fh = None
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise RuntimeError(f"another monitor instance holds the lock: {self.path}") from exc
            raise
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"{os.getpid()}\n")
        self._fh.flush()

    def acquire_wait(
        self,
        wait_seconds: float,
        *,
        poll_interval: float = 1.0,
        time_fn: Any = None,
        sleep_fn: Any = None,
    ) -> float:
        """Acquire with a bounded wait while preserving nonblocking acquire()."""
        clock = time_fn or time.monotonic
        sleep = sleep_fn or time.sleep
        started = clock()
        deadline = started + max(0.0, wait_seconds)
        interval = max(0.01, poll_interval)
        while True:
            try:
                self.acquire()
                return max(0.0, clock() - started)
            except RuntimeError:
                now = clock()
                if now >= deadline:
                    raise
                sleep(min(interval, max(0.0, deadline - now)))

    def release(self) -> None:
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
