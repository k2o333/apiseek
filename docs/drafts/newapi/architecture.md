# BotCF / TorchAI 令牌分组采集 — 架构方案（修订）

**状态：** 可执行设计（已吸收 [design-review.md](./design-review.md)；**实现与安全闸门完成前不得宣称生产就绪**）  
**问题：** 需要周期采集 BotCF、TorchAI 上「当前用户可选令牌分组」；协议为 **legacy session cookie**，与现网 Sub2API JWT 路径不兼容。  
**定位：** **采集器（collector）**，不是通用 New-API 监控框架，也不是完整告警平台。  
**原则：** 简洁、可预测、可审计；调度在 systemd；业务在短生命周期进程；不扩大故障域；不复制 Sub2API 已知缺陷。

---

## 1. 结论

| 层级 | 选择 |
|------|------|
| **范围** | **仅** BotCF + TorchAI 已探活的 legacy session 协议（见 site-notes） |
| **生产形态** | 每站 **`newapi-monitor-once@%i.timer` + oneshot** |
| **入口** | 独立 `newapi_monitor.py`；**默认即有界单次执行**（无前台 `run_loop`） |
| **配置** | 最小 env（§4.1）；`DATA_DIR` **固定派生**，不可随意指向他处 |
| **数据** | 规范化 `groups_latest.json` + 可审计 `groups_events.jsonl` + 最小 `auth_state.json` |
| **与 Sub2API** | 并行、分入口、分 unit 名；**禁止**共享 DATA_DIR |
| **明确不做（v1）** | 通用 provider；浏览器/Turnstile 破解；前台 loop；在线 events prune；静态 turnstile env；把逻辑塞进 `sub2api_monitor.py` |

**一句话：**  
timer 约 4～5 分钟起短进程；复用 session；失败不覆盖 latest；events 用 **尾事件去重** 并保存 before/after；日志只打摘要。

---

## 2. 开工前安全闸门（P0-1）

手册与仓库现状：

- `docs/websites/botcf.md` / `torchai.md` **曾含**真实用户名与明文密码，且可能已进入 Git 历史。  
- `.gitignore` **不能**保护已跟踪文件。

**上线 / 开工前必须：**

1. **轮换** BotCF、TorchAI 密码，并失效旧 session。  
2. 手册与任何示例 **仅用占位符**；真实凭据只在 `sites/*.env`（0600）或系统密钥设施。  
3. 若仓库曾推送/分享：评估 `git filter-repo` 清历史 + 通知持有旧 clone 者；若从未离机，至少清理本地敏感提交。  
4. 检查单加入 **秘密扫描**（如 gitleaks，含历史）。  
5. `auth_state.json` **不**保存 `username`。

本条不是“代码功能”，而是 **P0 阻断**。

---

## 3. 目标架构

### 3.1 组件

```text
sites/<id>.env                 # 最小凭据与开关；stem == MONITOR_SITE_ID == systemd %i
data/<id>/                     # 固定：<项目根>/data/<site_id>/
  auth_state.json              # 0600，单一 session 表示
  groups_latest.json
  groups_events.jsonl
  monitor.lock

python newapi_monitor.py --env-file sites/%i.env [--validate]
        ▲  默认：有界单次采集后退出
newapi-monitor-once@%i.service
        ▲  Unit=（无 Requires=）
newapi-monitor-once@%i.timer
```

### 3.2 代码结构（防抽象失控）

```text
newapi_monitor.py          # ~300–450 行：Config、HTTP 状态机、normalize、CLI
monitor_storage.py         # 可选 ~150–250 行：atomic IO、尾事件、hash/diff、lock
tests/test_newapi_monitor.py
newapi-monitor-once@.{service,timer}
install_newapi_service.sh  # 短脚本，不复制 Sub2API legacy 分支
```

- **不要**一上来拆 `monitor_common/{io,lock,snapshot,once_retry}.py` 四模块。  
- **不要** provider registry / 统一双 backend CLI。  
- 若实现冲到 700–800 行：先删功能，再谈拆文件。  
- 与 Sub2API 共享 storage **仅**在修复全局 hash 去重 bug 时小范围抽取；认证层永不强行统一。

### 3.3 为何独立入口

Bearer refresh 与 session 状态机不同；独立入口隔离故障、简化回滚（停用 timer 即可，无 “回滚到旧 New-API 实现”）。

---

## 4. 应用层

### 4.1 配置（v1 最小集）

