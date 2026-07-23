# Storage Contracts

> 状态：draft  
> Contract IDs：`storage/sub2api-groups-legacy`、`storage/newapi-groups-v1`、`storage/models-v1`、`storage/auth-behavior-v1`  
> 本文件定义目标不变量；当前实现差异见 [../inventory-as-is.md](../inventory-as-is.md)。

## 1. 通用规则

除显式标记的 Sub2API legacy 格式外，新 writer：

1. 必须写 `schema_version`、`site_id`、`backend`；
2. 必须使用 UTF-8；latest JSON 必须以一个换行结尾；
3. 时间必须为 UTC RFC 3339 秒精度 `YYYY-MM-DDTHH:MM:SSZ`；
4. hash 必须为 `sha256:<64 lowercase hex>`；
5. latest 必须用同目录临时文件、flush、`fsync`、atomic replace；
6. event 必须一行一个 JSON object，append 后 `fsync`；
7. provider/auth/contract 失败禁止用空值覆盖最后成功快照；
8. 普通 latest/events 禁止包含密码、JWT、refresh token、session value、完整 API Key 或 proxy userinfo；
9. reader 必须拒绝未知 version、错 site 或错 backend；
10. legacy 必须经显式 compatibility branch 读取，禁止把任意缺字段文件当 legacy。

字段策略：

- 顶层 envelope 和 models entry 应当 `additionalProperties: false`；
- New-API 规范化 group 必须 `additionalProperties: false`；
- Sub2API legacy group 保留完整上游 object，可以有额外字段；
- schema 增加可选字段前必须先升级 schema/reader，再升级 writer。

## 2. `storage/sub2api-groups-legacy`

### 2.1 定位

这是历史兼容契约，不是新 backend 的模板。默认决策是保持读写可复现，不主动迁 groups v2。只有出现跨 backend 统一消费的硬需求，才另开 ADR 设计 v2。

latest 现有 envelope：

```text
site_id, fetched_at, count, content_hash, groups
```

event 现有 envelope：

```text
site_id, observed_at, event, added, removed, modified, content_hash
```

### 2.2 Stable identity

```python
stable_id(group):
    value = group["id"] if group.get("id") is not None else group.get("name", "")
    return str(value)
```

`id=7` 与 `id="7"` 的 stable id 相同，但完整 object JSON 不同，因此它们的最终 content hash 不同。

### 2.3 Canonicalization 与 hash

必须按以下算法跨语言复现：

```python
def canonicalize(groups):
    return sorted(
        groups,
        key=lambda g: (
            stable_id(g),
            json.dumps(g, sort_keys=True, ensure_ascii=False),
        ),
    )

payload = json.dumps(
    canonicalize(groups),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
content_hash = "sha256:" + sha256(payload.encode("utf-8")).hexdigest()
```

注意：排序 tie-break 中的 JSON 使用 Python 默认 separators；最终 hash payload 使用紧凑 separators。向量必须锁定这个差异。

hash 包含完整 group object。上游新增字段、更新时间或任意值变化都可以改变 hash；这是 legacy 行为，不应在同一 contract 下改成字段白名单。

### 2.4 Diff 与 event

- diff map key 使用 stable id；
- added/removed/modified 最终是 id 值列表；纯十进制或负整数文本会转换成 JSON integer；
- initial 在没有可用 previous groups 时三个 diff 列表为空；
- dedup 使用 events 历史中是否出现过 content hash；
- 因此 `A -> B -> A` 的第二次 A 可以不追加恢复事件；
- event 先 append+fsync，latest 后 atomic replace；
- retention 当前按 `observed_at` 由进程在线 prune。

上述 event 行为仅为 legacy 可复现性保留。禁止复制到 New-API 或新版本。

### 2.5 Legacy reader

reader 只有同时满足以下条件才可识别此格式：

- 运行时 expected backend 是 `sub2api`；
- 顶层没有 `schema_version/backend`；
- `site_id` 等于 expected site；
- `groups` 是 list；
- `content_hash` 格式合法并能用上述算法复算一致。

否则必须拒绝，不能按 id 是否数字猜 backend。

## 3. `storage/newapi-groups-v1`

### 3.1 Envelope

latest 必须包含：

```text
schema_version=1
site_id
backend=newapi
fetched_at
count
content_hash
groups
```

event 必须包含：

```text
schema_version=1
site_id
backend=newapi
observed_at
event=initial|groups_changed
before_hash
after_hash
added
removed
modified
```

