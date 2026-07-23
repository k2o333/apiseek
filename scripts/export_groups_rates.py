#!/usr/bin/env python3
"""Merge data/*/groups_latest.json into docs/websites/table/groups_rates.{csv,json}.

Read-only over snapshots. Includes raw + effective rate columns when present;
falls back to rate_multiplier for effective when the field is missing (pre-getmulti).
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OUT_DIR = PROJECT_ROOT / "docs" / "websites" / "table"
CSV_PATH = OUT_DIR / "groups_rates.csv"
JSON_PATH = OUT_DIR / "groups_rates.json"

CSV_FIELDS = [
    "site_id",
    "backend",
    "fetched_at",
    "group_id",
    "group_name",
    "rate_multiplier",
    "rate_multiplier_effective",
    "rate_divisor",
    "status",
    "platform",
    "description",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def detect_backend(site_id: str, payload: dict) -> str:
    backend = payload.get("backend")
    if isinstance(backend, str) and backend.strip():
        return backend.strip()
    auth = DATA_DIR / site_id / "auth_state.json"
    if auth.exists():
        return "newapi"
    return "sub2api"


def load_rows() -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    sites_meta: list[dict] = []
    if not DATA_DIR.is_dir():
        return rows, sites_meta

    for site_dir in sorted(p for p in DATA_DIR.iterdir() if p.is_dir() and not p.name.startswith("_")):
        latest = site_dir / "groups_latest.json"
        if not latest.exists():
            continue
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        groups = payload.get("groups")
        if not isinstance(groups, list):
            continue
        site_id = str(payload.get("site_id") or site_dir.name)
        backend = detect_backend(site_id, payload)
        fetched_at = payload.get("fetched_at") or ""
        rate_divisor = payload.get("rate_divisor")
        if rate_divisor is None:
            rate_divisor = 1
        content_hash = payload.get("content_hash")
        sites_meta.append(
            {
                "site_id": site_id,
                "backend": backend,
                "fetched_at": fetched_at,
                "count": len(groups),
                "source": str(latest.relative_to(PROJECT_ROOT)),
                "content_hash": content_hash,
                "rate_divisor": rate_divisor,
            }
        )
        for group in groups:
            if not isinstance(group, dict):
                continue
            raw = group.get("rate_multiplier")
            effective = group.get("rate_multiplier_effective")
            if effective is None and isinstance(raw, (int, float)) and not isinstance(raw, bool):
                try:
                    effective = float(raw) / float(rate_divisor)
                except (TypeError, ValueError, ZeroDivisionError):
                    effective = raw
            rows.append(
                {
                    "site_id": site_id,
                    "backend": backend,
                    "fetched_at": fetched_at,
                    "group_id": group.get("id", ""),
                    "group_name": group.get("name", ""),
                    "rate_multiplier": raw if raw is not None else "",
                    "rate_multiplier_effective": effective if effective is not None else "",
                    "rate_divisor": rate_divisor,
                    "status": group.get("status", ""),
                    "platform": group.get("platform", ""),
                    "description": group.get("description", ""),
                }
            )
    return rows, sites_meta


def main() -> int:
    rows, sites_meta = load_rows()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = utc_now_iso()

    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    payload = {
        "generated_at": generated_at,
        "site_count": len(sites_meta),
        "row_count": len(rows),
        "sites": sites_meta,
        "rows": rows,
    }
    JSON_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"wrote {CSV_PATH.relative_to(PROJECT_ROOT)} and "
        f"{JSON_PATH.relative_to(PROJECT_ROOT)} "
        f"sites={len(sites_meta)} rows={len(rows)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
