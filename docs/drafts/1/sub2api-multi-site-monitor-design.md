# Sub2API 多站点分组监控设计与实施方案

## 1. 文档目的

本文定义如何把当前的 AIAPIBANK/PinAI 单站点监控扩展成可持续增加网站的
Sub2API 多站点监控系统。目标是让新增站点只需要完成站点识别、添加配置和密钥、
启动独立服务，不再复制 Python 脚本或修改公共代码。

本文覆盖：

- 如何确认一个网站是否属于 Sub2API 或兼容部署。
- 网站 URL、接口路径、用户名和密码如何保存。
- access token 和 refresh token 如何刷新、轮换和持久化。
- 分组信息如何高频轮询、保存当前状态和记录变化历史。
- 多个站点如何隔离运行、失败重试和统一运维。
- 当前项目如何分阶段迁移到目标架构。

本文不保存任何真实用户名、密码、access token、refresh token 或 API Key。

## 2. 当前状态

当前项目使用 `aiapibank_monitor.py`，通过每个站点独立的 env 文件运行：

- AIAPIBANK 使用 `config.env` 和 `aiapibank-monitor.service`。
- PinAI 使用 `pinaic.env` 和 `pinaic-monitor.service`。
- 两个站点都使用 `/api/v1/auth/login` 和
  `/api/v1/groups/available`。
- token 和分组数据已经按目录隔离。

这种方式可以支持少量站点，但每增加一个站点都需要创建新的 service 文件和安装
脚本。站点较多后会出现以下问题：

- 配置散落，无法快速查看当前监控了哪些站点。
- service 文件重复，仅站点名和配置路径不同。
- refresh token 已保存，但当前程序没有优先使用 refresh 接口。
- 每次成功轮询都追加完整 JSON，轮询频率提高后历史文件增长很快。
- 不便于查询某个分组何时新增、删除、改名或调整倍率。

## 3. 适用范围与站点识别

该方案只直接适用于 AIAPIBANK、PinAI 以及经过确认的 Sub2API 兼容网站。不能仅凭
页面外观或 URL 推断站点类型。

新增站点时按以下顺序验证：

1. 打开登录页，检查是否存在 Vue/Vite 构建资源、`window.__APP_CONFIG__`、
   Sub2API 版本号或相同的前端模块结构。
2. 在浏览器网络面板确认登录请求，而不是直接假设接口路径。
3. 确认登录请求通常为：

   ```http
   POST /api/v1/auth/login
   Content-Type: application/json

   {"email":"user@example.com","password":"secret"}
   ```

4. 确认成功响应包含 `data.access_token`，并记录是否包含
   `data.refresh_token`、`data.expires_in`。
5. 确认 refresh 接口。标准 Sub2API 前端通常调用：

   ```http
   POST /api/v1/auth/refresh
   Content-Type: application/json

   {"refresh_token":"rt_..."}
   ```

6. 使用 access token 验证只读分组接口：

   ```http
   GET /api/v1/groups/available
   Authorization: Bearer <access_token>
   Connection: close
   ```

7. 验证响应中的 `data` 是数组，并确认分组 ID、名称、倍率、状态等字段。
8. 记录与默认契约不同的路径、用户名字段、响应结构或认证要求。

如果页面使用不同认证方式、没有上述接口、需要验证码/设备认证，或响应结构完全
不同，应停止套用 Sub2API 方案，为该站点单独设计适配器。

## 4. 总体架构

目标架构分成四层：

```text
sites.yaml                    非敏感站点注册表
secrets/<site-id>.env         用户名和密码，权限 0600
data/<site-id>/token.json     运行时 token，权限 0600
data/<site-id>/...            latest、历史或 SQLite 数据
```

每个站点由独立的 systemd 模板实例运行：

```text
sub2api-monitor@aiapibank.service
sub2api-monitor@pinaic.service
sub2api-monitor@example.service
```

公共 Python 程序接收 `--site <site-id>`，从 `sites.yaml` 读取该站的非敏感配置，
然后只加载该站对应的密钥和运行时状态。

这样做有三个关键收益：

- 一个站点超时、限流或认证失败不会阻塞其他站点。
- 新增站点不需要复制程序和 systemd unit。
- 配置、密钥、token 和业务数据有清晰的安全边界。

## 5. 配置文件设计

### 5.1 为什么选择 YAML

