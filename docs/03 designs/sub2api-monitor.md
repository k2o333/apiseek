# Sub2API Groups Monitor

> 状态：active implementation design
> 最近复核：2026-07-23
> 实现：`sub2api_monitor.py`

## 1. 适用范围

本设计适用于已逐站验证的 Sub2API JWT/Bearer 部署。它不自动覆盖 New-API session 部署，也不因 endpoint 名称相似就授予新站兼容性。

生产默认采用每站独立 timer + oneshot：

```text
sub2api-monitor-once@<id>.timer
  -> sub2api-monitor-once@<id>.service
     -> .venv/bin/python sub2api_monitor.py \
          --env-file sites/<id>.env --once
```

旧 `sub2api-monitor@<id>.service` 是回滚用常驻 simple unit，不是新站默认路径。同站禁止 simple 与 once timer 同时 active。

## 2. 组件与所有权

| 组件 | 职责 |
|---|---|
| `sites/<id>.env` | 站级 origin、credentials、paths、timeouts、data paths |
| `AuthGroupClient` | login、refresh、Bearer groups、一次认证恢复 |
| `TokenStore` | `token.json` 的 0600 原子持久化 |
| `GroupMonitor` | poll、分类重试、快照/event、可选 T-new |
| `InstanceLock` | 同一 data dir 的进程互斥 |
| `install_service.sh` | 安装/验证 units，默认 enable groups timer |

每站必须使用独立 env、`DATA_DIR`、token 文件和 lock。`MONITOR_SITE_ID` 只允许小写字母、数字和连字符；base URL 必须 HTTPS。

## 3. 单轮数据流

```text
load config  (+ MONITOR_RATE_DIVISOR, default 1)
  -> acquire monitor.lock (non-blocking)
  -> load token cache
  -> token near expiry? refresh : reuse
  -> refresh unavailable/rejected? password login
  -> GET groups with Bearer
  -> 401: recover auth once, then retry groups once
  -> annotate_group_rates: rate_multiplier_effective = raw / rate_divisor
  -> canonicalize + hash + diff  (hash includes full group objects, hence effective)
  -> append event when legacy hash policy says changed
  -> atomic replace groups_latest.json  (top-level rate_divisor + per-group effective)
  -> optional incremental models for true new groups
  -> release lock
```

region/HTML/contract/auth 类错误不做无意义的进程级连打。timeout、5xx、429 和 network 属于 transient；`--once` 在一个进程中最多尝试 `--once-attempts` 次，默认 3，并复用 Retry-After/退避语义。

## 4. 调度语义

`sub2api-monitor-once@.timer` 当前参数：

```ini
OnBootSec=1min
OnUnitInactiveSec=240s
RandomizedDelaySec=60s
AccuracySec=1s
```

相邻启动间隔约等于本轮运行时长 + 240s + U(0,60s)。文档中的“约 4～5 分钟”不是固定墙钟 300s。

生产周期以 timer 为权威。env 的 `POLL_INTERVAL_SECONDS` 只驱动前台 `run_loop` 调试；installer 会把它与 timer 期望中点约 270s 比较，偏差超过 30s 时拒绝 enable。

Service 使用 `Type=oneshot`、`TimeoutStartSec=240`，无 `[Install]`、无 `Restart=always`。Timer 无 `Requires=`，避免 enable/start timer 时绕过 `OnBootSec` 拉起 service。

## 5. CLI

```bash
.venv/bin/python sub2api_monitor.py --env-file sites/<id>.env --validate
.venv/bin/python sub2api_monitor.py --env-file sites/<id>.env --once
.venv/bin/python sub2api_monitor.py --env-file sites/<id>.env --once --once-attempts 3
.venv/bin/python sub2api_monitor.py --env-file sites/<id>.env
```

| Mode | 用途 | 退出码 |
|---|---|---|
| `--validate` | 解析并校验配置，不请求 provider | 0/2 |
| `--once` | timer 的有界重试单轮 | 成功 0、运行失败 1、前置失败 2 |
| 默认 loop | 前台调试或 legacy simple | signal 正常停止 0 |

