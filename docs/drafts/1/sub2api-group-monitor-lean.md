# Sub2API 中转站分组监控（低熵方案）

## 1. 目标

对多个 Sub2API 兼容中转站做**长期、低频**的分组信息监控：

- 默认每 **5 分钟**轮询一次；可配置为 **10 分钟**或其他合理间隔。
- 始终保留每个站的**当前完整分组快照**。
- 仅在分组内容变化时记录历史，避免全量 JSONL 膨胀。
- 新增站点：加配置/密钥、启用一个 systemd 实例，**不复制脚本**。

本文是推荐实施方案。同目录下更长的
`sub2api-multi-site-monitor-*.md` 可作为背景参考，**不以它们为实施范围**。

## 2. 非目标（刻意不做）

在站点数量仍为个位数、周期 ≥ 5 分钟时，不引入：

- SQLite / 跨站报表数据库
- 独立 `poll_runs` 持久化（journal 已覆盖）
- 无变化时的 heartbeat 事件文件
- 邮件/企微/Prometheus 等告警适配层
- 多适配器框架或十模块包结构
- 30–60 秒级高频轮询假设

痛点出现后再加，而不是先搭平台。

## 3. 核心循环

每个站点一个常驻进程：

```text
ensure_token → GET groups → content_hash
  → 成功：原子写 groups_latest.json
  → hash 变化：追加一行 groups_events.jsonl
  → 失败：不覆盖 latest，有界退避后重试
  → sleep(poll_interval - elapsed)
```

一句话：**用有效 token 定期拉只读分组接口；latest 每次成功更新；历史只记变化。**

## 4. 布局

```text
sub2api_monitor.py              # 单入口（可由现有 aiapibank_monitor.py 演进）
sites.yaml                      # 非敏感站点注册表（可进 git）
secrets/<site-id>.env           # 用户名密码，0600（不进 git）
data/<site-id>/
  token.json                    # access/refresh，0600
  groups_latest.json            # 当前完整快照
  groups_events.jsonl           # 仅变化事件
sub2api-monitor@.service        # systemd 模板，实例名 = site-id
```

相对路径一律相对 `sites.yaml` 所在目录解析。

### 4.1 `sites.yaml` 示例

```yaml
version: 1

defaults:
  login_path: /api/v1/auth/login
  refresh_path: /api/v1/auth/refresh
  groups_path: /api/v1/groups/available
  username_field: email
  poll_interval_seconds: 300    # 5 分钟；某站可覆盖为 600
  connect_timeout_seconds: 10
  read_timeout_seconds: 30
  refresh_margin_seconds: 600
  request_jitter_seconds: 10    # 多站错峰

sites:
  aiapibank:
    name: AIAPIBANK
    base_url: https://www.aiapibank.com
    credentials_file: secrets/aiapibank.env
    data_dir: data/aiapibank
    enabled: true

  pinaic:
    name: PinAI
    base_url: https://app.pinaic.com
    credentials_file: secrets/pinaic.env
    data_dir: data/pinaic
    poll_interval_seconds: 600  # 10 分钟示例
    # proxy_url: http://host:port   # 仅当出口地区受限时
    enabled: true
```

约束：

- `site-id` 仅小写字母、数字、连字符；拒绝 `/`、`..`、空格。
- `base_url` 仅 `https://`（本地开发例外需显式开关）。
- `poll_interval_seconds` 下限 60；业务默认 300 或 600。
- 同一 `data_dir` 不得被两个站点共用。

### 4.2 凭据文件

`secrets/pinaic.env`：

```dotenv
MONITOR_USERNAME=user@example.com
MONITOR_PASSWORD=replace_me
```

- 目录 `0700`，文件 `0600`。
- 只加载**当前站点**凭据；不把密码写入 YAML、unit、日志。
- 修改凭据后只重启对应实例。

### 4.3 更简替代：仅 env + 模板 unit