站点注册表建议使用 YAML，而不是 JSON：

- 运维人员需要手工编辑，YAML 可写注释。
- 路径、轮询间隔和开关较多，YAML 可读性更好。
- 新增站点时更容易复制一个配置块并修改。

YAML 只保存非敏感信息。用户名、密码和 token 不应写入 YAML。

Python 侧应使用 `yaml.safe_load` 解析，禁止使用可构造任意对象的加载方式。加载后应
使用明确的数据模型校验字段，例如 dataclass、Pydantic 或手工类型校验。

### 5.2 `sites.yaml` 示例

建议路径：`/root/projects/zhongzhuan/sites.yaml`。

```yaml
version: 1

defaults:
  login_path: /api/v1/auth/login
  refresh_path: /api/v1/auth/refresh
  groups_path: /api/v1/groups/available
  username_field: email
  poll_interval_seconds: 300
  connect_timeout_seconds: 10
  read_timeout_seconds: 30
  refresh_margin_seconds: 600
  history_heartbeat_seconds: 3600
  request_jitter_seconds: 10

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
    poll_interval_seconds: 60
    enabled: true
```

站点 ID 只能使用小写字母、数字和连字符，例如 `pinaic`、`site-2`。程序必须拒绝
包含 `/`、`..`、空格或其他可能造成路径穿越的 ID。

所有相对路径都必须相对于 `sites.yaml` 所在目录解析，不能依赖 systemd 的当前
目录碰巧正确。

### 5.3 凭据文件

建议目录：`/root/projects/zhongzhuan/secrets/`。

每个站点一个文件，例如 `secrets/pinaic.env`：

```dotenv
MONITOR_USERNAME=user@example.com
MONITOR_PASSWORD=replace_me
```

要求：

- `secrets/` 和其中所有文件归运行服务的用户所有。
- 目录权限建议 `0700`，文件权限必须 `0600`。
- `secrets/` 必须加入 `.gitignore`。
- service unit 中只出现站点 ID，不能出现用户名或密码。
- 日志可以记录站点 ID，但默认不记录完整用户名。
- 修改凭据后只重启对应站点实例。

不建议将所有站点的密码放在同一个 JSON/YAML 文件。单文件泄露会暴露全部账号，
而且无法做到最小权限和按站点轮换。

### 5.4 配置校验

启动前必须校验：

- `base_url` 只允许 `https://`，开发环境例外必须显式开启。
- API path 必须以 `/` 开头，且不能是完整的外部 URL。
- `poll_interval_seconds` 不低于项目设定的安全下限。
- `credentials_file` 存在且权限没有向 group/other 开放。
- `data_dir` 不与其他站点重复。
- 同一站点的登录、refresh、分组请求使用相同 User-Agent。
- 可选代理地址不得被记录到日志；代理中可能包含认证信息。

提供一个只读命令验证所有配置：

```bash
python3 sub2api_monitor.py --registry sites.yaml --validate
```

## 6. Token 生命周期

### 6.1 不要高频刷新 refresh token

分组轮询可以很频繁，但 token 刷新不应与每次轮询绑定。正确行为是根据 access
token 的过期时间按需刷新：

```text
开始轮询
  |
  +-- 没有 access token ----------> 用户名密码登录
  |
  +-- access token 即将过期 ------> refresh token 刷新
  |
  +-- access token 仍有效 --------> 请求分组
                                      |
                                      +-- 200 -> 保存分组
                                      +-- 401/403 -> 刷新一次并重试一次
```

推荐在 access token 到期前 5–10 分钟刷新，具体由
`refresh_margin_seconds` 控制。JWT payload 只能用于读取 `exp`，不能当作签名验证。

### 6.2 Token 文件结构

每个站点独立保存，例如 `data/pinaic/token.json`：

```json
{
  "access_token": "<redacted>",
  "refresh_token": "<redacted>",
  "access_expires_at": 1784450000,
  "saved_at": "2026-07-19T02:00:00Z"
}
```

要求：

- 文件权限必须为 `0600`。
- 使用“同目录临时文件 + `fsync` + 原子替换”更新。
- refresh 响应返回新 refresh token 时，必须与 access token 一起原子保存。
- refresh 响应没有新 refresh token 时，保留现有值。
- token 文件损坏时记录脱敏错误，重新登录，不打印文件内容。
- token 不进入 YAML、SQLite、分组 JSON、journal 或异常响应正文。

