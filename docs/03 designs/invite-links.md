# 邀请链接采集

> 状态：active  
> 最近复核：2026-07-23  
> 契约：`storage/invite-link-v1`（draft）→ [storage.md](../02%20specs/contracts/storage.md)

## 目标

按站拉取推广码并落盘可分享邀请链接，供运维查询；与 groups/models 采集解耦。

## 入口

```bash
.venv/bin/python invite_links.py --env-file sites/<id>.env --validate
.venv/bin/python invite_links.py --env-file sites/<id>.env
.venv/bin/python invite_links.py --env-file sites/<id>.env --force
```

- 后端自动识别：env 含 `TOKEN_STATE_FILE` 或 `MONITOR_LOGIN_PATH` → Sub2API，否则 New-API。
- 退出码：0 成功/TTL 跳过；1 远端或 contract；2 配置。

## 数据

路径：`data/<site-id>/invite_latest.json`（atomic，0644）。

刷新：

1. `MONITOR_BASE_URL` 规范化 origin 变化 → **立即**远端重拉并重写 `invite_link`；
2. origin 未变 → `ttl_seconds` 内（默认 14 天）可跳过远端；
3. 远端失败 **不覆盖** 已有合法 latest。

拼装：`{base_url}/register?aff={aff_code}`。

| backend | 拉码 |
|---|---|
| sub2api | `GET /api/v1/user/aff`（Bearer，UA=`sub2api-monitor/*`） |
| newapi | `GET /api/user/self` 或 `/api/user/aff`（session + 可选 `new-api-user`） |

鉴权复用 `token.json` / `auth_state.json` 与对应 monitor 客户端；不把 JWT/session 写入 invite 文件。

## 非目标

- 不挂 groups systemd timer（可后续独立 timer）。
- 第一版无 events JSONL。
- 契约仍为 draft，未 frozen。