若长期站点 ≤ 5 且不想维护 YAML，可用：

```text
sites/<site-id>.env   # 含 BASE_URL、路径、凭据、POLL_INTERVAL、DATA_DIR
data/<site-id>/
ExecStart=... --env-file sites/%i.env
```

行为与 YAML 方案相同，只是注册表换成目录里的 env。二选一即可，不要两套并存。

## 5. Token

- 每站 `data/<site-id>/token.json`，权限 `0600`，临时文件 + 原子替换。
- 字段：`access_token`、`refresh_token`、`access_expires_at`、`saved_at`。
- **不**在每次轮询时刷新；仅在 access 将于 `refresh_margin_seconds` 内过期时 refresh。
- 顺序：无 token → 密码登录；将过期且有 refresh → refresh；401/403 → refresh 一次，失败则登录一次，再失败则本周期结束并退避。
- refresh 返回新 refresh token 时与 access **一起**原子写入；未返回则保留旧 refresh。
- 超时 / 5xx **不**清空 token。
- JWT 只读 `exp`，不做签名校验。
- token 不得进入 YAML、latest、events、journal 正文。

## 6. 分组与历史

### 6.1 Latest

每次**成功**轮询原子覆盖 `groups_latest.json`：

```json
{
  "site_id": "pinaic",
  "fetched_at": "2026-07-20T00:00:00+00:00",
  "count": 7,
  "content_hash": "sha256:...",
  "groups": []
}
```

`content_hash`：对规范化后的 `groups` 计算（按稳定 group id 排序、JSON key 排序、固定分隔符），**不含** `fetched_at`。

### 6.2 Events（仅变化）

hash 与上一份 latest 不同时，追加一行 JSONL，例如：

```json
{
  "site_id": "pinaic",
  "observed_at": "2026-07-20T00:00:00+00:00",
  "event": "groups_changed",
  "added": [83],
  "removed": [],
  "modified": [45],
  "content_hash": "sha256:..."
}
```

- 首次成功可写 `event: "initial"`。
- 无变化：**不**追加 events。
- 失败：**不**覆盖 latest，**不**伪造“空分组”事件。

### 6.3 为何不写全量 history

当前 `groups_history.jsonl` 每次成功写完整 payload，5 分钟一档也会快速膨胀。  
变化事件 + latest 已满足审计与排障；需要时再从 events 反查。

## 7. 调度与错误

| 情况 | 行为 |
|---|---|
| 成功 | 失败计数清零；等待 `interval - 已耗时`（可加 0–jitter 秒） |
| 认证失败 | refresh → 登录各至多一次；仍失败则退避 |
| 超时 / 5xx / 429 | 保留 token；有界退避（如 10/30/60/120/300，上限不超过 interval）；429 尊重 Retry-After |
| 响应结构错误 | 记契约错误，不覆盖 latest |

其他：

- 长间隔用 `Connection: close` 或每周期新 Session，避免空闲连接假死。
- 登录 / refresh / groups 共用同一 User-Agent。
- 连接超时与读超时分开配置。
- 优雅处理 SIGTERM/SIGINT：当前安全点结束后退出。
- 每站仅一个实例，避免双写 token。

## 8. systemd

`/etc/systemd/system/sub2api-monitor@.service`：

```ini
[Unit]
Description=Sub2API group monitor for %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/projects/zhongzhuan
ExecStart=/root/projects/zhongzhuan/.venv/bin/python \
  /root/projects/zhongzhuan/sub2api_monitor.py \
  --registry /root/projects/zhongzhuan/sites.yaml \
  --site %i
Restart=always
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/root/projects/zhongzhuan/data
ReadOnlyPaths=/root/projects/zhongzhuan/sites.yaml \
  /root/projects/zhongzhuan/secrets

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now sub2api-monitor@aiapibank
systemctl enable --now sub2api-monitor@pinaic
journalctl -u sub2api-monitor@pinaic -f
```