Models flags 见 [Sub2API models](./sub2api-models.md)。`--once` 不能与 models flags 组合。

## 6. 持久化

```text
data/<id>/
  token.json
  groups_latest.json
  groups_events.jsonl
  monitor.lock
```

`token.json` 必须 0600。groups latest 只在完整成功后原子替换；失败保留旧值。当前 groups 使用 Sub2API legacy schema 和历史 hash/event dedup，正式目标见 [storage contract](../02%20specs/contracts/storage.md)。

### 6.1 分组倍率规范化

| 字段 | 含义 |
|------|------|
| `rate_multiplier` | Provider 原始倍率（不改写） |
| `rate_multiplier_effective` | `rate_multiplier / MONITOR_RATE_DIVISOR` |
| 顶层 `rate_divisor` | 本快照使用的 divisor |

- 配置：`MONITOR_RATE_DIVISOR`（正有限浮点，默认 `1`）。**pinaic / hubway 配 `10`**（例 raw `16` → effective `1.6`）；其余站省略或 `1`。
- 运行时只读 env，禁止按 `site_id` 写死 divisor。
- 首版仅主字段 `rate_multiplier`；`image`/`video`/`peak` 附属倍率不换算。
- 消费：与后台对照读 raw；业务报价/跨站比较读 effective。models 快照 **不**复制 rate 字段，按需 join groups。
- 实现：`monitor_rates.py`（`parse_rate_divisor` / `annotate_group_rates`）+ `sub2api_monitor.py` 成功路径。

## 7. 错误与恢复

| 类别 | 行为 |
|---|---|
| token 临近过期 | refresh；失败回退 password login |
| groups 401 | 一次 auth recovery，再试一次 |
| 403 region/HTML | 分类失败，不刷 login |
| 429 | 尊重 Retry-After，受 oneshot 预算约束 |
| timeout/5xx/network | 保留 token/latest，进程内有界重试 |
| contract | 不覆盖 latest，不做 transient 连打 |
| lock conflict | 不访问 provider，返回 2 |

### 7.1 会话网络指纹（session binding）

部分 Sub2API 部署（已验证：`ai.klinkw.com`）会在 JWT 中写入 `bnd`，把 access token 绑到登录时的网络指纹。若 **login/refresh 与紧随其后的 groups 请求不在同一 keep-alive 连接上**（例如对每条请求强制 `Connection: close`），groups 会返回：

```json
{"code":"SESSION_BINDING_MISMATCH","message":"Session network fingerprint changed, please login again"}
```

实现约定：

- `AuthGroupClient` **不**在请求头里强制 `Connection: close`，保证同轮 login/refresh → groups（及一次 auth recovery）可复用连接；
- `GroupMonitor` 仍可在 **轮次之间** `session.close()`，避免长间隔 idle keep-alive 假死；轮次开始时若旧 token 的指纹已失效，走既有 401 recovery（refresh/login 后再 groups）。

## 8. 安装与回滚

```bash
./install_service.sh <id>
systemctl start sub2api-monitor-once@<id>.service
systemctl status sub2api-monitor-once@<id>.timer
journalctl -u sub2api-monitor-once@<id> -n 80 --no-pager
```

回滚：

```bash
systemctl disable --now sub2api-monitor-once@<id>.timer
systemctl stop sub2api-monitor-once@<id>.service
systemctl enable --now sub2api-monitor@<id>.service
```

数据格式和 token 不需要随调度形态回滚。

## 9. 已知契约差距

- 当前 legacy groups 尚无 frozen schema/vectors；
- once service 缺目标 `UMask=0077`；
- legacy simple hardening/生命周期仅作回滚兼容；
- config manifest 尚未生成；
- provider profile 仍需逐站脱敏 fixtures 和 capability 记录。

这些差距记录在正式 manifest 中；历史 timer 评审和迁移推演保留于 `docs/drafts/onethred/`。