### 6.3 Refresh 失败处理

建议状态机：

1. access token 临近过期，调用 refresh 接口。
2. refresh 成功，保存新 token，继续分组请求。
3. refresh 返回 400/401/403，清除内存中的失效 token，使用用户名密码登录一次。
4. 登录成功后保存新 token。
5. 登录仍失败，结束本次轮询并进入退避，不能无限重试账号密码。
6. 网络超时或 5xx 视为传输故障，不应立刻判定 token 失效或清空 token。

为避免多个进程同时刷新同一个站点 token，应确保每个站点只运行一个服务实例，
或者为 token 文件增加进程锁。

## 7. 分组高频轮询

### 7.1 轮询频率

默认建议 300 秒。确实需要快速发现分组变化时，可设置为 30–60 秒，但要满足：

- 已确认站点允许该请求频率。
- 每次只调用只读分组接口。
- 不因轮询频繁而频繁登录或刷新 token。
- 多站点启动时增加 0–10 秒随机抖动，避免整点同时请求。
- 成功周期从本次轮询开始时间计算，防止请求耗时造成持续漂移。

### 7.2 连接管理

对于 5 分钟或更长的间隔，不复用长时间空闲的 keep-alive 连接。请求应使用：

```http
Connection: close
```

或者每个轮询周期创建并关闭新的 Session。登录、refresh 和分组请求必须保持同一
User-Agent，因为部分部署可能将 token 与客户端指纹绑定。

超时必须拆分为连接和读取超时，例如 `(10, 30)`。`ConnectTimeout`、
`ReadTimeout` 和 401/403 必须分开处理。

### 7.3 当前快照

每次成功轮询都原子覆盖：

```text
data/<site-id>/groups_latest.json
```

内容包含：

```json
{
  "site_id": "pinaic",
  "fetched_at": "2026-07-19T02:00:00Z",
  "count": 7,
  "content_hash": "sha256:...",
  "groups": []
}
```

`content_hash` 应基于规范化后的 `groups` 计算：

- 按稳定的 group ID 排序。
- JSON key 排序。
- 使用固定分隔符。
- 不包含 `fetched_at` 等每次都会变化的字段。

### 7.4 变化型历史

不要每 30 秒或 60 秒无条件追加完整响应。建议：

1. 读取上一份 latest 的 `content_hash`。
2. 新旧 hash 相同，只更新 latest。
3. 新旧 hash 不同，计算新增、删除和修改的分组。
4. 把一次变化事件追加到 JSONL 或 SQLite。
5. 即使没有变化，也可以每 15–60 分钟记录一条轻量 heartbeat。

JSONL 变化事件示例：

```json
{"site_id":"pinaic","observed_at":"2026-07-19T02:00:00Z","event":"groups_changed","added":[83],"removed":[],"modified":[45],"content_hash":"sha256:..."}
```

这样轮询 60 次但没有变化时，不会保存 60 份重复的完整分组数据。

## 8. JSONL 与 SQLite 的选择

### 8.1 继续使用 JSON/JSONL 的条件

满足以下情况时，JSON latest + 变化型 JSONL 足够：

- 站点数量较少，例如 1–10 个。
- 主要需求是查看当前分组和按时间顺序审计变化。
- 不需要复杂查询和跨站点统计。
- 已配置日志轮转或保留策略。

优点是实现简单、容易人工检查、恢复时不依赖数据库。

### 8.2 使用 SQLite WAL 的条件

出现以下需求时迁移到 SQLite：

- 站点较多或轮询间隔很短。
- 需要查询“某个倍率何时变化”“哪些站点新增了某个分组”。
- 需要生成跨站点报表或告警。
- JSONL 文件增长和扫描成本已经明显。

建议数据库：`data/monitor.db`，启用：

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
```

建议表结构：

```sql
CREATE TABLE sites (
    site_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    base_url TEXT NOT NULL
);

CREATE TABLE group_current (
    site_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (site_id, group_id),
    FOREIGN KEY (site_id) REFERENCES sites(site_id)
);

CREATE TABLE group_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    group_id TEXT,
    event_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload_json TEXT,
    observed_at TEXT NOT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(site_id)
);

CREATE INDEX idx_group_events_site_time
ON group_events(site_id, observed_at);

