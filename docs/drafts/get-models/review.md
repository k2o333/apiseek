# get-models 方案评审

评审对象：本目录的 `README.md`、`design.md`、`data-model.md`、`timer-units.example.md`、`implementation-checklist.md`，并对照当前 `sub2api_monitor.py`、systemd 单元和 `scripts/probe_groups_models.py`。

评审日期：2026-07-21。

## 0. 采纳状态（2026-07-21 回写）

| 项 | 状态 |
|----|------|
| P0-1 … P0-6 | **全部采纳**，已写入 `design.md` / `data-model.md` |
| P1-1 … P1-6 | **全部采纳** |
| 最小状态（删 keys_index、models 不复制 group 元数据） | **采纳** |
| secret 可回读硬前提（而非首版 secrets 文件） | **采纳**（评审推荐低熵项） |
| 同日 00:30 backup timer | **第一版不采纳**（优先有界等锁 + 进程内有限重试） |
| 方案文档 | 已按本节修订；以 `design.md` 为准实现 |

下文为评审原文，保留备查。

---

## 1. 结论

方案的主方向是对的：继续使用每站独立的 timer + oneshot，把 groups 高频采集与 models 低频采集拆开，使用 latest + events 落盘，不引入数据库，也不删除用户 Key。这些决策与现有仓库一致，部署和回滚边界也清楚。

但当前版本还不是可直接实现的“低熵定稿”。正常路径很简洁，异常路径尚未闭合，尤其是远端 Key 创建的幂等性、创建后 Key 列表的一致性、密钥不可回读时的恢复、首次启用语义和 daily 抢锁失败。照文档直接实现，可能产生重复/游离 Key、首轮意外全量请求、旧模型被错误隐藏，以及某天全量刷新静默漏跑。

建议结论：**保留总体架构，先解决下列阻断项并收敛数据模型，再进入 Phase 1。**

## 2. 做得好的部分

1. **调度边界正确。** 高频 groups 仍走现有 oneshot，models 全量刷新使用独立 daily timer，没有把分钟级和天级任务揉进常驻调度器。
2. **故障隔离方向正确。** 单组 models 失败不应回滚 groups，也不应覆盖最后一次成功模型列表。
3. **持久化方向正确。** latest 服务当前查询，events 服务审计，进程停止后仍可读；数据量很小，不需要 SQLite。
4. **远端变更策略克制。** 只补缺失 Key、不自动删除或改绑现有 Key，避免误伤人工配置。
5. **范围控制合理。** Sub2API 与 New-API 不强行抽象成同一后端。

## 3. 阻断问题

### P0-1：`create -> bind` 不是可重入事务，会持续制造游离或重复 Key

`design.md` 5.3 将缺组处理定义为 POST 创建后 PUT 绑定，并规定单组失败后继续、未来轮次再补。但以下窗口没有恢复协议：

- POST 已在服务端成功，客户端响应超时；下轮仍认为 Missing，再 POST 一次。
- POST 成功而 PUT 失败；新 Key 的 `group_id` 仍为空，下轮再次 POST。
- PUT 成功而客户端未收到响应；如果随后只看旧列表，仍可能重复创建。
- 进程在 POST 与 PUT 之间退出。

“只增不删”会让这个问题无法自动收敛。创建是远端写操作，不能采用普通 GET 的重试策略。

定稿要求：

1. 自动 Key 使用确定性名称，例如 `sub2api-monitor:g:<group_id>`，不要使用会改名、会重名的 `group.name` 作为身份。
2. 每次创建前先分页拉全 Key，先认领同名且未绑定的 managed Key；bind 后必须重新 list 并验证。
3. POST 超时属于“结果未知”，必须先重新 list/reconcile，禁止直接重发 POST。
4. 所有 group/key id 在比较前统一为字符串；当前 schema 允许 number/string，直接集合比较会把 `7` 与 `"7"` 当成不同组。
5. 补齐测试：POST 超时但服务端已创建、bind 失败、bind 响应丢失、进程重启四种场景，验证最终至多创建一个 managed Key。

### P0-2：Key 列表没有分页契约，`Missing` 可能是假的

探针只请求 `p=1&page_size=100`，设计只讨论 envelope，没有定义 total/page/has_more，也没有要求拉到末页。用户已有 Key 超过一页时，后页已覆盖的 group 会被误判为 Missing，进而创建重复 Key。

定稿要求：`list_keys_all()` 必须解析并遍历分页；若服务端无法证明已经取全，ensure 必须 fail closed，只允许读模型，不允许创建 Key。

