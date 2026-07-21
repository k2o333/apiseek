# New-API 监控方案评审与精简建议

**评审日期：** 2026-07-21  
**评审范围：** 本目录 6 份方案文档、仓库现有 `sub2api_monitor.py` / timer / 安装脚本 / 测试、`docs/websites/botcf.md` 与 `torchai.md`，以及 New-API 上游公开实现。  
**结论：** “独立协议入口 + 每站 timer/oneshot + 成功快照”这个主方向正确，但当前稿还不适合直接按检查单开工。存在 2 个上线阻断问题、若干契约和安全缺口，也机械继承了 Sub2API 路径中并不适合新入口的复杂度。

> **状态（2026-07-21）：** 合理意见已写入修订方案。  
> **以** [architecture.md](./architecture.md)、[data-model.md](./data-model.md)、[review-adoption.md](./review-adoption.md) **为准**。  
> 本文保留为评审原文；下列「只给建议、不修改原方案」已过时。

本文原为建议稿。严重度含义：

- **P0：** 开发前必须处理，否则会泄密或记录错误历史。
- **P1：** 首版上线前处理，否则故障时行为不可预测或缺少关键能力。
- **P2：** 精简、可维护性和后续演进建议。

---

## 1. 值得保留的决策

以下不是过度工程化，应保留：

1. **New-API 与 Sub2API 认证代码分开。** Bearer refresh 与 session 登录的状态机不同，硬塞入现有 `AuthGroupClient` 会明显增加条件分支和回归面。
2. **生产只用 systemd timer + oneshot。** 两站是分钟级轮询，没有常驻进程或多线程 Supervisor 的需求。
3. **失败不覆盖最后成功快照。** 这是监控数据最重要的不变量。
4. **每站独立凭据、状态目录和 flock。** 认证状态不能跨站共享。
5. **登录态持久化。** 每 4～5 分钟密码登录一次会制造约 300 次/天/站的登录流量，容易触发限流或验证码；复用 session 是必要复杂度。
6. **原子替换 latest、事件先于 latest。** 思路正确，但去重算法需要修正，见 P0-2。
7. **不做浏览器自动化和 Turnstile 破解。** 这是正确的范围边界。

---

## 2. 阻断问题

### P0-1：明文凭据已经进入 Git，方案中的“以后不提交”不能闭合现状

**证据**

- `docs/websites/botcf.md`、`docs/websites/torchai.md` 是 Git 已跟踪文件，并包含真实用户名和明文密码。
- 它们已经进入提交 `fa86e48`；`.gitignore` 对已经跟踪的文档无效。
- `architecture.md` §7 和 `data-model.md` §11 只要求未来 env 不入库，没有处置已经泄露的凭据和历史。

**影响**

只要仓库被推送、备份或分享，账号就应视为已泄露。后续把文档改成省略号也不能撤回 Git 历史里的内容。

**建议**

1. 立即轮换两站密码，并使现有 session 全部失效。
2. 将两份站点手册中的账号改成占位符；真实账号只放 `sites/*.env` 或系统凭据设施。
3. 如果仓库曾离开本机，使用 `git filter-repo` 清理历史后强制更新远端，并通知所有持有旧 clone 的人；如果从未离开本机，也应至少重写包含秘密的本地提交。
4. 在上线检查单加入秘密扫描，例如 `gitleaks`，并检查 Git 历史而不只是工作树。
5. 不要在 `auth_state.json` 中保存 `username`，它对恢复会话没有作用，只增加身份信息暴露面。

这不是 New-API 代码实现的一部分，但应作为开工前安全阻断项。

### P0-2：全文件按 hash 去重会吞掉合法的“恢复旧状态”事件