CLI 最小集合：

```bash
python3 sub2api_monitor.py --registry sites.yaml --validate
python3 sub2api_monitor.py --registry sites.yaml --site pinaic --once
python3 sub2api_monitor.py --registry sites.yaml --site pinaic
```

迁移期可保留 `--env-file` 兼容旧配置。

## 9. 站点接入

1. 浏览器确认：登录、refresh（若有）、`GET /groups/available` 契约与 Sub2API 一致。
2. 分配 `site-id`，写入 `sites.yaml`（或 `sites/<id>.env`）。
3. 创建 `secrets/<id>.env`，`chmod 600`。
4. `--validate` → `--once`。
5. 检查 token 权限、latest 内容、events 是否合理。
6. `enable --now sub2api-monitor@<id>`，观察 ≥ 两个成功周期。

非兼容站点（验证码、完全不同 API）**不要**硬套本方案，单独处理。

## 10. 安全

- `sites.yaml` 可版本控制；`secrets/`、token、data 进 `.gitignore`。
- 日志只记 site-id、HTTP 状态、错误类别、分组数量/摘要字段；不记密码、Bearer、refresh。
- 仅请求配置中的 base URL + 固定 path。
- 代理 URL 可能含认证信息，不写入日志。

## 11. 可观测性（最小）

依赖 journal 即可，日志中出现：

- 成功：site、分组数、耗时、是否 content 变化
- 失败：site、错误类别、连续失败次数
- 可选：token 剩余有效秒数（非 token 本身）

运维判定：

- 连续失败或长时间无成功 → 看 journal + 对应实例
- 分组变化 → `groups_events.jsonl` 与 `groups_latest.json`

## 12. 测试（够用即可）

- 配置合并与非法 site-id / 非 HTTPS / 重复 data_dir 拒绝
- 凭据文件权限过宽时启动失败
- refresh 成功/失败后的登录兜底；超时不清 token
- content_hash 稳定；相同数据不写 events；增删改 diff 正确
- 假 HTTP：登录、401、5xx、坏 JSON
- 两站并行不串目录

## 13. 从现状迁移

保持现有 AIAPIBANK / PinAI 服务可用，分步替换：

1. **配置统一**：`sites.yaml` + `secrets/`，或规范 `sites/*.env`；脚本支持 `--site` / 保留 `--env-file`。
2. **认证**：实现 refresh；token 写入过期时间。
3. **历史**：latest + hash；events 仅变化；停止向旧 `groups_history.jsonl` 写全量（可保留文件只读归档）。
4. **systemd**：上模板 unit，逐站切换；确认两个周期后 disable 旧 unit；删除重复 `install_*.sh`。

## 14. 第一版实现清单

- [ ] 统一入口与 `--site` / `--once` / `--validate`（可选 `--env-file`）
- [ ] `sites.yaml` + `secrets/*.env`，或等价的 `sites/*.env`
- [ ] refresh 优先 + 密码登录兜底 + 原子 token
- [ ] 可配置 `poll_interval_seconds`（默认 300）
- [ ] `groups_latest.json` + `content_hash` + 变化型 `groups_events.jsonl`
- [ ] 失败分类与 last-known-good
- [ ] `sub2api-monitor@.service`
- [ ] 迁移 AIAPIBANK、PinAI 并验证
- [ ] 清理重复 service / 安装脚本

## 15. 与冗长草案的关系

| 保留 | 推迟/删除 |
|---|---|
| 一程序 + 模板 unit | SQLite WAL、Repository 双实现 |
| 配置/密钥/数据按站隔离 | poll_runs、heartbeat 文件 |
| refresh 状态机 | 独立 metrics/alerting 模块 |
| 变化型历史 | 10+ 文件包拆分 |
| 失败不覆盖 latest | 完整多文档架构/数据流图作为实施前置 |

实现以本文为准；复杂度只在真实需求出现时增加。