### 3.2 Group 规范化

provider `data` 必须是非空 object。每个 key/value 规范化为：

```text
id               = trimmed object key
name             = id
rate_multiplier  = finite non-negative number
description      = "" when desc is null, else str(desc)
```

必须：

- 拒绝 trim 后空 name；
- 拒绝 trim 后重复 name；
- ratio 可以是数值或可解析数值字符串，但 bool 禁止；
- ratio 必须有限且 `>= 0`；
- 任一 item 非法时整包 contract fail，不覆盖 latest；
- 结果按 id 升序。

### 3.3 Hash

hash 只包含每个规范化 group 的：

```text
id, name, rate_multiplier, description
```

```python
payload = json.dumps(
    groups_sorted_by_id,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
content_hash = "sha256:" + sha256(payload.encode("utf-8")).hexdigest()
```

hash 禁止包含时间、site、backend、count、auth 或 provider envelope。

### 3.4 Diff

- added/removed 是完整四字段 group object；
- modified 是 `{id,before,after}`；
- before/after 只包含 `rate_multiplier` 和 `description`；
- name 改变等价于 remove old + add new；
- initial 的 `before_hash=null`，added 是完整初始集合；
- changed 的 before/after 必须等于旧/新 latest hash。

### 3.5 Tail dedup 与崩溃恢复

`events_dedup=tail_after_hash`：

```text
latest hash == new hash
  -> 只刷新 latest metadata，不追加 event

latest hash != new hash
  -> 修复 JSONL 半行尾
  -> 读取最后一条完整 event
  -> last.after_hash != new hash 时 append+fsync
  -> atomic replace latest
```

必须提供状态向量：

```text
none -> A
A -> A
A -> B
B -> A
event(B) 已写但 latest 仍 A，重启后采 B
events 尾部只有半行
```

`A -> B -> A` 必须产生 initial(A)、changed(A,B)、changed(B,A) 三条事件。

## 4. `storage/models-v1`

### 4.1 当前格式与 legacy profile

目标 writer 继续使用 `schema_version=1`，但必须输出统一当前 profile：

```text
schema_version=1
site_id
backend=sub2api|newapi
updated_at
bootstrap_completed_at
last_full_attempt_at
last_full_success_at
last_full_result
last_incremental_at
models_path
models_by_group
```

现网已有缺 backend、三字段 full result 的 Sub2API v1。它被命名为 `models-v1-legacy-sub2api`，只允许受限兼容，不是当前 writer 合法输出。

该历史格式在 machine contract 冻结前已经落盘，因此可以作为 pre-contract legacy profile 收敛到当前 v1；它不构成一个已经 frozen、却被原地破坏的 v1。首次冻结后再发生同类 shape 变化必须正常升版本。

### 4.2 顶层不变量

- `last_full_result` 是 null 或恰好 `{target,ok,failed,skipped}`；
- 四个计数都是非负 integer；
- 必须满足 `target == ok + failed + skipped`；
- 只有 `target > 0 && ok == target && failed == 0 && skipped == 0` 才更新 `last_full_success_at`；
- 只有完整成功的 bootstrap 才首次设置 `bootstrap_completed_at`；
- 后续失败禁止清空已有 bootstrap/success 时间；
- `models_by_group` key 是 `str(group_id).strip()` 或规范化 group name；空 key 禁止。

### 4.3 Model parser

provider parser 必须验证整个 group 的 models envelope：

- envelope 必须匹配对应 provider profile；
- 每一 row 必须是 object；
- 每一 row 必须存在 `id`；
- `id` 必须是 string；
- `id.strip()` 必须非空。

任一 row 违反规则时，该 group 为 `contract` failure：

- 禁止静默 drop malformed row；
- 禁止把剩余子集写成成功；
- 必须保留该 group 最后成功的 models/hash/key_id/last_success_at；
- 更新 last_attempt/error/retry 状态。

合法 `data: []` 是成功空列表，与从未成功的 null 不同。

### 4.4 规范化与 hash

两个 backend 必须使用同一算法：

```python
normalized = sorted({model_id.strip() for model_id in model_ids})
payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
content_hash = "sha256:" + sha256(payload.encode("utf-8")).hexdigest()
```

规范化后的列表同时用于落盘、计数、event diff 和 hash。禁止出现“落盘顺序与 hash 输入不同”的双重语义。

### 4.5 Per-group entry

每组必须包含：

```text
key_id
models
content_hash
last_success_at
last_attempt_at
last_error
next_retry_at
source
```

