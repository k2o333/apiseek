# New-API Legacy Groups Monitor

> 状态：active implementation design
> 最近复核：2026-07-23
> 实现：`newapi_monitor.py`、`monitor_storage.py`

## 1. 适用范围

本适配器只覆盖 BotCF/TorchAI 已探活的 legacy session groups 协议，不声称支持任意 New-API 版本。

它与 Sub2API 共享“每站 env + data + timer/oneshot”的运维形态，但认证和存储 schema 独立。两类 backend 禁止共用同一个 `DATA_DIR`。

## 2. Provider 形状

共同流程：

```text
POST /api/user/login?turnstile=
  body: {username, password}
  -> session cookie
GET /api/user/self/groups
```

Redirect 禁止跟随。TorchAI 还要求 `new-api-user: <login data.id>`；BotCF 不要求。配置通过 `REQUIRE_NEW_API_USER_HEADER` 表达这一已验证差异。

groups response 的 `data` 必须是非空 object。key 是 group name；ratio 必须是有限非负数。空 object 或任一非法 item 都是 contract failure，不覆盖 latest。

## 3. 单轮数据流

New-API 默认 CLI 本身就是单轮，不提供 `--once`：

```text
load config  (+ MONITOR_RATE_DIVISOR, default 1)
  -> acquire monitor.lock (non-blocking)
  -> load auth_state.json
  -> validate cached origin/site/session/user_id
  -> ensure session, login when required
  -> GET groups, auth failure recovery once
  -> normalize complete response  (ratio -> rate_multiplier)
  -> annotate_group_rates  (rate_multiplier_effective = raw / rate_divisor)
  -> SnapshotStore.persist_success
       -> repair JSONL partial tail
       -> tail event dedup (hash/diff include effective)
       -> append+fsync event when changed
       -> atomic replace latest  (top-level rate_divisor)
  -> optional T-new models
  -> release lock
```

Client 有整体 deadline 和有界请求重试。HTTP 与 business `success=false` 都参与 auth/contract 分类；认证恢复只有一次，避免登录循环。

## 4. Storage

```text
data/<id>/
  auth_state.json
  groups_latest.json
  groups_events.jsonl
  monitor.lock
```

`auth_state.json` 必须 0600，包含可恢复 session 状态，不得进入普通 snapshot/log。

groups latest 使用 `schema_version=1`、`site_id`、`backend=newapi`，保存规范化 group：`id`/`name`/`rate_multiplier`/`rate_multiplier_effective`/`description`，以及顶层 `rate_divisor`。Event 的 before/after 同时含 raw 与 effective；hash 纳入 effective，因此仅改 `MONITOR_RATE_DIVISOR` 也会记 modified。采用 tail-after-hash 去重，因此 `A->B->A` 会记录恢复事件。

Event 先 append+fsync，latest 后 atomic replace。若进程在两者之间崩溃，下次采到同一结果时通过尾事件避免重复。JSONL 半行尾在 append 前修复。

**倍率：** 与 Sub2API 同一旋钮 `MONITOR_RATE_DIVISOR`（默认 `1`；botcf/torchai 通常保持 1，effective==raw 仍落盘）。实现见 `monitor_rates.py` + `monitor_storage.py`。

详细目标不变量见 [storage contract](../02%20specs/contracts/storage.md)。

## 5. CLI 与退出

```bash
.venv/bin/python newapi_monitor.py --env-file sites/<id>.env --validate
.venv/bin/python newapi_monitor.py --env-file sites/<id>.env
```

| 结果 | Exit |
|---|---:|
| validate/collect success | 0 |
| provider/auth/contract failure | 1 |
| config 或 lock failure | 2 |
| groups success、可选 T-new partial failure | 0 |

Models flags 见 [New-API models](./newapi-models.md)。

## 6. systemd

```text
newapi-monitor-once@<id>.timer
  -> newapi-monitor-once@<id>.service
     -> newapi_monitor.py --env-file sites/<id>.env
```

Timer 使用 `OnBootSec=1min`、`OnUnitInactiveSec=240s`、60s jitter、`AccuracySec=1s`。Service 是 240s oneshot，带 `UMask=0077`、`NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=strict` 和 data-only `ReadWritePaths`。

```bash
./install_newapi_service.sh botcf torchai
systemctl start newapi-monitor-once@<id>.service
journalctl -u newapi-monitor-once@<id> -n 80 --no-pager
```

Installer 校验 env stem/site id 全局唯一，收紧 env mode，运行 validate 和 systemd verify。

## 7. 站级边界

| Site | Header | Models support |
|---|---|---|
| BotCF | 无 `new-api-user` | 不在正式支持范围 |
| TorchAI | 必须 `new-api-user` | rc.21 目标 profile，mutation 尚待冻结探针 |

站级 URL、历史探活和安全说明见 [BotCF](../websites/botcf.md) 与 [TorchAI](../websites/torchai.md)。

## 8. 已知契约差距

- provider profile 仍缺完整脱敏 fixtures 和机器 capability 表；
- config manifest 尚未生成；
- `--validate` 与 models flags 的组合策略尚未作为 contract test 固化；
- installer enable models 前尚未强制验证 bootstrap/provider capability；
- 通用 reader 对 unknown version/backend/site 的完整拒绝矩阵仍待冻结。

历史 architecture、data model、site notes 和评审保留于 `docs/drafts/newapi/`。