**推荐 env（常规站只需前 5 项 + 开关）：**

```bash
MONITOR_SITE_ID=botcf
MONITOR_BASE_URL=https://botcf.com
MONITOR_USERNAME=...
MONITOR_PASSWORD=...
REQUIRE_NEW_API_USER_HEADER=0
# 可选：
MONITOR_PROXY_URL=
CONNECT_TIMEOUT_SECONDS=5
READ_TIMEOUT_SECONDS=20
LOG_LEVEL=INFO
```

| 项 | 规则 |
|----|------|
| `MONITOR_SITE_ID` | 小写字母数字连字符；**必须** = env 文件 stem = systemd `%i` |
| `MONITOR_BASE_URL` | **纯 origin**：`https` + hostname；禁止 userinfo / query / fragment；path 空或 `/` |
| `REQUIRE_NEW_API_USER_HEADER` | `0` BotCF；`1` TorchAI；为 1 时 login 必须返回正整数 `data.id` |
| `DATA_DIR` | **不可配置**；固定为 `<项目根>/data/<site_id>` |
| `AUTH_STATE_FILE` | **不可配置**；固定 `DATA_DIR/auth_state.json` |

**v1 删除 / 不提供：**  
`MONITOR_BACKEND`、`MONITOR_LOGIN_PATH`、`MONITOR_GROUPS_PATH`、`MONITOR_USERNAME_FIELD`、`AUTH_STATE_FILE`、`EVENTS_RETENTION_DAYS`、`MAX_ONCE_ATTEMPTS`、`TURNSTILE_VALUE`、`POLL_INTERVAL_SECONDS`、`MONITOR_SITE_NAME`。

**固定契约（两站一致）：**

- Login：`POST {origin}/api/user/login?turnstile=`（turnstile **恒为空**）  
- Body：`{"username":"...","password":"..."}`  
- Groups：`GET {origin}/api/user/self/groups`  

`load_config`：**纯函数**，不 mutate `os.environ`。  
校验 env 文件 mode（无 group/other 访问）；读 `auth_state` 时也校验 owner/mode。

路径基准：**项目根**（含 `newapi_monitor.py` 的仓库根），**不是** env 文件所在的 `sites/` 目录。

### 4.2 登录成功契约

```text
login success 当且仅当：
  HTTP 200
  JSON object
  payload.success is true
  存在可用的 session cookie（名 session，domain 匹配 BASE host，path 允许 /）
  若 REQUIRE_NEW_API_USER_HEADER：data.id 为正整数

否则不得把响应中的 cookie 当作有效会话写入 auth_state。
```

注意：上游常用 **HTTP 200 + success=false** 表示密码错误/验证码/业务错误；**禁止**“HTTP 2xx 即成功”。

Turnstile：

- v1 **只**支持允许空 `turnstile` 的站点。  
- 若业务文案表明需要验证码 → `captcha`，本回合停止；**保留**仍可能有效的旧 session（若尚未清）。  
- **不**提供静态 `TURNSTILE_VALUE`（token 单次、约 5 分钟，不能当配置）。

### 4.3 分组成功契约

```text
groups success 当且仅当：
  HTTP 200
  JSON object
  payload.success is true
  payload.data 是 object 且非空（首批两站至少有可选组）

ratio：仅有限非负数值（见 data-model）；"自动" 等 → 整包 contract 失败。
```

### 4.4 失败分类（按下一步动作）

| 条件 | 动作 |
|------|------|
| 401，或未登录 / user header 不匹配等业务码 | 最多清会话并 **重登 1 次**，再 GET 1 次 |
| login `success=false` 且验证码文案 | `captcha`，停止 |
| 429 | `rate_limit`；解析 Retry-After（秒或 HTTP-date），受 deadline 限制；过长则留给下轮 timer |
| 408 / 5xx / timeout / 连接错误 | transient：**最多再试 1 次**（短退避） |
| 403 HTML / CF 标志 | `region`，**不**重登 |
| 2xx 但 envelope/字段/ratio 非法 | `contract`，保留 latest |
| 其他 4xx | permanent，不重试 |

日志：只记 status、content-type、业务 code、**脱敏/去控制字符/限长** 的 message。  
禁止：密码、cookie、完整 query、代理凭据、原始 body、逐条 group 描述。

### 4.5 HTTP 客户端约束