### P0-3：增量流程使用创建前的旧 `keys`，新组可能仍然取不到模型

`design.md` 5.1 的顺序是：

```text
keys = list_keys()
ensure_coverage(groups)
key = pick_representative_key(keys, gid)
```

架构图又把 `ensure_coverage` 的返回值写成 `created[]`。因此刚创建并绑定的 Key 不一定进入后续 `pick` 所使用的列表。探针之所以工作，是因为它在 ensure 后重新执行了 `get_keys()`。

定稿要求：ensure 返回经过服务端复核的 `keys_after`，或者调用方强制重新 `list_keys_all()`；不能继续使用旧列表。

### P0-4：密钥不可回读时的“未来降级”不可实现

当前方案依赖 `GET /api/v1/keys` 每次返回完整 Key。文档提出未来不再返回时再增加 `keys_secrets.json`，但到那时历史 Key 的 secret 已无法恢复；而 coverage 又会因为已有绑定 Key 拒绝创建替代 Key，形成永久死状态。

首版必须二选一并写成契约，不能留作实现时 feature 探测：

- **推荐的低熵选择：** 将“Key 列表可回读完整 secret”设为站点能力前提。bootstrap 先做只读 preflight；不满足则该站不启用模型链路，且绝不创建 Key。
- 或者从首版开始把自动创建 Key 的 secret 原子写入 0600 secret store，并设计历史 Key 无 secret 时的显式轮换流程。这更可靠，但状态和运维复杂度明显更高。

### P0-5：默认启用会把一次普通代码发布变成远端写入和冷启动全量

`MONITOR_MODELS_ENABLE` 默认值为 1，而 Phase 2 又规定 `refresh_set = added ∪ 缺快照组`。已有 `groups_latest`、尚无 `models_latest` 的站在新代码第一次 `--once` 时，所有组都是“缺快照”，会执行全量 ensure + models；这与“冷启动使用 `--models-bootstrap`”及 240 秒预算相冲突。

这也意味着部署新版本会在没有逐站确认的情况下自动创建远端 Key，不符合最小惊讶和回滚原则。

定稿要求：

- 自动增量默认关闭，逐站 preflight + bootstrap 成功后再显式开启。
- `models_latest` 不存在不能由普通 `--once` 隐式 bootstrap；必须存在 `bootstrap_completed_at` 或等价标志后，`--once` 才处理真正新增组。
- “关闭模型能力”应有一个清楚的总开关语义；目前 env 只关增量、daily 仍需另停 timer，名称容易误导。

### P0-6：JWT 认证失败与 API Key 失败混成了同一种 auth 恢复

`design.md` 7.1 写的是 keys/models 遇到 auth 都 refresh/login 后重试。但 keys CRUD 使用 JWT，`/v1/models` 使用分组 API Key。后者 401 通常表示 Key 禁用、过期、未正确绑定或 secret 不对，重新登录不能修复，反而增加无效认证请求。

定稿要求：

- keys API 的 401/令牌型 403：沿用 JWT refresh/login 一次。
- models API 的 401/403：标记该 Key 不可用，尝试同组下一个可用 Key；没有候选时记录 `no_usable_key`，不得触发登录。
- coverage 的定义应是“至少一个可用、已绑定、可取得 secret 的 Key”，而不是任意非空 `group_id`。仅有 disabled/expired Key 不能算覆盖。

## 4. 高优先级问题

### P1-1：daily 抢锁失败后可能整天不再执行

现有 `InstanceLock` 是非阻塞锁；方案也明确 daily 与 5 分钟任务冲突时后到者 exit 1。calendar timer 的当次触发此时已经被消费，`Persistent=true` 只补机器关机时错过的触发，不会因为 service 失败自动在当天重试。因此一次普通碰撞就可能让该站整天没有全量刷新。

建议让 daily 对锁做有界等待，等待上限至少覆盖一轮普通 `--once`；若仍失败，再提供明确的有限重试机制。不要把“人工重跑”作为正常竞态的恢复路径。

另外，daily 持同一把锁做最长 600 秒网络操作时，groups 轮询也会被跳过。可以接受偶发跳过一轮，但文档应把最大 stale 窗口写进验收，而不是继续声称 5 分钟语义完全不变。

### P1-2：失败状态会把仍然有效的旧成功列表隐藏掉

`data-model.md` 规定失败组写 `status=error` 并保留旧 `models`，但查询伪代码只有 `status == ok` 才返回模型，否则返回空数组。这在数据层保留了旧值，却在读取层把它解释成“无模型”，违背“失败不覆盖成功快照”。

