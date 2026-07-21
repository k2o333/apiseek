#!/usr/bin/env python3
"""Full sub2api flow: groups -> ensure 1 key per group -> /v1/models.

Uses stored access/refresh tokens under data/<site>/token.json.
Serial only. Never deletes keys. Redacts secrets in stdout/JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "sub2api-monitor/1.0.0"
TIMEOUT = (10.0, 45.0)


def mask(s: str | None, head: int = 8, tail: int = 4) -> str:
    if not s:
        return ""
    if len(s) <= head + tail + 3:
        return s[:2] + "***"
    return f"{s[:head]}...{s[-tail:]}"


def load_env_base_url(site_id: str) -> str:
    env_path = ROOT / "sites" / f"{site_id}.env"
    base = ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == "MONITOR_BASE_URL":
            base = v.strip().strip("\"'")
    if not base:
        raise SystemExit(f"{site_id}: MONITOR_BASE_URL missing")
    return base.rstrip("/")


def load_token(site_id: str) -> dict[str, Any]:
    path = ROOT / "data" / site_id / "token.json"
    return json.loads(path.read_text(encoding="utf-8"))


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Connection": "close",
        }
    )
    return s


def unwrap_list(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    candidates: list[Any] = [payload.get("data")]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("data"), data.get("items"), data.get("list")])
    candidates.extend([payload.get("items"), payload.get("list")])
    for c in candidates:
        if isinstance(c, list):
            return c
    return None


def extract_keys_list(kbody: Any) -> list[Any] | None:
    keys_list = unwrap_list(kbody)
    if keys_list is not None:
        return keys_list
    if isinstance(kbody, dict):
        data = kbody.get("data")
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
    return None


def try_refresh(sess: requests.Session, base: str, token: dict[str, Any]) -> dict[str, Any] | None:
    rt = token.get("refresh_token")
    if not rt:
        return None
    r = sess.post(
        f"{base}/api/v1/auth/refresh",
        json={"refresh_token": rt},
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        print(f"  refresh HTTP {r.status_code}: {r.text[:200]!r}")
        return None
    try:
        body = r.json()
    except Exception as exc:
        print(f"  refresh non-json: {exc}")
        return None
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or not data.get("access_token"):
        print(f"  refresh bad body: {list(body) if isinstance(body, dict) else type(body)}")
        return None
    print(f"  refresh ok, access={mask(data['access_token'])}")
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or rt,
        "access_expires_at": token.get("access_expires_at"),
        "saved_at": token.get("saved_at"),
    }


def ensure_access(sess: requests.Session, base: str, token: dict[str, Any]) -> str | None:
    access = token.get("access_token")
    exp = token.get("access_expires_at")
    now = int(time.time())
    need_refresh = not access
    if exp is not None:
        try:
            if int(exp) <= now + 120:
                need_refresh = True
        except (TypeError, ValueError):
            pass
    if need_refresh:
        print("  access missing/near-expiry -> refresh")
        new = try_refresh(sess, base, token)
        if new:
            token.update(new)
            return new["access_token"]
        if access:
            print("  refresh failed; trying existing access anyway")
            return access
        return None
    return access


def auth_headers(access: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}


def get_groups(sess: requests.Session, base: str, access: str) -> list[dict[str, Any]]:
    r = sess.get(
        f"{base}/api/v1/groups/available",
        headers={"Authorization": f"Bearer {access}"},
        timeout=TIMEOUT,
    )
    print(f"  GET groups -> HTTP {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"groups HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    groups = unwrap_list(body)
    if groups is None:
        raise RuntimeError(f"groups envelope unexpected: {list(body) if isinstance(body, dict) else type(body)}")
    out: list[dict[str, Any]] = []
    for g in groups:
        if isinstance(g, dict) and g.get("id") is not None:
            out.append(g)
    return out


def get_keys(sess: requests.Session, base: str, access: str) -> list[dict[str, Any]]:
    r = sess.get(
        f"{base}/api/v1/keys?p=1&page_size=100",
        headers={"Authorization": f"Bearer {access}"},
        timeout=TIMEOUT,
    )
    print(f"  GET keys -> HTTP {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"keys HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    keys = extract_keys_list(body)
    if keys is None:
        raise RuntimeError(f"keys envelope unexpected: {list(body) if isinstance(body, dict) else type(body)}")
    return [k for k in keys if isinstance(k, dict)]


def create_key(sess: requests.Session, base: str, access: str, name: str) -> dict[str, Any]:
    r = sess.post(
        f"{base}/api/v1/keys",
        headers=auth_headers(access),
        json={"name": name},
        timeout=TIMEOUT,
    )
    print(f"  POST keys name={name!r} -> HTTP {r.status_code}")
    if r.status_code not in (200, 201):
        raise RuntimeError(f"create key HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or data.get("id") is None:
        raise RuntimeError(f"create key bad body: {json.dumps(body, ensure_ascii=False)[:300]}")
    return data


def bind_key(
    sess: requests.Session,
    base: str,
    access: str,
    key_id: int | str,
    name: str,
    group_id: int | str,
) -> dict[str, Any]:
    r = sess.put(
        f"{base}/api/v1/keys/{key_id}",
        headers=auth_headers(access),
        json={"name": name, "group_id": group_id},
        timeout=TIMEOUT,
    )
    print(f"  PUT keys/{key_id} group_id={group_id} -> HTTP {r.status_code}")
    if r.status_code != 200:
        raise RuntimeError(f"bind key HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    data = body.get("data") if isinstance(body, dict) else body
    if not isinstance(data, dict):
        raise RuntimeError(f"bind key bad body: {json.dumps(body, ensure_ascii=False)[:300]}")
    return data


def list_models(sess: requests.Session, base: str, api_key: str) -> tuple[list[str], str | None, str | None]:
    """Return (models, used_url_path, error)."""
    for path in ("/v1/models", "/api/v1/models"):
        try:
            r = sess.get(
                f"{base}{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
            continue
        if r.status_code != 200:
            last = f"HTTP {r.status_code}: {r.text[:120]!r}"
            continue
        try:
            body = r.json()
        except Exception as exc:
            last = f"json: {exc}"
            continue
        mlist = unwrap_list(body)
        if mlist is None and isinstance(body, dict) and isinstance(body.get("data"), list):
            mlist = body["data"]
        if mlist is None:
            last = f"unexpected body keys={list(body) if isinstance(body, dict) else type(body)}"
            continue
        models: list[str] = []
        for m in mlist:
            if isinstance(m, dict):
                mid = m.get("id") or m.get("name") or m.get("model")
                if mid:
                    models.append(str(mid))
            elif isinstance(m, str):
                models.append(m)
        return models, path, None
    return [], None, last  # type: ignore[name-defined]


def ensure_keys_cover_groups(
    sess: requests.Session,
    base: str,
    access: str,
    groups: list[dict[str, Any]],
    keys: list[dict[str, Any]],
    *,
    create_missing: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (keys_after, created_records). Only create for Missing = G - C."""
    used = {k.get("group_id") for k in keys if k.get("group_id") is not None}
    created: list[dict[str, Any]] = []
    missing = [g for g in groups if g.get("id") not in used]
    print(f"  coverage: groups={len(groups)} covered={len(used & {g.get('id') for g in groups})} missing={len(missing)}")

    if not create_missing:
        if missing:
            print("  note: --no-create; will only probe existing keys")
            for g in missing:
                print(f"    missing group id={g.get('id')} name={g.get('name')!r}")
        return keys, created

    for g in missing:
        gid = g["id"]
        name = str(g.get("name") or f"group-{gid}")
        print(f"  [create] group id={gid} name={name!r}")
        new_key = create_key(sess, base, access, name)
        new_id = new_key["id"]
        bound = bind_key(sess, base, access, new_id, name, gid)
        created.append(
            {
                "group_id": gid,
                "group_name": name,
                "key_id": new_id,
                "key_preview": mask(new_key.get("key"), 6, 4),
                "bound_group_id": bound.get("group_id"),
            }
        )
        print(
            f"    ok key_id={new_id} key={mask(new_key.get('key'), 6, 4)} "
            f"bound_group_id={bound.get('group_id')}"
        )
        time.sleep(0.2)

    keys_after = get_keys(sess, base, access)
    covered = {k.get("group_id") for k in keys_after if k.get("group_id") is not None}
    still = [g for g in groups if g.get("id") not in covered]
    if still:
        names = [g.get("name") for g in still]
        raise RuntimeError(f"still missing groups after create: {names}")
    print(f"  [verify] all {len(groups)} groups covered by keys (keys_total={len(keys_after)})")
    return keys_after, created