- 所有 API：`allow_redirects=False`（防 307/308 带密码跨 origin）。  
- 固定 `User-Agent: newapi-monitor/<version>`。  
- 默认 timeout：**connect 5s / read 20s**（可 env 覆盖，但应保持“小 JSON API”量级）。  
- 可选 `MONITOR_PROXY_URL`。

### 4.6 单轮流程与预算

```text
load config
  -> 校验 stem == site_id、origin、权限、固定 data path
  -> acquire flock
  -> restore allowlisted session (+ user_id if required)
  -> 若状态不完整：login（计 1）
  -> GET groups
       auth 类：清状态 -> login（+1，全回合 login ≤2）-> GET（仅再 1 次）
       transient：deadline 内短退避后 GET 再 1 次
       captcha/region/contract/permanent：停止
  -> validate + normalize 整包
  -> 若变化：尾事件去重 -> append event + fsync
  -> atomic replace latest（失败路径不写）
  -> summary log 一行
  -> release
```

**重试精简：**

| 项 | v1 |
|----|-----|
| 进程内 transient GET 重试 | 最多 **1** 次（合计 GET 语义上有界） |
| 全回合 login | 最多 **2**（首次 + 一次恢复） |
| `TimeoutStartSec` | 240s（systemd 保险） |
| 应用目标 deadline | **150～180s** monotonic |
| 每次 HTTP 前 | 检查剩余时间；收紧本次 timeout |
| CLI | **无** `MAX_ONCE_ATTEMPTS` env；固定行为；无前台 loop |

退出码：`0` 成功 / `1` 采集失败 / `2` 配置或锁。

### 4.7 日志摘要格式

```text
site=botcf result=ok|changed|unchanged|fail kind=… count=34 added=1 removed=0 modified=2 hash=sha256:abc…
```

不逐条打印 group。

---

## 5. 数据

详见 [data-model.md](./data-model.md)。强制摘要：

- `schema_version: 1` + `backend: "newapi"` + `site_id`。  
- group：`id`/`name`/`rate_multiplier`/`description`；**无**虚构 `status`。  
- events：**尾事件**去重；`before_hash`/`after_hash`；added/removed/modified 带足够字段做审计。  
- auth：**单一** `session` 对象 + 可选 `user_id`；读时校验 mode/domain。  
- v1 **无** events 在线 prune。

读取旧 latest 时校验 `site_id`/`backend`，不匹配 **硬失败**（防错误 DATA_DIR 串写）。

---

## 6. 运维

### 6.1 Unit

见 [timer-units.example.md](./timer-units.example.md)。

- 无 timer `Requires=`；`AccuracySec=1s`；`TimeoutStartSec=240`；oneshot 无 `[Install]`。  
- `ReadWritePaths` = 固定项目 `data/`；与不可配置 DATA_DIR 一致。  
- 建议 `UMask=0077`。  
- **已接受风险（v1）：** 进程仍可能以 root、工作目录在 `/root/projects/...` 运行（对齐现 Sub2API）；专用用户与 `/var/lib` 迁移为后续债务，不假装“已最小权限”。

### 6.2 安装脚本（短）

1. 校验 site id、env 权限、stem 一致。  
2. 应用 `--validate`。  
3. 安装并 `systemd-analyze verify` 两个 unit。  
4. 扫描所有 `sites/*.env`：**site_id 全局唯一**（含 Sub2API 站）。  
5. `daemon-reload`；`enable --now` timer。  
6. 打印 **停用** 命令（不称“回滚到旧实现”）。

**不需要：** POLL interval 中点校验、legacy simple 分支、通用 multi-backend 安装框架。

### 6.3 命令

```bash
.venv/bin/python newapi_monitor.py --env-file sites/botcf.env --validate
.venv/bin/python newapi_monitor.py --env-file sites/botcf.env

systemctl enable --now newapi-monitor-once@botcf.timer
systemctl enable --now newapi-monitor-once@torchai.timer
journalctl -u newapi-monitor-once@botcf -n 80 --no-pager
```

连续调试：用 `watch`、shell 循环或 timer，**不要**给入口加常驻 loop。

### 6.4 运维闭环（采集器最小要求）

| 项 | 定义 |
|----|------|
| 产品名 | 分组 **采集器** |
| 成功 | timer 周期内 `groups_latest.fetched_at` 更新 |
| **stale** | `now - fetched_at > 2 × 最大预期间隔 + 单轮超时`（例：间隔上界 ~300s+任务，超时 180s → 约 15 分钟量级可调） |
| 消费 | events 为审计/触发信号；v1 可由外部巡检读 latest/events 或挂 journal 告警，**不**新建告警平台 |
| 验收 | 不限于“timer 跑了两次成功”；须含 session 复用、失效恢复、stale 可观测 |