`data-model.md` §7 要求“events 中尚无相同 hash 时追加”，这沿用了现有实现 [`events_has_hash`](../../../sub2api_monitor.py#L920-L937)。该算法把“崩溃后的重复提交”和“未来再次出现相同内容”混为一谈。

状态序列如下：

```text
A（初始，hash=A）
  -> B（记录 hash=B）
  -> A（hash=A 已经在历史中，于是跳过事件）
```

最终 latest 会回到 A，但 events 完全缺少 `B -> A`，历史不再可信。

**正确且更简单的恢复规则**

只比较 events 的**最后一条完整记录**，不要扫描整个文件：

```text
if latest.hash == new.hash:
    只刷新 latest.fetched_at
else:
    if last_event.after_hash != new.hash:
        append event + fsync
    atomic replace latest
```

这样同时满足：

- 崩溃发生在 append event 后、replace latest 前：重跑时尾事件已经是新 hash，不重复 append，随后补写 latest。
- 合法 `A -> B -> A`：尾事件是 B，与新 A 不同，因此会记录恢复事件。

**必须增加的测试**

- `A -> B -> A` 产生 3 条事件。
- 模拟 event 已写、latest 未写，重跑不重复 event。
- 文件尾是半行 JSON 时，恢复策略明确：先截断到最后一个换行或隔离损坏文件，不能直接在半行后续写。
- retention 删除旧事件后，尾事件去重仍成立。

现有 Sub2API 也有同一缺陷。最合理的处理是先给共享的存储逻辑加回归测试，再让两个后端使用修正后的语义；不要在 New-API 中复制该 bug。

---

## 3. 协议和认证契约不完整

### P1-1：“New-API / One-API 系通用客户端”的范围说得过大

当前设计只有 BotCF、TorchAI 两个样本，而且两者已经有差异。上游协议也在演进：

- New-API `v0.4.6.1` 的 `UserAuth` 明确要求 `New-Api-User`。
- 当前上游 `main` 已改为 `Authorization` 中的 dashboard token / PAT，并移除了该头校验。
- 当前上游 `GetUserGroups` 还可能返回特殊倍率字符串 `"自动"`，并非所有 ratio 都可转成 float。

参考上游源码：[`v0.4.6.1 middleware/auth.go`](https://github.com/QuantumNous/new-api/blob/v0.4.6.1/middleware/auth.go)、[`main middleware/auth.go`](https://github.com/QuantumNous/new-api/blob/main/middleware/auth.go)、[`controller/group.go`](https://github.com/QuantumNous/new-api/blob/main/controller/group.go)。

**建议**

- 首版明确命名为“BotCF/TorchAI 已探活的 legacy session 协议适配器”，不要承诺覆盖 New-API 全系。
- 不做版本探测、能力协商或通用 provider 框架；第三个站接入前按 `site-notes.md` 的探活表验证，再由证据决定是否加一个能力位。
- 首版若只支持数值 ratio，应在范围中明确 `"自动"` 为不支持的 contract，而不是声称通用；若确有站点返回它，再扩展为 `rate_multiplier: null` + `rate_label: "自动"`。

这比预先设计一套多版本插件框架更可靠，也更简洁。

### P1-2：HTTP 2xx 与业务成功没有形成确定状态机

`architecture.md` §4.2 的“若响应含 success 字段则须 true”仍有歧义。旧版 New-API 常用 HTTP 200 表示登录失败、验证码失败或数据库错误。因此不能先按 HTTP 成功就保存 cookie，也不能把所有 `success=false` 都归为 contract。

建议对 login 和 groups 分别写死成功契约：

```text
login success:
  HTTP 200
  JSON object
  payload.success is true
  session cookie 存在
  data.id 是正整数（需要 user header 的站点必需）

groups success:
  HTTP 200
  JSON object
  payload.success is true
  payload.data 是非空 object（两个首批站均保证至少有默认可用组）
```

失败按“下一步动作”分类，避免自由文本 kind 越长越多：

| 条件 | 动作 |
|---|---|
| 401，或已知未登录 / user header 不匹配业务码 | 最多清会话并重登一次 |
| login `success=false` 且验证码文案 | `captcha`，本回合停止 |
| 429 | `rate_limit`，解析并限制 Retry-After |
| 408 / 5xx / timeout / connection error | transient，至多短重试一次 |
| 403 HTML / CF 标志 | `region`，不重登 |
| 2xx 但 envelope、字段或 ratio 不合法 | `contract`，保留 latest |
| 其他 4xx | permanent HTTP error，不重试 |

不要把响应正文原样写进 journal。最多记录 status、content-type、业务 code 和经过严格脱敏、去控制字符、限长后的 message。

### P1-3：Turnstile 配置项的语义不可用

方案把 `TURNSTILE_VALUE` 当成可长期放在 env 中并在下轮复用的值。Cloudflare Turnstile token 只有 5 分钟有效期且只能验证一次，不能作为静态配置长期使用。参考 [Cloudflare 服务端校验文档](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)。

建议首版只支持：

- 登录允许空 `turnstile` 的站点；请求参数固定为空。
- 一旦站点强制验证码，明确标记 `captcha` 并停止自动登录；保留仍有效的旧 session。
- 不提供看似可用但实际会过期/重放失败的 `TURNSTILE_VALUE` env。

未来若真有外部 token 获取流程，应设计成每次登录即时调用的 provider，而不是静态字符串；在有真实需求前不要实现。

### P1-4：认证状态有两份 cookie 真相，恢复和轮换行为未定义

`data-model.md` 同时保存 `cookies` map 和 `cookie_jar` list，但没有规定冲突时谁优先。这会制造两份秘密、两套序列化和额外测试。

建议只保存一个最小结构：

```json
{
  "schema_version": 1,
  "saved_at": "2026-07-21T00:00:00Z",
  "user_id": 12345,
  "session": {
    "value": "<redacted>",
    "domain": "torchai.ai",
    "path": "/",
    "expires": null
  }
}
```

恢复时只允许 cookie 名 `session`，domain 必须与 `MONITOR_BASE_URL` host 匹配，path 固定或校验为 `/`，拒绝任意跨域 cookie。还要补足：

- 读取已有文件时校验 owner/mode，不只是写出时 chmod 0600。
- 登录响应设置新 session 后立即原子保存。
- groups 响应若轮换 session cookie，也要更新 auth state；否则每个新进程会恢复旧 cookie。
- auth state 损坏时重登，但日志不能输出内容。
- `saved_at` 表示凭据最后变化时间；成功 GET 不要无意义 touch 文件。

`NEW_API_USER_HEADER` 这个能力位有站点证据支撑，可以保留；但应命名为 `REQUIRE_NEW_API_USER_HEADER`，并在启用时要求登录响应每次都提供合法正整数 id。

### P1-5：登录重定向可能把密码带到非预期地址

`requests` 默认跟随重定向。登录 POST 遇到 307/308 时可能保留请求体；若跳到其他 origin，会带来凭据泄露风险。方案没有说明 redirect 策略。

建议所有 API 请求 `allow_redirects=False`。站点换域应显式更新 `MONITOR_BASE_URL` 并重新探活，而不是自动跟随。同时把 base URL 校验收紧为纯 origin：

- scheme 必须 `https`；
- 必须有 hostname；
- 禁止 userinfo、query、fragment；
- path 只能为空或 `/`。

---

## 4. 数据模型需要补齐的语义

### P1-6：`status="active"` 是虚构数据，不是规范化

上游没有返回 status，却固定写 `active` 并纳入 hash。它让跨站形状看似一致，但语义是假的：无法区分“上游明确 active”和“上游根本没提供”。

首版应删掉 `status`，消费方把它视为可选字段；如果必须保持固定 schema，使用 `null` / `"unknown"`，且不要纳入内容 hash。不要为了字段齐全制造事实。

### P1-7：ratio 数值校验缺少边界

直接 `float(value)` 会接受一些不应进入 JSON 快照的值，例如 `NaN`、`Infinity`；Python 中 `bool` 也是数字子类。建议数值站点执行：

```text
拒绝 bool
接受 int / float / 可解析十进制字符串
转换后必须 math.isfinite(value)
必须 value >= 0
```

整包失败比跳过单条安全，因为跳过会制造假的 removed 事件。`0` 是合法倍率，不能按 falsy 当缺失。

### P1-8：events 只保存 id，无法回答“倍率从多少改成多少”

目前事件中的 `modified: ["Codex-Plus"]` 只说明某组变过。latest 下一次变化后，历史里既没有 before，也没有 after，无法审计倍率或描述的实际改动。

需要先确定 events 的产品语义：

- 如果只是“触发信号”，保留 id 即可，但文档必须明确它不是审计历史，消费者收到后立即读取 latest。
- 如果要保留变化历史，建议事件直接保存小体量 diff：

```json
{
  "event": "groups_changed",
  "before_hash": "sha256:...",
  "after_hash": "sha256:...",
  "added": [{"id": "new", "rate_multiplier": 0.2}],
  "removed": [{"id": "old", "rate_multiplier": 0.3}],
  "modified": [
    {
      "id": "Codex-Plus",
      "before": {"rate_multiplier": 0.08, "description": "..."},
      "after": {"rate_multiplier": 0.1, "description": "..."}
    }
  ]
}
```

两站只有几十个分组且事件只在变化时写，保存 before/after 的空间成本很低。推荐采用第二种，使 events 名副其实。

### P1-9：跨后端读取不能用 id 类型猜 backend

`data-model.md` §9 建议通过“id 是非数字字符串”猜 New-API。这会误判：New-API 分组名可以是 `"123"`，Sub2API 也可能返回非数字字符串 id。

建议所有新快照和事件固定包含：

```json
{
  "schema_version": 1,
  "backend": "newapi",
  "site_id": "botcf"
}
```

消费规则只看 `backend`；历史记录缺失 backend 时才按“legacy Sub2API”处理，绝不检查 id 形状。读取旧 latest 时还应验证 `site_id` / `backend`，不匹配就硬失败，避免错误 DATA_DIR 下产生跨后端 diff。

### P2-1：180 天 retention 在首版没有收益，反而增加写路径复杂度

events 只在内容变化时写，两站的数据量很小。每次成功轮询都扫描、解析并可能重写 JSONL，是机械复制现有实现，不是当前需求。

建议 v1 删除 `EVENTS_RETENTION_DAYS` 和 prune：

- 先观察实际增长；
- 真有体量问题时，用独立维护命令按文件大小/日期处理；
- 不要把 retention 放在每次采集的关键成功路径上。

若坚持 retention，必须定义损坏行、原子重写、尾事件保留、并发锁和断电恢复行为，测试成本明显高于收益。

### P2-2：initial 事件不应继续“二选一”

可执行设计不应把 initial 的 `added` 留给实现阶段任选。建议：若 events 是审计历史，initial 的 `added` 放完整初始组；若只是信号，则 initial 三个 diff 均为空。本文建议前者，并用测试锁死。

---

## 5. 调度、重试和运维缺口

### P1-10：240 秒 unit 超时与 3 次请求重试的预算数学不成立

方案默认 connect/read 为 15/60 秒。一轮可能是：

```text
旧 session GET 失败 + login + 再 GET
≈ 3 × (15 + 60) = 225 秒
```

这还没有包括 DNS、JSON/磁盘、两次退避，更不可能在 240 秒内执行 3 轮。当前“只 cap sleep 的剩余预算”也不能约束正在进行的 HTTP 请求；现有测试只证明 sleep 不超预算，没有证明整个 `run_once` 不超预算。

**精简建议**

- tiny JSON API 默认 timeout 改为 connect 5 秒、read 20 秒。
- transient 最多重试 1 次；timer 约 4～5 分钟后本来就会再跑，3 次进程内重试收益有限。
- auth 恢复最多 1 次，明确全回合最大 HTTP 调用数。
- 使用 monotonic deadline；每次发请求前检查剩余时间，并收紧本次 timeout，而不只是 cap backoff。
- `Retry-After` 同时支持秒数和 HTTP-date，且受全局 deadline 限制；超长值留给下一次 timer。
- 保留 `TimeoutStartSec=240` 作为最终保险，但应用自己的目标预算可设 150～180 秒。

不要把 `MAX_ONCE_ATTEMPTS` 做成又一个 env。固定为 2，只有出现真实站点证据时再开放 CLI override。

### P1-11：同 DATA_DIR 的禁止规则目前不可执行，flock 也不是数据隔离兜底

两个不同站点或不同 backend 即使共享同一个 `monitor.lock`，也只会避免**同时**写；它们仍会轮流覆盖同一 `groups_latest.json`。因此“flock 兜底”不能保证隔离。

最简单的修复是减少配置：

- `DATA_DIR` 固定派生为仓库数据根下的 `<site_id>`；
- `AUTH_STATE_FILE` 固定为 `DATA_DIR/auth_state.json`，不允许单独配置；
- 强制 env 文件 stem、systemd `%i`、`MONITOR_SITE_ID` 三者一致；
- site id 全局唯一，并在安装时同时扫描 Sub2API/New-API env。

如果必须支持自定义 DATA_DIR，则 `--validate-all sites/*.env` 要对所有 resolve 后路径做唯一性检查。只检查 auth 文件“位于 DATA_DIR 内”不够。

另一个文档歧义是相对路径基准。现有 `load_config` 以 env 文件目录为基准，`data/botcf` 会变成 `sites/data/botcf`，与方案图中的仓库根 `data/botcf` 不一致。必须固定并测试路径基准，推荐直接从项目根派生。

### P1-12：systemd sandbox 与可配置路径互相矛盾

unit 只允许写 `/root/projects/zhongzhuan/data`，配置却允许任意 `DATA_DIR`。一个能通过 `--validate` 的配置可能在 service 中被 `ProtectSystem=strict` 拒绝，形成“手动成功、定时失败”。

两种选择只能选一个：

1. v1 固定 data root，与 `ReadWritePaths` 完全一致；这是推荐的精简方案。
2. 支持自定义路径，并为每站生成 systemd drop-in；复杂度高，当前没有必要。

另外建议 unit 加 `UMask=0077`。长期更合理的做法是用专用低权限用户，将配置放 `/etc`、状态放 `/var/lib`，而不是让外部 HTTP 客户端长期以 root 运行。若首版因现有 `/root` 布局暂不迁移，应把它写成已接受风险，而不是用几项 sandbox 参数造成“已经最小权限”的错觉。

### P1-13：监控只有采集，没有失败告警和变化消费闭环

当前输出是 latest、events 和 journal，但文档没有回答：

- 连续失败多久算监控失效？
- 谁检查 `fetched_at` 已经过旧？
- 新 event 由谁通知或消费？
- timer/service 失败是否进入现有告警系统？

如果目标只是本地采集，应明确称“采集器”。如果目标是生产监控，最小闭环至少需要一个 freshness 规则，例如：

```text
now - groups_latest.fetched_at > 2 × 最大预期间隔 + 单轮超时
=> stale 告警
```

以及明确 event 的消费者。首版不必新建告警平台，可接现有日志告警或外部巡检；但验收不能只看“timer 跑了两次”。

---

## 6. 明显的过度工程化和代码熵来源

### 6.1 建议从 v1 删除的配置

当前配置约 18 项，两个首批站真正变化的只有站点身份、origin、凭据和是否要求 user header。建议删除或固定：

| 当前项 | 建议 | 原因 |
|---|---|---|
| `MONITOR_BACKEND` | 删除 | 独立入口本身已经确定 backend |
| `MONITOR_LOGIN_PATH` | 固定 | 两站完全一致，没有变化证据 |
| `MONITOR_GROUPS_PATH` | 固定 | 同上 |
| `MONITOR_USERNAME_FIELD` | 固定 `username` | 同上 |
| `AUTH_STATE_FILE` | 从 DATA_DIR 派生 | 避免路径组合错误 |
| `EVENTS_RETENTION_DAYS` | 删除 | 事件稀疏，首版无需在线 prune |
| `MAX_ONCE_ATTEMPTS` | 固定 2 | timer 已承担长周期重试 |
| `TURNSTILE_VALUE` | 删除 | token 单次、短时有效，不是静态配置 |
| `POLL_INTERVAL_SECONDS` | 删除 | 生产由 timer 权威调度 |
| `MONITOR_SITE_NAME` | 暂删 | journal/site 文件都可用 site_id；有 UI 再加 |

建议 v1 env：

```bash
MONITOR_SITE_ID=botcf
MONITOR_BASE_URL=https://botcf.com
MONITOR_USERNAME=...
MONITOR_PASSWORD=...
REQUIRE_NEW_API_USER_HEADER=0
MONITOR_PROXY_URL=
CONNECT_TIMEOUT_SECONDS=5
READ_TIMEOUT_SECONDS=20
LOG_LEVEL=INFO
```

其中 timeout、proxy、log level 都有默认值，常规站只需前 5 项。

### 6.2 删除可选 `run_loop`

新入口没有历史兼容负担。增加前台 loop 会连带引入：

- `POLL_INTERVAL_SECONDS`；
- 周期双权威和安装脚本中点校验；
- signal/STOP 全局状态；
- success delay、failure counter、jitter 两套调度；
- 两种运行模式的测试矩阵。

建议脚本默认就是有界单次执行，只保留 `--validate`。连续调试可以用 `watch`、shell loop 或直接启动 timer。这样 timer 是唯一调度权威，安装脚本也不必解析 env 的 interval。

### 6.3 不要复制完整 Sub2API 类层级

New-API 首版不需要复刻 `TokenStore -> AuthGroupClient -> GroupMonitor -> SnapshotStore` 加前台 loop 的所有状态。推荐代码结构：

```text
newapi_monitor.py
  Config + load_config
  SessionStateStore
  NewApiSessionClient (login, fetch_groups)
  normalize_groups
  run_once / main

monitor_storage.py（最多一个共享模块）
  atomic JSON write
  append event + tail recovery
  snapshot hash/diff/persist
  InstanceLock
```

不要一开始拆成 `monitor_common/{io,lock,snapshot,once_retry}.py` 四个小模块，也不要建立 provider registry、基类、统一 CLI。只有 storage/lock 确实跨 backend 且已有相同 bug，值得形成一个小而具体的共享边界。

如果迁移现有 Sub2API 到共享 storage 风险过大，也可以先让 New-API 独立实现约 100～150 行存储逻辑，但必须用相同测试向量验证；不要无测试地复制粘贴现有代码。

### 6.4 安装脚本不要再复制一份 200 多行版本

New-API 没有 legacy simple 回滚路径，也不需要 interval 双源检查。新的安装脚本只需：

1. 校验 site id 和 env 权限；
2. 调用应用 `--validate`；
3. 安装、verify 两个 unit；
4. 检查同 id / DATA_DIR 冲突；
5. daemon-reload 并 enable timer；
6. 打印停用命令。

保持成一个短脚本即可。不要把 Sub2API 的 legacy 分支、POLL interval 解析和回滚逻辑复制过来，也不必为了两个 backend 立刻重写通用安装框架。

### 6.5 日志只写摘要

现有 Sub2API 每轮 INFO 打印每个 group。New-API 有 emoji、长描述和潜在控制字符，照搬会产生大量 journal 噪声并扩大不可信上游文本进入日志的表面。

每轮一条摘要足够：

```text
site=botcf result=changed count=34 added=1 removed=0 modified=2 hash=sha256:abc...
```

失败日志记录分类和状态，不记录密码、cookie、完整 URL query、代理凭据、原始响应或 group 描述。

---

## 7. 推荐的精简 v1

### 7.1 范围

- 只支持 BotCF、TorchAI 已探活的 session-cookie 协议。
- 固定 login/groups path 和 username 字段。
- ratio 只支持有限、非负数值；遇到 `"自动"` 等表达式明确 contract 失败。
- 无前台常驻 loop、无在线 retention、无浏览器验证码、无通用 provider 框架。

### 7.2 单轮流程

```text
load config
  -> 校验 env stem == site_id、origin、权限、固定 data path
  -> acquire flock
  -> restore allowlisted session + user_id
  -> 若状态不完整：login
  -> GET groups
       auth failure：清状态 -> login -> GET（仅一次）
       transient：在总 deadline 内短退避后重试（仅一次）
       captcha/region/contract：停止
  -> validate + normalize whole payload
  -> event tail 去重并 append/fsync（若变化）
  -> atomic replace latest
  -> summary log
  -> release/close
```

要避免“auth 恢复 × transient 重试”形成隐式乘法。实现和测试中写明每回合最大 login 次数、GET 次数和总 deadline。

### 7.3 最小快照

```json
{
  "schema_version": 1,
  "site_id": "botcf",
  "backend": "newapi",
  "fetched_at": "2026-07-21T00:00:00Z",
  "count": 1,
  "content_hash": "sha256:...",
  "groups": [
    {
      "id": "Codex-Plus",
      "name": "Codex-Plus",
      "rate_multiplier": 0.08,
      "description": "目前性价比之选 plus 号池"
    }
  ]
}
```

`id=name` 是当前上游信息限制下可接受的选择；名称改变表现为 remove + add，也已在原方案中明确接受。

### 7.4 建议文件规模和职责

这不是硬行数限制，而是防止抽象失控的信号：

- `newapi_monitor.py`：约 300～450 行，包含配置、HTTP 状态机和 CLI。
- 一个可选的 `monitor_storage.py`：约 150～250 行，包含原子 IO、事件和锁。
- 测试按行为组织，不为每个内部 helper 建一层 mock。

若实现迅速超过 700～800 行，通常说明复制了 loop、通用协议能力、重复序列化或过细错误层级，应先停下来删功能，而不是继续拆更多文件。

---

## 8. 建议补充的测试与验收闸门

### 必须的自动化测试

1. 登录 HTTP 200 但 `success=false`：不得保存 auth state。
2. 登录拿到 cookie 但缺合法 id，Torch 配置下失败。
3. cookie/domain/path round-trip；groups 响应轮换 cookie 后落盘。
4. 401 只重登一次，不形成 login loop。
5. 403 HTML 不触发重登；403 JSON 的 user-header mismatch 触发一次恢复。
6. 307/308 登录重定向不跟随。
7. ratio：`0`、数值字符串成功；bool、负数、NaN、Infinity、`"自动"` 整包失败。
8. 空 data 按两个首批站 contract 失败且不覆盖 latest。
9. 同内容不同字典顺序 hash 相同；description/ratio 变化 hash 不同。
10. `A -> B -> A` 三次事件完整；event 后崩溃可恢复且不重复。
11. 事件半行尾恢复。
12. events 中 modified 保存 before/after（若采纳审计语义）。
13. latest 的 backend/site_id 不匹配时硬失败。
14. env stem、site_id、DATA_DIR 冲突校验。
15. 总 deadline 包含 HTTP 调用，不只限制 sleep。
16. 对所有失败路径捕获日志，断言不含密码、cookie、用户名全文和代理凭据。
17. lock contention 退出可识别；进程被 SIGTERM 后下轮可重新获取锁。

### 现场验收应增加

- 首次登录后，第二次进程启动确实复用 session，没有每轮重登。
- 人工使 session 失效，确认只重登一次并恢复。
- 人工设置错误 user id，确认 Torch 恢复后更新 id。
- 连续制造失败，验证 timer 继续调度且 freshness 告警能发现 stale。
- 检查已安装 unit 的实际属性，而不只 verify 仓库模板。
- 检查 journal 全量而不是最后 80 行，做秘密模式扫描。
- 轮换凭据并演练停用 timer；New-API 不存在 Sub2API 等价回滚，文档应称“停用”而不是“回滚到旧实现”。

---

## 9. 推荐实施顺序

1. **先处理 P0-1：** 轮换并清理已提交凭据。
2. **冻结协议证据：** 对两站保存脱敏后的 login/groups contract 样本、状态码和 cookie/header 要求。
3. **先写数据测试：** 尤其是 `A -> B -> A`、半行事件、ratio 边界和 backend/site 校验。
4. **实现精简 once-only 客户端：** 不加 loop、retention、provider 框架。
5. **完成 deadline、redirect、日志脱敏测试。**
6. **落 unit 和短安装脚本：** 固定 data root，使 sandbox 与配置一致。
7. **两站手动 once：** 验证第二轮复用 session。
8. **timer 试点与 freshness 检查：** 至少观察成功、session 过期恢复、一次失败后的下轮恢复。
9. **最后再决定共享 storage：** 以修复 Sub2API 的全局 hash 去重 bug 为契机，小范围抽取；不要提前统一认证层。

---

## 10. 最终取舍摘要

| 主题 | 原方案 | 建议 |
|---|---|---|
| 进程边界 | New-API 独立入口 | **保留** |
| 调度 | timer + oneshot | **保留** |
| session 持久化 | 双 cookie 表示 | **保留能力，改成单一最小表示** |
| 前台 loop | 可选 | **删除** |
| interval env 校验 | 沿用 Sub2API | **随 loop 一起删除** |
| 重试 | 3 次、200s soft budget | **2 次、真实 monotonic deadline** |
| events 去重 | 历史任意相同 hash | **只比较尾事件** |
| events 内容 | 只有 id | **保存 before/after，或明确仅是信号** |
| retention | 每轮 prune | **v1 删除** |
| schema 判断 | 猜 id 类型 | **显式 schema_version/backend** |
| status | 固定 active | **删除或 unknown** |
| Turnstile | 静态 env token | **删除；强制时停止自动登录** |
| API 可配置性 | path/field 全开放 | **两站已知契约先固定** |
| 通用性声明 | New-API/One-API 系 | **收窄为两个已探活 legacy 站点** |
| 共用代码 | 后续四模块 common | **最多先共享一个 storage 模块** |
| 安装 | 新脚本或通用 backend | **短专用脚本，不复制 legacy 分支** |
| 运维闭环 | timer 成功两轮 | **增加 stale 和 event 消费定义** |

按以上收缩后，方案会更接近“两个已知站点的可靠采集器”，而不是尚无需求支撑的通用 New-API 监控框架；同时不会牺牲认证复用、崩溃恢复和数据隔离这些真正必要的工程质量。