建议把“最后成功结果”和“最后尝试结果”正交保存：

- `models`、`content_hash`、`last_success_at` 始终代表最后一次成功。
- `last_attempt_at`、`last_error` 代表最近尝试。
- 下游始终可以读取旧 models，同时根据 `last_success_at` 和 `last_error` 判断 stale/degraded。
- 从未成功的组使用 `models: null`，不要用空数组混淆“成功返回 0 个模型”和“尚无成功结果”。

### P1-3：`last_full_refresh_at` 在部分成功时语义不真实

设计在循环结束后写 `last_full_refresh_at`，但 daily 允许部分成功。下游看到该时间无法判断是“全部组已对齐”还是“只完成一组”。

建议拆为：

- `last_full_attempt_at`
- `last_full_success_at`：仅本轮所有目标组成功时更新
- 最近一轮 `target_count / ok_count / failed_count`

并明确 daily 部分失败的退出码。仅“0 组成功才非 0”会让 systemd 把大多数降级轮次标成成功，弱化告警。

### P1-4：缺快照组会在每个 5 分钟轮次无限重试

方案称 models 是低频采集，但 `refresh_set` 永远包含缺快照组。若某组持续 contract/403/no-secret，`--once` 会每 4～5 分钟重复 keys/models 请求，实际退化为高频失败探测。

建议新组只在首次观察时做一次增量尝试；失败后设置有界 `next_retry_at`，或留给 daily 重试。contract/no-secret 至少应冷却到 daily，429 应尊重 Retry-After。

### P1-5：长批次只在末尾写盘会丢掉已完成进度

文档没有明确 models 是每组 checkpoint，还是全组完成后一次写入。若 daily 在第 15 组被 600 秒超时终止，而前 14 组只存在内存中，本轮成果会全部丢失。

models 文件很小，建议每个组尝试完成后都原子合并写 latest；模型变化 event 与该组 latest 更新采用固定顺序，并明确允许 at-least-once event。需要测试进程在任意组后退出时，已完成组仍可读。

### P1-6：代表 Key 与 coverage 规则未形成同一个确定算法

当前定义一处说“第一个”，一处说 active/enabled 优先再取最小 id；但没有定义 status 缺失、布尔状态、过期时间、无 secret、多 Key 中首个调用失败时如何处理。`id` 若为字符串，普通字典序还会出现 `"10" < "2"`。

建议定义一个纯函数 `usable_keys(group_id)`：先规范化 group_id，过滤禁用/过期/无 secret，按规范化 key id 稳定排序；模型请求遇到 key-auth 失败时顺序尝试候选。只有候选为空时才进入 reconcile/create。

## 5. 熵与简洁性评估

### 5.1 当前不够低熵的地方

1. **同一事实有多个权威来源。** group name/platform/rate/status 同时存在于 `groups_latest`、`models_latest` 和 `keys_index`，更新频率不同，必然产生互相矛盾的值。
2. **`keys_index.json` 是可派生状态。** 它来自远端 list keys，却又试图记录 `managed` 历史；默认 Key 名没有固定前缀，上游也未证明返回 `created_by`，重启后无法可靠判断某 Key 是否由 monitor 创建。
3. **removed 策略仍是二选一。** `design.md` 写“标记 removed 或移入 orphaned”，data model 又增加 30 天 prune；这是核心状态机，不应留给实现者选择。
4. **多个“可选/建议”落在正确性路径上。** 是否重拉 groups、是否存 keys_index、是否保存 secret、events 是否去重、是否补缺快照，都需要定稿，不能同时作为实现自由度。
5. **配置项包含无效权威。** `MONITOR_MODELS_DAILY_TZ` 在 Python env 中不能决定 systemd `OnCalendar` 的时区，容易形成双源配置。

### 5.2 建议的最小状态模型

首版只新增两个文件：

```text
models_latest.json
models_events.jsonl
```

`keys_index.json` 首版删除。远端 Key 列表是 coverage 权威，自动 Key 用确定性名称识别；日志输出 reconcile 摘要即可。若未来确有离线审计需求，再增加由远端快照生成的只读报表，不参与控制决策。

`models_latest` 只保存 models 自身事实，不复制 group 元数据：

```json
{
  "schema_version": 1,
  "site_id": "littleapi",
  "updated_at": "...",
  "last_full_attempt_at": "...",
  "last_full_success_at": "...",
  "last_full_result": {"target": 7, "ok": 6, "failed": 1},
  "models_by_group": {
    "52": {
      "key_id": 843,
      "models": ["grok", "grok-4.5"],
      "content_hash": "sha256:...",
      "last_success_at": "...",
      "last_attempt_at": "...",
      "last_error": null
    }
  }
}
```

