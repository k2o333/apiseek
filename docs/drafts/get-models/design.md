# Sub2API 分组模型采集 — 详细设计（定稿草案）

> **评审采纳：** 已吸收 `review.md`（2026-07-21）全部 P0 与 P1 定稿要求，以及最小状态模型建议。  
> 变更摘要见 [README.md §评审采纳](./README.md#评审采纳)。

## 1. 背景与问题

### 1.1 现状

- 入口：`sub2api_monitor.py`
- 调度：`sub2api-monitor-once@<site>.timer` → oneshot `--once`（约 4～5 分钟）
- 鉴权：`data/<site>/token.json`（login / refresh）
- 产出：`groups_latest.json`、`groups_events.jsonl`
- **不**创建 API Key，**不**调用 `/v1/models`

### 1.2 业务缺口

1. 某站有哪些分组？（已有）
2. **某分组当前能打哪些模型？**（本方案）
3. 新上线的分组，能否尽快出现模型列表？（本方案：已 bootstrap 站上的 **真正新增组** 做一次增量）
4. 模型列表是否需要与分组同频刷新？（**否** — 全量仅日更）

### 1.3 约束

- 每站独立 env + 独立 `DATA_DIR`，无 SQLite / `sites.yaml`（第一版）
- timer + oneshot，**不做**多线程 Supervisor
- 失败不覆盖最后一次 **成功** 的模型列表
- 密钥不进 Git、不进普通日志；`sites/*.env` 与 token 权限 0600
- **不删除、不改绑** 用户已有 API Key（只认领 managed 未绑定 Key 或新建）
- New-API 站不混用本链路
- **远端写 Key 必须可重入**；自动能力 **默认关闭**，逐站显式开启

---

## 2. 目标与非目标

### 2.1 目标

| ID | 目标 | 验收要点 |
|----|------|----------|
| G1 | Sub2API 站可适配 Key 覆盖 + 模型拉取 | preflight 通过后 bootstrap；每组有 models 成功结果或可解释 error |
| G2 | 持久记录扩大 | `models_latest.json` + `models_events.jsonl`；停机可读 |
| G3 | 分组约 5 分钟更新 | 现有 timer 保留；偶发与 daily 抢锁时允许跳过一轮（见 7.4） |
| G4 | 模型低频更新 | 仅：真正新组一次尝试 / 有界重试 / 每日全量 |
| G5 | 与现有监控共存 | 同站 flock；可安装、可回滚；默认不创建远端 Key |

### 2.2 非目标（第一版不做）

- 不采集用量、余额、订单；不做 completion 探测
- 不为 New-API 统一模型接口
- 不写 `keys_index.json` 控制面；不把 secret 默认落第二份盘
- 不自动删除冗余 Key；不做「分组改名重建 Key」
- 不引入 provider registry / 通用任务框架

---

## 3. 站点能力前提（preflight，只读）

启用任何 **创建 Key** 或 **写 models 快照** 之前，该站必须通过 **capability preflight**（零写操作）：

| 检查 | 要求 | 失败动作 |
|------|------|----------|
| groups | `GET groups` 成功，`data` 为 list | 不启用 |
| keys list 分页 | `list_keys_all()` 能证明取全（见 5.2） | **禁止 create**；可只读探测 models 若已有 secret |
| secret 可回读 | 列表中至少能对已绑 Key 读到完整 `key` 字段 | **该站不启用模型链路，绝不 create** |
| models envelope | 若有可用 Key，`/v1/models` 返回可解析的 id 列表 | 不启用自动链路 |

**定稿（P0-4）：** 首版契约 = **「Key 列表必须可回读完整 secret」**。  
不实现「未来 keys_secrets.json 降级」；不满足则停在 preflight，避免「有绑定无 secret 的永久死状态」。

CLI：

```bash
python sub2api_monitor.py --env-file sites/<id>.env --models-preflight
# exit 0 = 可通过；非 0 = 打印失败项，不写远端
```

---

## 4. 核心概念

### 4.1 ID 归一化

**所有** group_id / key_id 在集合比较、map 键、落盘键之前统一：

```text
norm_id(x) = str(x).strip()
```

禁止用原始 number/string 混比（避免 `7` ≠ `"7"`）。

### 4.2 集合与 coverage（usable）

```text
G = available groups（norm_id）
keys_all = list_keys_all()   # 必须分页取全，否则 fail closed
usable(g) = usable_keys(g) 非空
C = { g | usable(g) }
Missing = G − C
```

**coverage 定义（P0-6）：** 至少一个 **可用** Key：已绑定该 group、非禁用/非过期、且能取得 secret。  
仅有 `group_id` 但 disabled / 无 secret **不算**覆盖。

### 4.3 自动 Key 身份（确定性名称）

```text
managed_key_name(group_id) = "sub2api-monitor:g:" + norm_id(group_id)
```

- **禁止**用可变的 `group.name` 作为身份键（可显示在日志，不作为 reconcile 键）。
- 识别 managed Key：名称精确等于上述模式（前缀固定，重启后可判定，无需 `created_by` 字段）。

### 4.4 代表 Key：纯函数 `usable_keys` / `pick_key`

```text
usable_keys(group_id, keys_all) -> ordered list:
  1. filter: norm(group_id) 匹配
  2. filter: secret 非空
  3. filter: 非 disabled / 非 expired（字段缺失则不过滤该维）
  4. sort: 按 (managed_name 优先? 否) 统一按 norm(key_id) 做「整数优先」稳定序：
     - 可解析为 int 的按数值比
     - 否则按字符串比
     避免 "10" < "2"

pick_key = usable_keys 的第一个
list_models 时：按 usable_keys 顺序尝试；某 Key 401/403（Key 域）则试下一个；
全部失败 -> no_usable_key，不得 JWT login。
```

### 4.5 模型刷新触发器

| 触发器 | 时机 | 范围 | 前置 |
|--------|------|------|------|
| **T-bootstrap** | 运维显式 `--models-bootstrap` | ensure Missing + 全组 models | preflight 通过 |
| **T-new** | `--once` 中 groups 成功后 | **仅本轮 diff 的真正 added** 且站点已 `bootstrap_completed` | `MODELS_INCREMENTAL_ENABLE=1` |
| **T-daily** | 每天上海 00:00 后抖动 | 全量 ensure + 全组 models | 站已 enable daily timer |

**不做：**

- 默认把「`models_latest` 缺快照」并入每 5 分钟 refresh_set（避免部署即全量 + 无限失败重试）
- 未 bootstrap 站上的隐式冷启动

**新组失败重试（P1-4）：**  
T-new 对每个新 group_id **首次观察时尝试一次**；失败则写 `next_retry_at`（或仅依赖 daily）。  
- `contract` / `no_usable_key` / 明确 403：冷却至下一次 daily，**禁止**每 5 分钟重打  
- `429`：尊重 Retry-After，有界  
- `timeout` / `5xx`：可设较短 `next_retry_at`（如 30～60 分钟），仍非每轮必打  

---

## 5. 详细流程

### 5.1 模块边界

```text
sub2api_monitor.py          # 认证、groups 主流程、CLI 编排、锁
sub2api_models.py           # 新增：远端写 Key 与 models 风险面隔离
  list_keys_all()
  reconcile_key_for_group()
  ensure_coverage()
  list_models()
  refresh_models_for_groups()
  ModelsStore
```

不引入基类层次 / provider registry。

### 5.2 `list_keys_all()` 分页契约（P0-2）

1. 从 `p=1`（或等价 page）循环请求，合并 items。  
2. 解析 total / page / page_size / has_more 等任一可用证据；或「本页条数 < page_size」作为末页。  
3. **若无法证明已取全：fail closed** — `ensure_coverage` **禁止 POST create**；仅允许对已有 usable Key 读 models（若调用方需要）。  
4. 单测：多页、页中重复 id 去重、无 total 且满页后下一页空、无法确认时禁止 create。

### 5.3 `reconcile_key_for_group`（P0-1 幂等）

对单个 `group_id`（已 norm）：

```text
1. keys = 调用方传入的最新 keys_all（不得用过期快照）
2. 若 usable_keys 非空 -> 返回已有，created=false
3. 查找 name == managed_key_name(g) 且 group_id 为空的 Key
   -> 对其 PUT bind；bind 后 list_keys_all 复核；成功则返回
4. 查找 name == managed_key_name(g) 且已绑到本 group -> 返回
5. 否则 POST create { name: managed_key_name(g) }
   - POST 超时 / 连接中断 / 5xx 后客户端未确认：
       **禁止立即重 POST**
       必须 list_keys_all + 按 name 认领（步骤 3/4）
   - POST 明确 4xx 业务失败：记录 error，不重试本轮
6. PUT bind group_id
   - 失败：不删 Key；下轮从步骤 3 认领未绑定 managed Key
7. bind 后 **必须** list_keys_all，验证 usable；不得信任单次 PUT 响应体 alone
8. 返回 keys_after（全量列表）与结果摘要
```

`ensure_coverage(groups)`：

- 先 `list_keys_all`；不可取全则 **abort create**  
- 对 Missing 逐个 `reconcile_key_for_group`  
- 单组失败继续其他组  
- **返回 `keys_after`**（最后一次成功 list），调用方 **禁止** 继续用 ensure 前的 `keys`（P0-3）

幂等验收：同一 Missing 组在「POST 超时但服务端已创建 / bind 失败 / bind 响应丢失 / 进程重启」下，最终 **至多一个** managed Key。

### 5.4 Groups 一轮 + T-new（`--once`）

```text
poll_once:
  acquire lock (非阻塞；失败 exit，下周期再来)
  ensure_token (JWT)
  groups = get_groups()
  prev = load groups_latest
  persist groups
  added = diff(prev, groups).added   # norm_id 集合

  if models_incremental_enabled AND bootstrap_completed:
    refresh_set = { g in added | should_attempt_now(g) }  # 尊重 next_retry_at
    if refresh_set:
      keys = list_keys_all()          # fail closed 影响 create
      keys = ensure_coverage(...)     # 返回 keys_after
      refresh_models_for_groups(refresh_set, keys)
  release lock
```

**bootstrap_completed（P0-5）：**  
`models_latest.meta.bootstrap_completed_at` 非空（由成功 `--models-bootstrap` 写入）。  
不存在 `models_latest` 或无该标志时，`--once` **不得** 因「缺快照」拉全站 models，也 **不得** 自动 create Key。

### 5.5 Bootstrap / Daily

```text
models_bootstrap / models_refresh:
  acquire lock（daily：有界等待，见 7.4）
  ensure_token
  preflight 关键检查（bootstrap 强制；daily 可短检 secret 仍可读）
  groups = get_groups()              # daily/bootstrap **必须** 重新 GET，禁止只信 latest
  optional: 若 groups 有变化，走既有 SnapshotStore 落盘
  keys = list_keys_all()
  keys = ensure_coverage(groups)     # keys_after
  refresh_models_for_groups(all G, keys)  # 逐组 checkpoint
  更新 meta.last_full_attempt_at / last_full_result
  若全部目标成功：last_full_success_at；bootstrap 则写 bootstrap_completed_at
  release lock
```

### 5.6 `refresh_models_for_groups`（P1-5 checkpoint）

对每个 group（串行）：

1. `candidates = usable_keys(g, keys)`  
2. 无候选 → 写 attempt error `no_usable_key`，**保留**旧成功 models；checkpoint 写盘；continue  
3. 按序 `list_models(secret)`  
   - Key 域 401/403 → 试下一候选；**不** JWT recover  
   - JWT 无关  
4. 成功 → 更新该组 `models` / `content_hash` / `last_success_at`；hash 变则 append event  
5. 失败 → 只更新 `last_attempt_at` / `last_error` / `next_retry_at`  
6. **每组结束后原子合并写 `models_latest`**（不是全批结束才写）  
7. 进程任意点退出：已完成组仍可读  

进程内 **总 deadline**（如 500s）与 systemd `TimeoutStartSec=600` 双保险；临近 deadline 停止开新组，已完成保留。

### 5.7 认证域分离（P0-6）

| 调用 | 凭证 | 401/令牌型 403 |
|------|------|----------------|
| groups / keys CRUD | JWT Bearer | refresh/login **一次** 后重试当轮 keys 操作 |
| `/v1/models` | API Key Bearer | **禁止** login；换同组下一 Key 或记 `no_usable_key` |

### 5.8 Envelope 兼容

| 接口 | 解析 |
|------|------|
| groups | `data` 为 list（**禁止**写死 `data.data`） |
| keys | `data.items` 或 `data` list + 分页字段 |
| models | `data: [{id}]` 等 unwrap |

---

## 6. 状态模型（最小）

### 6.1 每站仅新增两个文件

```text
data/<site>/
  models_latest.json      # 模型成功结果 + 尝试元数据
  models_events.jsonl     # 模型列表变化（及可选 bootstrap 事件）
```

**首版删除 `keys_index.json`：** coverage 权威 = 远端 `list_keys_all`；managed 身份 = 确定性名称。  
group 的 name/platform/rate/**是否存在** 以 `groups_latest` 为唯一权威；查询时 join，**不在 models 文件复制 group 元数据**。

### 6.2 `models_latest` 语义（成功与尝试正交，P1-2 / P1-3）

见 [data-model.md](./data-model.md)。要点：

- `models` + `content_hash` + `last_success_at` = 最后一次成功（永不被失败覆盖）
- `last_attempt_at` + `last_error` + `next_retry_at` = 最近尝试
- 从未成功：`models: null`（不是 `[]`）；成功且上游返回空列表：`models: []`
- 顶层：`last_full_attempt_at` / `last_full_success_at` / `last_full_result` / `bootstrap_completed_at`
- 查询忽略 models 里多出的旧 group id；removed 历史只靠 `groups_events`，**不做** models 侧 orphaned/30 天 prune 状态机

### 6.3 开关语义（P0-5）

| 变量 / 标志 | 默认 | 含义 |
|-------------|------|------|
| `MONITOR_MODELS_INCREMENTAL_ENABLE` | **`0`** | `--once` 是否对 **真正 added** 做 T-new |
| `bootstrap_completed_at` | 无 | 无则 `--once` 不做任何 models/create |
| daily timer | 未安装 | 安装 = 显式启用日更 |
| `MONITOR_KEYS_PATH` | `/api/v1/keys` | |
| `MONITOR_MODELS_PATH` | `/v1/models` | |

**总开关（运维）：**

- 完全关闭模型写路径：增量 env=0 **且** disable daily timer  
- 文档与 install 脚本使用「关闭模型能力」时同时说明两者  

**删除：** `MONITOR_MODELS_DAILY_TZ`（时区只由 systemd `OnCalendar=... Asia/Shanghai` 表达，避免双源）。

---

## 7. 失败、锁与时间预算

### 7.1 错误分类

| kind | keys API | models API |
|------|----------|------------|
| auth (JWT) | recover 一次 | 不适用 |
| key_auth | — | 试下一 Key |
| region | 不重登 | 不重登 |
| rate_limit | Retry-After | Retry-After |
| timeout/server/network | 该组失败继续 | 该组失败继续 |
| contract | fail closed create | 冷却至 daily |
| paging_incomplete | **禁止 create** | — |

### 7.2 退出码

| 命令 | 建议 |
|------|------|
| `--once` groups 失败 | 非 0（现网） |
| `--once` groups 成功，T-new 部分失败 | **0** + 日志（避免 timer 风暴） |
| `--models-bootstrap` / `--models-refresh` | `failed_count > 0` → **非 0**（含部分成功）；全成功 → 0 |
| preflight 失败 | 非 0 |

（修正原「仅 0 组成功才非 0」：部分失败也必须非 0，便于 systemd/告警，P1-3。）

### 7.3 时间预算

| 场景 | 预算 |
|------|------|
| 常规 `--once` 仅 groups | 现有 240s |
| `--once` + 少量 T-new | 仍争 240s；新组少 |
| bootstrap / daily | 进程内 deadline ~500s；`TimeoutStartSec=600` |
| 逐组 checkpoint | 必须 |

### 7.4 锁（P1-1）

| 任务 | 取锁 |
|------|------|
| `--once`（groups） | **非阻塞**；失败则本轮跳过，下周期恢复 |
| `--models-refresh` / bootstrap | **有界等待**（建议 ≥ 一轮 `--once` 最坏时间，如 90～120s）；超时仍失败则 **有限重试**（见下） |

**Daily 漏跑防护：**

1. 有界等待锁（覆盖普通 groups 持锁窗口）  
2. service 内：锁失败则 sleep 短间隔再试，最多 N 次（如 3），仍在同一 oneshot 进程  
3. 可选：timer `OnCalendar` 增加同日一次 backup（如 00:30）——若采用须写进 install，避免无限复杂；**最低要求是 (1)+(2)**  
4. **禁止**把「人工 start」当作正常竞态恢复路径  

**5 分钟语义诚实表述（P1-1）：**  
daily 持锁最长可达数分钟时，groups 轮询可能 **跳过一轮**。验收写明：同站最大分组 stale 窗口 ≈ `groups 周期 + daily 持锁时间`（偶发），而非「绝对永不大于 5 分钟」。

---

## 8. 安全

1. 凭据仅 env + token.json；日志 mask sk/jwt  
2. `models_latest` / events / 汇总表 **禁止** 完整 api_key；探针文件避免保留 `key_preview`（或仅调试且 0600）  
3. 默认不落 keys secret 文件  
4. 自动 create 仅 managed 名；二次 bootstrap `created=0`  
5. 发布默认不开启增量；无 preflight+bootstrap 不写远端  

---

## 9. 调度与 CLI

### 9.1 CLI

```bash
--models-preflight      # 只读能力检查
--models-bootstrap      # 显式冷启动：ensure + 全量 models + 写 bootstrap_completed_at
--models-refresh        # 日更：re-GET groups + ensure + 全量 models
--once                  # groups；仅当增量开关且已 bootstrap 时 T-new
```

### 9.2 systemd

- groups：现有 `sub2api-monitor-once@`  
- daily：`sub2api-models-daily@`  
  - `OnCalendar=*-*-* 00:00:00 Asia/Shanghai`  
  - `RandomizedDelaySec=300` → 语义为 **上海 00:00 后 0～5 分钟**，非精确 00:00  
  - `Persistent=true`  
  - `TimeoutStartSec=600`  
详见 [timer-units.example.md](./timer-units.example.md)。

---

## 10. 站级适配

| site_id | 顺序 |
|---------|------|
| littleapi, pinaic, aiapibank | preflight → bootstrap×2 → 开增量灰度 → daily |
| aresaicode, hubway, iaiguo, klinkw, yybb | 同上 |
| aijws | preflight **全部通过前禁止 create** |

---

## 11. 可观测性

日志字段：`phase=preflight|ensure|models`、`keys_created`、`paging_complete`、`models_ok/fail`、`lock_wait_ms`。  
验收除 `ok_count` 外：分页证据、create 前后 Key 数量、二次 bootstrap `created=0`、故障注入后 managed Key ≤1、systemd 下次触发时间。

---

## 12. 测试（含 review §7）

- 多页 keys、无法确认总页数时禁止 create  
- norm_id number/string  
- disabled/expired/no-secret 不算 usable  
- create 超时 / bind 失败 / 响应丢失 / 重启 reconcile 幂等  
- ensure 后使用 keys_after  
- models 401 不 JWT login；候选 Key 回退  
- 旧 models + 新 error 同时可读；`null` vs `[]`  
- 逐组 checkpoint；硬超时保留已完成  
- 缺快照/contract 失败不每 5 分钟无限打  
- daily 与 groups 并发：有界等待后仍能完成或同进程有限重试  
- secret 扫描：响应、异常、落盘  

---

## 13. 分阶段落地（修订）

1. **契约定稿**（本文 + data-model）— 本步  
2. **只读 preflight**（无 create）  
3. **幂等 reconcile + mock 故障**  
4. **最小 models_latest/events + checkpoint + 失败保旧**  
5. **单站 bootstrap×2**（第二次 created=0）  
6. **默认关闭的 T-new 灰度**  
7. **daily timer + 锁碰撞验收**  
8. **逐站开启**；aijws 最后  

---

## 14. 决策摘要

| 议题 | 定稿 |
|------|------|
| 架构 | timer+oneshot；groups 高频 / models 低频；无 DB |
| Key | 确定性名；reconcile 可重入；只增不删；分页取全否则不 create |
| Secret | 列表可回读为硬前提；不落 keys_secrets |
| 默认 | 增量关闭；须 preflight + bootstrap 标志 |
| 状态 | 仅 models_latest + events；join groups_latest |
| Auth | JWT 与 API Key 分域 |
| Daily | 上海 00:00+抖动；有界等锁；部分失败 exit≠0；逐组 checkpoint |
| 新组 | 一次尝试 + 冷却；非每轮缺快照全量 |

完成以上后，本方案可作为 **边界清楚、实现量可控** 的实现输入进入 Phase 1。