不变量：

- `models=null`：从未成功；此时 hash/success 必须 null；
- `models=[]`：上游明确成功返回空列表；hash/success 必须非 null；
- success 更新 models/hash/success/attempt/key_id，清 error/retry；
- failure 只更新 attempt/error/retry 和可选 source；
- failure 禁止修改最后成功 models/hash/success/key_id；
- source enum 统一为 `bootstrap|refresh|incremental`；daily timer 执行的也是 refresh mode，必须写 `source=refresh`，禁止把调度器名称作为第四种数据语义。

### 4.6 Models event

v1 明确使用 `events_dedup=at_least_once`：

- event 只允许 `initial|models_changed`；
- 仅成功 content hash 改变时追加；
- 首次成功为空数组也写 initial；
- added/removed 各最多 50 项，任一截断则 `truncated=true`；
- event 必须加入 `schema_version/site_id/backend`；
- 崩溃发生在 event append 与 latest checkpoint 之间时，允许同一变化重复 event；
- 若未来改成 tail dedup，必须升 models event contract version。

### 4.7 Legacy Sub2API reader

缺 backend 文件仅在以下条件下允许读取：

1. expected backend 明确为 `sub2api`；
2. `schema_version == 1`；
3. `site_id == expected site`；
4. `models_by_group` 为 object；
5. full result 为合法三字段且 `target == ok + failed`；
6. 每组字段 shape 合法；
7. 不含与 New-API profile 冲突的显式 backend。

兼容 reader 在内存中：

- 填 `backend=sub2api`；
- 给三字段 full result 增加 `skipped=0`；历史上已合并到 failed 的 deadline skip 保持在 failed，不能伪造拆分；
- 将 latest entry 的 legacy `source=daily` 规范化为 `source=refresh`；
- 标记 `needs_migration=true`。

禁止 read-only reader 自动写磁盘。持久化当前格式只能由：

- 显式幂等 migration；或
- 下一次本来就会发生的成功 checkpoint。

### 4.8 Legacy migration

显式 migration 必须：

1. 验证上述 legacy 条件；
2. 保留原文件的可恢复备份或使用可回滚版本文件；
3. 添加 backend 和 skipped；
4. 对每组 models 使用目标算法规范化并重算 hash；
5. 保留 success/attempt/error/retry/key_id 时间和值；
6. 原子写回 latest；
7. 禁止为纯表示迁移追加 `models_changed` event；
8. 不重写历史 JSONL；legacy event reader 继续兼容旧行；
9. 重复执行不得产生进一步变化。

若不执行显式 migration，下一次真实成功 refresh 可以按新算法产生业务 event；实现必须区分这是 provider 内容变化还是 legacy 表示变化，避免伪告警。推荐部署前先显式 migration。

历史 models JSONL 行缺 `schema_version/backend` 时，只能在以下文件上下文识别：expected backend 是 Sub2API、当前站点已通过 legacy latest 校验、event `site_id` 匹配且字段 shape 合法。reader 在内存补 `schema_version=1/backend=sub2api`，并把 `source=daily` 映射为 `refresh`。历史行禁止原地重写；迁移后的文件可以同时包含 legacy 行和当前行，逐行识别必须由 vectors 覆盖。

## 5. `storage/auth-behavior-v1`

Auth cache 不进入 public 字段级 schema bundle，只冻结五类行为：

1. `sites/*.env`、`token.json`、`auth_state.json` 必须 0600；
2. cache 必须 atomic write，禁止打印或复制进普通 snapshot；
3. domain/site/backend 不匹配时禁止复用；
4. corrupt/unknown cache 必须安全丢弃并重新认证，禁止带未知状态请求；
5. cache schema 演进可以让旧 cache 一次性失效，但失败模式必须是重新认证，不是错站复用或 secret 泄露。

## 6. 必需 vectors

| Contract | 必需向量 |
|---|---|
| Sub2API groups legacy | full object 排序、number/string id、Unicode、上游额外字段、历史 hash |
| New-API groups v1 | 顺序/字段顺序、ratio string、null desc、Unicode、A->B->A、半行/崩溃窗口 |
| Models v1 | 顺序、重复、空白、Unicode、null vs empty、malformed/non-string id、失败保旧 |
| Models migration | 缺 backend、三字段 result、重复/乱序列表、幂等、无 event |

冻结时完整示例必须从 vectors 生成或由 schema 校验，禁止继续只放在散文中。