def pick_key_for_group(keys: list[dict[str, Any]], group_id: Any) -> dict[str, Any] | None:
    """One group may have multiple keys historically; pick first with secret."""
    with_secret = [k for k in keys if k.get("group_id") == group_id and k.get("key")]
    if with_secret:
        return with_secret[0]
    any_bound = [k for k in keys if k.get("group_id") == group_id]
    return any_bound[0] if any_bound else None


def probe_site(site_id: str, *, create_missing: bool) -> dict[str, Any]:
    print(f"\n{'=' * 60}\nSITE: {site_id}\n{'=' * 60}")
    base = load_env_base_url(site_id)
    token = load_token(site_id)
    print(f"  base={base}")
    print(f"  token access={mask(token.get('access_token'))} exp={token.get('access_expires_at')}")

    result: dict[str, Any] = {
        "site_id": site_id,
        "base_url": base,
        "ok": False,
        "groups": [],
        "created_keys": [],
        "group_models": [],
        "errors": [],
    }

    with session() as sess:
        access = ensure_access(sess, base, token)
        if not access:
            result["errors"].append("no access token")
            print("  FAIL: no access token")
            return result

        try:
            groups = get_groups(sess, base, access)
        except Exception as exc:
            # one refresh retry on auth-ish failure
            print(f"  groups error: {exc}; try refresh")
            new = try_refresh(sess, base, token)
            if not new:
                result["errors"].append(str(exc))
                return result
            access = new["access_token"]
            token.update(new)
            try:
                groups = get_groups(sess, base, access)
            except Exception as exc2:
                result["errors"].append(str(exc2))
                return result

        print(f"  groups count={len(groups)}")
        for g in groups:
            row = {
                "id": g.get("id"),
                "name": g.get("name"),
                "platform": g.get("platform"),
                "rate": g.get("rate_multiplier"),
                "status": g.get("status"),
            }
            result["groups"].append(row)
            print(
                f"    - id={row['id']} name={row['name']!r} "
                f"platform={row['platform']} rate={row['rate']} status={row['status']}"
            )

        try:
            keys = get_keys(sess, base, access)
        except Exception as exc:
            result["errors"].append(str(exc))
            return result
        print(f"  keys count (before ensure)={len(keys)}")
        for k in keys:
            group = k.get("group") if isinstance(k.get("group"), dict) else {}
            print(
                f"    - key_id={k.get('id')} name={k.get('name')!r} "
                f"group_id={k.get('group_id')} group={group.get('name')!r} "
                f"has_key={bool(k.get('key'))}"
            )

        try:
            keys, created = ensure_keys_cover_groups(
                sess, base, access, groups, keys, create_missing=create_missing
            )
            result["created_keys"] = created
        except Exception as exc:
            result["errors"].append(f"ensure keys: {exc}")
            print(f"  FAIL ensure keys: {exc}")
            return result

        # Prefer one model probe per available group (1:1 strategy)
        ok_n = 0
        for g in groups:
            gid = g.get("id")
            gname = g.get("name")
            k = pick_key_for_group(keys, gid)
            if not k:
                result["group_models"].append(
                    {
                        "group_id": gid,
                        "group_name": gname,
                        "platform": g.get("platform"),
                        "status": "no_key",
                        "model_count": 0,
                        "models": [],
                    }
                )
                print(f"  models SKIP group={gname!r}: no key")
                continue
            api_key = k.get("key")
            if not api_key:
                result["group_models"].append(
                    {
                        "group_id": gid,
                        "group_name": gname,
                        "key_id": k.get("id"),
                        "status": "no_secret",
                        "model_count": 0,
                        "models": [],
                    }
                )
                print(f"  models SKIP group={gname!r} key_id={k.get('id')}: no secret")
                continue

            models, path, err = list_models(sess, base, api_key)
            entry = {
                "group_id": gid,
                "group_name": gname,
                "platform": g.get("platform"),
                "rate": g.get("rate_multiplier"),
                "key_id": k.get("id"),
                "key_name": k.get("name"),
                "key_preview": mask(api_key, 6, 4),
                "status": "ok" if path else "fail",
                "url": path,
                "error": err,
                "model_count": len(models),
                "models": models,
            }
            result["group_models"].append(entry)
            if path:
                ok_n += 1
                preview = ", ".join(models[:8])
                more = f" ...(+{len(models) - 8})" if len(models) > 8 else ""
                print(
                    f"  models OK group={gname!r} key_id={k.get('id')} "
                    f"count={len(models)} via {path}"
                )
                print(f"    {preview}{more}")
            else:
                print(f"  models FAIL group={gname!r} key_id={k.get('id')}: {err}")

        covered_groups = sum(1 for m in result["group_models"] if m.get("status") == "ok")
        result["ok"] = covered_groups == len(groups) and len(groups) > 0
        print(
            f"  SUMMARY: groups={len(groups)} created={len(result['created_keys'])} "
            f"models_ok={ok_n}/{len(groups)} overall_ok={result['ok']}"
        )
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Ensure 1 key/group then list models")
    parser.add_argument("sites", nargs="*", default=["littleapi", "aiapibank", "pinaic"])
    parser.add_argument(
        "--no-create",
        action="store_true",
        help="Do not create missing keys (read-only probe)",
    )
    args = parser.parse_args()
    create_missing = not args.no_create

    all_results = []
    for site_id in args.sites:
        try:
            all_results.append(probe_site(site_id, create_missing=create_missing))
        except Exception as exc:
            print(f"\nSITE {site_id} EXCEPTION: {type(exc).__name__}: {exc}")
            all_results.append({"site_id": site_id, "ok": False, "errors": [str(exc)]})
        time.sleep(0.5)

    out_dir = ROOT / "data" / "_probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "groups_models_full.json"
    # Never persist full API secrets
    safe = json.loads(json.dumps(all_results, ensure_ascii=False, default=str))
    for site in safe:
        for gm in site.get("group_models") or []:
            gm.pop("api_key", None)
    out_path.write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {out_path}")

    print("\n" + "=" * 60)
    print("FINAL")
    print("=" * 60)
    for r in all_results:
        gm = r.get("group_models") or []
        ok_m = sum(1 for x in gm if x.get("status") == "ok")
        print(
            f"  {r.get('site_id')}: ok={r.get('ok')} "
            f"groups={len(r.get('groups') or [])} "
            f"created={len(r.get('created_keys') or [])} "
            f"models_ok={ok_m}/{len(gm)} "
            f"errors={r.get('errors') or []}"
        )
    return 0 if all(r.get("ok") for r in all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