读取时以 `groups_latest` 为当前分组权威，通过字符串化 group id join。模型缓存里多出的旧 id 不需要额外 `group_status/orphaned/retention` 状态；当前查询自然忽略它，group 的 removed 历史由既有 `groups_events` 表达。这样也避免 group 元数据日更与 5 分钟快照冲突。

### 5.3 建议的最小模块边界

当前 `sub2api_monitor.py` 已较长。建议保留认证和 groups 主流程，在独立同仓模块中放置：

```text
sub2api_models.py
  KeysClient
  ModelsClient
  ModelsStore
  reconcile_key_for_group()
  refresh_models_for_groups()
```

这不是为了抽象而抽象，而是隔离“只读监控”和“远端写 Key”两种风险面。不要再引入 provider registry、基类层次或通用任务框架。

## 6. 调度建议

1. daily 始终重新 GET groups，删除“信任 latest / 可选 re-GET”的分支。
2. timer 直接使用 systemd 支持的时区语法：`OnCalendar=*-*-* 00:00:00 Asia/Shanghai`，无需按机器时区维护 A/B 两套配置，也无需 Python 的 timezone env。
3. 使用 `RandomizedDelaySec=300` 后，语义应写成“上海 00:00 后 0～5 分钟触发”，不是精确 00:00。
4. daily 有界等待同站锁；普通 groups 任务仍可非阻塞退出并在下一周期恢复。
5. 全量流程使用总 deadline，每组 checkpoint；不要仅依赖 systemd 在 600 秒硬杀进程。

## 7. 还需要补充的测试与验收

在现有 checklist 之外，至少增加：

- keys 多页、页中重复、无法确认总页数时禁止 create。
- number/string group id 归一化。
- disabled/expired/no-secret Key 不算 usable coverage。
- create 超时、bind 失败、响应丢失、重启后的 reconcile 幂等。
- ensure 后使用服务端复核的新 Key 列表。
- models 401 不触发 JWT 登录，并可回退到同组下一个 Key。
- 旧 models + 新 error 同时可读；从未成功与成功空列表可区分。
- daily 部分成功、硬超时和中途退出后的逐组 checkpoint。
- 缺快照持续失败不会每 5 分钟无限请求。
- daily 与 groups 实际并发，验证 daily 不会因一次锁冲突漏掉整天。
- 任意响应 message、异常和落盘文件都经过 secret 扫描；除完整 Key 外，也应评估是否有必要在 0644 的探针文件中保留 `key_preview`。
- 自动能力默认关闭；仅完成 preflight + bootstrap 的站会产生远端写请求。

真实站验收不能只看 `ok_count == group_count`。还应记录 list keys 的分页证据、创建前后 Key 数量、第二次 bootstrap 的 `created=0`、同一 bootstrap 在故障注入后的最终 Key 数量，以及 systemd 下一次触发时间。

## 8. 推荐落地顺序

1. 先把上述 P0 项写成确定契约，删掉核心路径上的“可选/建议/二选一”。
2. 实现完全只读的 capability preflight：groups、全量 list keys、secret 可回读、models envelope，不创建 Key。
3. 实现确定性 Key reconcile，并用 mock 故障测试证明可重入。
4. 实现最小 `models_latest` + events，完成失败保旧与逐组 checkpoint。
5. 单站显式 bootstrap，连续跑两次证明第二次不创建 Key。
6. 默认关闭的 T-new 单站灰度，再接 daily timer 和锁碰撞验收。
7. 最后逐站开启。`aijws` 已从活跃站表剔除（不再 preflight/create）。

## 9. 最终判断

- **正确性：** 主流程成立，但远端写入和失败恢复尚未闭环，当前不可直接编码上线。
- **低熵：** 调度架构低熵，状态模型和可选分支偏高熵；删除 `keys_index`、去掉重复 group 元数据后会明显改善。
- **简洁优雅：** timer + oneshot、独立 daily 是简洁的；“监控轮次默认自动创建 Key”不够克制，应显式启用并有可重入 reconcile。
- **考虑不周：** 主要遗漏是 POST 结果未知、分页、secret 不可回读、认证域混淆、冷启动隐式全量、失败快照读取语义、锁冲突漏掉 daily，以及批次中途退出。

完成 P0 修订后，这个方案可以在不改变总体架构的前提下成为一版边界清楚、实现量可控的低熵方案。