CREATE TABLE poll_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    group_count INTEGER,
    error_class TEXT,
    duration_ms INTEGER NOT NULL,
    FOREIGN KEY (site_id) REFERENCES sites(site_id)
);
```

密码和 token 仍不能进入 SQLite。数据库用于分组状态和运行记录，不承担密钥管理。

## 9. 重试与失败分类

不同错误必须采用不同策略：

| 错误 | 处理方式 |
|---|---|
| 401/403 | refresh 一次，失败后登录一次，再失败则等待下一周期 |
| ConnectTimeout | 指数退避后重试，保留 token |
| ReadTimeout | 指数退避并使用新连接，保留 token |
| HTTP 429 | 遵守 `Retry-After`，扩大该站点退避时间 |
| HTTP 5xx | 有界指数退避，不能高速循环 |
| JSON 结构错误 | 保存脱敏诊断信息，标记契约变化，不覆盖 last-known-good |
| 凭据错误 | 降低重试频率并产生运维告警 |
| 地区限制 | 报告实际出口 IP/地区，等待路由或代理修复 |

推荐退避为 10、30、60、120、300 秒，上限不超过正常轮询周期或站点配置的最大值。
恢复成功后立即清零失败计数。

失败轮询不能覆盖最后一份成功的 `groups_latest.json`。应在 `poll_runs` 或独立状态文件
中记录最近错误和最后成功时间。

## 10. systemd 模板

建议创建 `/etc/systemd/system/sub2api-monitor@.service`：

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

先使用 `systemd-analyze verify` 校验 unit，再逐站点启用：

```bash
systemctl daemon-reload
systemctl enable --now sub2api-monitor@aiapibank.service
systemctl enable --now sub2api-monitor@pinaic.service
```

查看状态：

```bash
systemctl status sub2api-monitor@pinaic.service
journalctl -u sub2api-monitor@pinaic.service -f
```

不要让模板 unit 通过 `EnvironmentFile` 直接展开不同路径。由经过校验的 `--site`
参数选择配置，可以避免 systemd 实例名被用于任意文件读取。

## 11. 新增网站标准流程

### 11.1 收集输入

新增一个网站至少需要：

- 登录页 URL。
- 用户名和密码的安全文件路径，或授权创建新的 mode-600 密钥文件。
- 期望的轮询频率；未指定时使用 300 秒。
- 是否需要代理，以及代理所在地区。

### 11.2 站点接入

1. 使用认证检查 skill 判断是否为 Sub2API 兼容部署。
2. 记录登录、refresh、分组接口和响应结构。
3. 给站点分配稳定的 `site-id`。
4. 在 `sites.yaml` 添加非敏感配置。
5. 创建 `secrets/<site-id>.env` 并设置权限。
6. 执行配置校验。
7. 使用 `--once` 完成一次真实登录和分组抓取。
8. 检查 token、latest 和历史文件权限与目录隔离。
9. 启用对应 systemd 模板实例。
10. 观察至少两个轮询周期，并控制一次 401 或超时测试。

### 11.3 接入完成标准

- 站点出口地区符合对方策略。
- 登录和 refresh 均经过真实验证。
- 分组接口返回完整数组，不只是数量。
- 每个分组的 ID、名称、倍率、状态可见。
- token 文件为 `0600`，且日志没有 token 或密码。
- latest 正常更新，未变化时不会重复写完整历史。
- 401 会刷新/重登，网络超时不会误删 token。
- systemd 实例为 enabled、active，并能优雅停止。

## 12. 安全要求

- `sites.yaml` 可以进入版本控制，但只能包含非敏感信息。
- `secrets/`、token、数据库、快照和历史必须被 `.gitignore` 排除。
- 原始账号文档如果包含密码，权限也必须为 `0600`。
- 日志不得输出密码、Bearer token、refresh token、Cookie 或完整敏感响应正文。
- 登录失败只记录站点 ID、HTTP 状态和错误类别。
- 对外请求仅访问配置中明确允许的站点 base URL 和固定 API path。
- 不使用免费公共代理传输账号密码。
- 备份 token 或数据库时，备份介质需要与源文件相同或更严格的访问控制。
- 账号离线或站点移除后，应停止服务、删除 token，并按保留策略归档业务历史。

## 13. 可观测性和告警

每个站点至少记录以下非敏感指标：

- 最近一次成功时间。
- 最近一次失败时间和错误类别。
- 连续失败次数。
- 当前分组数量。
- 本次请求耗时。
- 当前内容 hash。
- token 距离过期的秒数，不记录 token 本身。

建议在以下情况告警：

- 连续 3 次或超过 10 分钟没有成功轮询。
- 登录或 refresh 持续失败。
- 响应结构变化。
- 分组被删除、状态变为 inactive 或倍率改变。
- 出口国家发生变化并触发地区限制。
- token、凭据或数据文件权限不符合要求。

第一阶段可以使用 journal 和状态文件；站点增多后再接入 Prometheus、邮件、企业微信或
其他现有告警渠道。

## 14. 测试方案

### 14.1 单元测试

- YAML 默认值与站点覆盖合并正确。
- 非法 site ID、HTTP URL、重复 data dir 被拒绝。
- 凭据文件权限不安全时启动失败。
- 登录响应和 refresh 响应解析正确。
- refresh token 轮换时原子保存。
- 401 只 refresh 一次并只重试一次。
- timeout 不触发 token 清理。
- 分组规范化 hash 稳定。
- 相同数据不追加变化历史。
- 新增、删除、修改分组的 diff 正确。

### 14.2 集成测试

- 使用假 HTTP 服务覆盖登录、refresh、401、429、5xx、超时和错误 JSON。
- 至少运行两个真实轮询周期。
- 模拟进程在 token 文件替换前后终止，确认不会留下半个 JSON 文件。
- 两个站点实例并行运行，确认配置、token 和数据互不串用。

### 14.3 线上验证

- 新站点先执行 `--once`，成功后才启用常驻服务。
- 观察 journal 中没有密钥和完整认证响应。
- 验证停止信号能在短时间内让进程退出。
- 验证最后成功快照不会被失败轮询覆盖。

## 15. 数据保留与清理

建议默认策略：

- `groups_latest.json` 永久保留一份当前状态。
- 分组变化事件保留 180–365 天，视审计需要调整。
- 成功心跳保留 30–90 天。
- 失败运行记录保留 90 天。
- journal 使用系统 logrotate/journald 上限控制。

JSONL 可以按月切分并压缩。SQLite 应定期删除超期记录并在维护窗口执行
`PRAGMA optimize`；不要在每次轮询后运行 `VACUUM`。

## 16. 从当前项目迁移

迁移分四个阶段，任何阶段都应保持现有 AIAPIBANK 和 PinAI 服务可用。

### 阶段一：抽象配置

1. 新增 `sites.yaml.example` 和实际 `sites.yaml`。
2. 新增 `secrets/`，把 `config.env`、`pinaic.env` 中的用户名密码迁移为按站点文件。
3. 给监控脚本增加 `--registry`、`--site`、`--validate` 参数。
4. 保留现有 `--env-file` 作为临时兼容入口。

### 阶段二：完善认证

1. 实现 `/api/v1/auth/refresh`。
2. token 状态增加明确的过期时间和原子轮换。
3. 统一 401、refresh 失败、密码登录失败和传输错误状态机。
4. 增加对应单元和集成测试。

### 阶段三：优化历史

1. 为 groups 生成规范化 hash。
2. latest 保持每次成功更新。
3. history 改为只记录变化和低频 heartbeat。
4. 根据实际站点数量决定是否立即引入 SQLite。

### 阶段四：切换 systemd

1. 创建并验证 `sub2api-monitor@.service`。
2. 先并行试运行一个模板实例，避免与旧服务同时写同一数据目录。
3. 停止旧站点服务，再启动对应模板实例。
4. 验证两个周期后 disable 旧 service。
5. 所有站点迁移完成后删除重复安装脚本和旧 unit。

## 17. 推荐的第一版实现范围

第一版不要一次引入所有复杂度，建议实现以下最小闭环：

1. `sites.yaml` + 每站独立 env 密钥。
2. 一个公共监控程序和一个 systemd 模板。
3. refresh 优先、密码登录兜底。
4. 60–300 秒可配置轮询和 0–10 秒抖动。
5. 原子 latest + 变化型 JSONL + 每小时 heartbeat。
6. 完整的失败分类和 last-known-good 保护。
7. 配置校验、权限校验和两站并行测试。

当站点数量、历史量或查询需求证明 JSONL 不够用时，再迁移到 SQLite WAL。这样既能
尽快稳定扩站，也避免在需求尚未出现时引入数据库维护成本。