---

## 7. 首批站点

| site_id | origin | REQUIRE_NEW_API_USER_HEADER |
|---------|--------|------------------------------|
| `botcf` | `https://botcf.com` | `0` |
| `torchai` | `https://torchai.ai` | `1` |

细节与探活： [site-notes.md](./site-notes.md)。  
第三个站：先按探活表验证，**有证据**再加能力位；不做版本协商框架。

---

## 8. 测试与验收

### 8.1 自动化（必须）

1. 登录 HTTP 200 但 `success=false`：不写 auth_state。  
2. 需 user header 时缺合法 id：失败。  
3. session domain/path round-trip；groups 响应轮换 session 后落盘。  
4. 401 只重登一次。  
5. 403 HTML 不重登；403 JSON user-header 问题可恢复一次。  
6. 307/308 登录不跟随。  
7. ratio：`0`、十进制字符串成功；bool / 负 / NaN / Inf / `"自动"` 整包失败。  
8. 空 data：contract，不覆盖 latest。  
9. 字典序不同 hash 相同；description/ratio 变 hash 不同。  
10. **`A → B → A` 三次事件**；event 已写 latest 未写可恢复且不重复。  
11. events 半行尾恢复策略（截断到最后换行或隔离坏文件）。  
12. modified 含 before/after。  
13. latest `backend`/`site_id` 不匹配硬失败。  
14. env stem / site_id 冲突校验。  
15. **总 deadline 约束 HTTP**，不只 cap sleep。  
16. 失败路径日志无密码/cookie/用户名全文/代理凭据。  
17. 锁冲突可识别；SIGTERM 后下轮可获锁。

### 8.2 现场

- 第二轮进程 **复用 session**（无每轮密码登录）。  
- 人工失效 session → 一次重登恢复。  
- Torch 错误 user id → 恢复并更新 id。  
- 连续失败后 timer 仍调度；stale 可被外部规则发现。  
- 检查 **已安装** unit 属性，不只仓库模板。  
- journal 秘密模式扫描（多于 tail 80 行）。  
- 轮换凭据 + 演练 **停用** timer。

### 8.3 通过标准

- [ ] §2 安全闸门处理完毕（或书面接受残余 Git 历史风险）  
- [ ] §8.1 自动化通过  
- [ ] 两站真实 once + timer 试点 + session 复用证据  
- [ ] Sub2API 现网无回归  

---

## 9. 风险

| 风险 | 控制 |
|------|------|
| 手册/Git 泄密 | §2 |
| 复制 Sub2API 全文件 hash 去重 | 禁止；用尾事件 |
| 伪通用 New-API 客户端 | 范围收窄两站 |
| HTTP 200 假成功 | 业务 success 契约 |
| Turnstile 静态 token | 删除 |
| 密码随 redirect 泄露 | allow_redirects=False |
| DATA_DIR 串写 | 固定派生 + site_id 唯一 |
| unit 与路径不一致 | 固定 data root = ReadWritePaths |
| 无 stale 可见性 | §6.4 规则 |
| root 运行 | 已接受 v1 债务 |

---

## 10. 升级路径（有证据再走）

1. 共享 `monitor_storage`：修正 Sub2API 全局 hash 去重时一并抽取。  
2. 专用系统用户 + 状态目录迁出 `/root`。  
3. 外部 Turnstile provider（每次登录即时取 token）。  
4. 第三站探活后的最小能力位。  
5. ratio 标签类（如 `"自动"` → `rate_label`）——仅当站点真返回。

---

## 11. 决策记录

1. 独立入口 + timer oneshot — **保留**。  
2. 范围 = 两站 legacy session，非全系 — **收窄**。  
3. 无 run_loop / 无 interval 双源 / 无在线 prune — **删除**。  
4. events = 审计语义（before/after）+ 尾事件去重 — **采纳评审**。  
5. 固定 path/field/data 布局 — **采纳精简**。  
6. 应用 deadline + 最多 1 次 transient — **采纳**。  
7. 安全闸门先于写业务代码 — **P0**。

实现与上线：**以本文与 data-model 为闸门**；design-review 为历史依据。
