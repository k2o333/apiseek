# Storage Contracts

> Publication：formal
> Status：draft
> Contract IDs：`storage/sub2api-groups-legacy`、`storage/newapi-groups-v1`、`storage/models-v1`、`storage/auth-behavior-v1`、`storage/invite-link-v1`

本文件定义拟冻结的持久化不变量。状态为 draft 时，它是实现收敛目标，不是“当前所有 writer 已满足”的声明。当前差异见 [实现设计索引](../../03%20designs/README.md)。

## 1. 通用规则

除显式标记的 Sub2API legacy 格式外，新 writer：

1. 必须写 `schema_version`、`site_id`、`backend`；
2. 必须使用 UTF-8，latest JSON 以换行结尾；
3. 时间必须为 UTC RFC 3339 秒精度 `YYYY-MM-DDTHH:MM:SSZ`；
4. hash 必须为 `sha256:<64 lowercase hex>`；
5. latest 必须同目录临时文件、flush、`fsync`、atomic replace；
6. event 必须一行一个 JSON object，append 后 `fsync`；
7. provider/auth/contract 失败禁止以空结果覆盖最后成功快照；
8. 普通 latest/events 禁止包含密码、JWT、refresh token、session、完整 API Key 或 proxy userinfo；
9. reader 必须拒绝未知 version、错 site 和错 backend；
10. legacy 只能经显式 compatibility branch 读取，禁止把任意缺字段文件猜成 legacy。

字段策略：顶层 envelope、models entry 和 New-API normalized group 应当 `additionalProperties: false`；Sub2API legacy group 保留完整上游 object，允许额外字段。

## 2. `storage/sub2api-groups-legacy`

### 2.1 定位与 envelope

这是历史兼容契约，不是新 backend 的模板。没有跨 backend 统一消费的硬需求时，不主动迁 groups v2。

latest 字段：

```text
site_id, fetched_at, count, content_hash, groups
```

event 字段：

```text
site_id, observed_at, event, added, removed, modified, content_hash
```

### 2.2 Identity、canonicalization 与 hash

```python
stable_id(group):
    value = group["id"] if group.get("id") is not None else group.get("name", "")
    return str(value)

canonical = sorted(
    groups,
    key=lambda g: (
        stable_id(g),
        json.dumps(g, sort_keys=True, ensure_ascii=False),
    ),
)
payload = json.dumps(
    canonical,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
content_hash = "sha256:" + sha256(payload.encode("utf-8")).hexdigest()
```

排序 tie-break 的 JSON 使用 Python 默认 separators，最终 hash payload 使用紧凑 separators。vectors 必须锁定这一历史差异。hash 包含完整 group object，上游新增字段也会改变 hash。

### 2.3 Diff 与 event

- diff map key 使用 `stable_id`；
- added/removed/modified 最终为 id 值列表；纯整数文本沿用 legacy JSON integer 行为；
- initial 的三个 diff 列表为空；
- dedup 通过扫描历史 events 是否已有相同 `content_hash`；
- 因此 `A -> B -> A` 的第二次 A 可以不产生恢复事件；
- event 先 append+fsync，latest 后 atomic replace。

这些是 legacy 可复现性，不得复制到新 contract。

### 2.4 Legacy reader

只有同时满足以下条件才可接受：

- expected backend 明确是 `sub2api`；
- 顶层没有 `schema_version/backend`；
- `site_id` 与 expected site 一致；
- `groups` 是 list；
- `content_hash` 格式合法且按上述算法复算一致。

## 3. `storage/newapi-groups-v1`

### 3.1 Envelope

latest 必须包含：

```text
schema_version=1, site_id, backend=newapi, fetched_at,
count, content_hash, groups
```

event 必须包含：

```text
schema_version=1, site_id, backend=newapi, observed_at,
event=initial|groups_changed, before_hash, after_hash,
added, removed, modified
```

### 3.2 Group 规范化

provider `data` 必须是非空 object。每个 key/value 规范化为：

```text
id              = trimmed object key
name            = id
rate_multiplier = finite non-negative number
description     = "" when desc is null, else str(desc)
```

必须拒绝空 name、trim 后重复 name、bool ratio、非有限数和负数。任一 item 非法时整包 contract fail，不覆盖 latest；结果按 id 升序。

### 3.3 Hash 与 diff

hash 只包含规范化 group 的 `id/name/rate_multiplier/description`，按 id 排序后用以下 JSON 参数计算：

```python
json.dumps(groups, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

- added/removed 是完整四字段 group object；
- modified 是 `{id,before,after}`，before/after 仅含 multiplier 和 description；
- name 改变等价于 remove old + add new；
- initial 的 `before_hash=null`，added 是完整初始集合。

### 3.4 Tail dedup 与崩溃恢复

```text
latest hash == new hash
  -> 只刷新 latest metadata，不追加 event

latest hash != new hash
  -> 修复 JSONL 半行尾
  -> 读取最后一条完整 event
  -> last.after_hash != new hash 时 append+fsync
  -> atomic replace latest
```

必须覆盖 `none->A`、`A->A`、`A->B`、`B->A`、event 已写而 latest 未写、JSONL 半行尾。`A->B->A` 必须产生三条事件。

## 4. `storage/models-v1`

### 4.1 当前目标 profile

目标 writer 使用 `schema_version=1` 并输出：

```text
schema_version, site_id, backend=sub2api|newapi, updated_at,
bootstrap_completed_at, last_full_attempt_at, last_full_success_at,
last_full_result, last_incremental_at, models_path, models_by_group
```

已存在的 Sub2API models 文件可能缺 `backend`、使用三字段 full result 或 `source=daily`。它们是 `models-v1-legacy-sub2api`，允许受限读取，但不是目标 writer 合法输出。

### 4.2 顶层不变量

- `last_full_result` 是 null 或恰好 `{target,ok,failed,skipped}`；
- 四个计数是非负 integer，且 `target == ok + failed + skipped`；
- 仅 `target > 0 && ok == target && failed == 0 && skipped == 0` 更新 `last_full_success_at`；
- 仅完整成功 bootstrap 首次设置 `bootstrap_completed_at`；
- 后续失败禁止清已有 bootstrap/success 时间；
- `models_by_group` key 必须 trim 后非空。

### 4.3 Model parser

每个 provider profile 必须严格验证整个 group 的 models envelope：

- envelope 匹配 profile；
- 每个 row 是 object；
- `id` 存在、类型为 string、trim 后非空。

任一 row 非法时整组为 `contract` failure：禁止 drop 单行后把剩余子集写成功；保留该组最后成功 models/hash/key_id/success_at，只更新 attempt/error/retry。

合法 `data: []` 是成功空列表，与从未成功的 null 不同。

### 4.4 规范化与 hash

```python
normalized = sorted({model_id.strip() for model_id in model_ids})
payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
content_hash = "sha256:" + sha256(payload.encode("utf-8")).hexdigest()
```

规范化列表同时用于落盘、count、diff 和 hash，禁止落盘顺序与 hash 输入分裂。

### 4.5 Per-group entry

```text
key_id, models, content_hash, last_success_at, last_attempt_at,
last_error, next_retry_at, source
```

- `models=null` 表示从未成功，hash/success 必须 null；
- `models=[]` 表示 provider 成功返回空列表，hash/success 必须非 null；
- success 更新 models/hash/success/attempt/key_id，清 error/retry；
- failure 只更新 attempt/error/retry 和可选 source；
- failure 禁止修改最后成功 models/hash/success/key_id；
- source 只允许 `bootstrap|refresh|incremental`，daily 调度仍写 `refresh`。

### 4.6 Models event

v1 使用 `events_dedup=at_least_once`：

- event 只允许 `initial|models_changed`；
- 仅成功 content hash 改变时追加；
- 首次成功为空数组也写 initial；
- added/removed 各最多 50 项，截断时 `truncated=true`；
- event 必须带 `schema_version/site_id/backend`；
- event append 后、latest checkpoint 前崩溃时允许重复事件。

将 models event 改成 tail dedup 必须升版本。

### 4.7 Legacy reader 与 migration

缺 backend 文件只有在 expected backend=`sub2api`、version=1、site 匹配、shape 合法、三字段 result 满足 `target==ok+failed` 时可读。

兼容 reader 只在内存中：

- 补 `backend=sub2api`；
- full result 补 `skipped=0`；
- `source=daily` 映射为 `refresh`；
- 标记 `needs_migration=true`。

禁止 read-only reader 写磁盘。显式 migration 必须幂等、可回滚、重算规范化 hash，且不得为纯表示变化追加业务 event；历史 JSONL 不原地重写。

## 5. `storage/auth-behavior-v1`

Auth cache 不进入公共字段级 schema，只冻结：

1. `sites/*.env`、`token.json`、`auth_state.json` 必须 0600；
2. cache 必须 atomic write，禁止进入普通 snapshot 或日志；
3. domain/site/backend 不匹配时禁止复用；
4. corrupt/unknown cache 安全丢弃并重新认证；
5. schema 演进可以令旧 cache 一次失效，但不能错站复用或泄密。

## 6. `storage/invite-link-v1`

> Status：draft  
> 路径：`{data_dir}/invite_latest.json`  
> applies_to：sub2api、newapi-legacy

邀请链接是**可分享业务快照**，不是 JWT/session。mode 允许 `0644`；禁止写入 groups/models latest/events，也禁止附带 password、access_token、refresh_token、session 或完整 API Key。

### 6.1 Envelope（schema_version=1）

latest 必须包含：

```text
schema_version=1
site_id
backend          # "sub2api" | "newapi"
base_url         # HTTPS origin，无 path/query/fragment，无尾 /
aff_code         # 非空、trim 后无空白
invite_link      # 必须精确等于 base_url + "/register?aff=" + aff_code
fetched_at       # UTC RFC3339 秒精度 Z
checked_at       # UTC RFC3339 秒精度 Z
ttl_seconds      # 正整数；默认 1209600（14 天）
```

第一版**不写** invite events JSONL。

### 6.2 拼装与远端源

| backend | 拉码 | invite_link |
|---|---|---|
| sub2api | `GET /api/v1/user/aff` → `data.aff_code` | `{base_url}/register?aff={aff_code}` |
| newapi | `GET /api/user/self` 的 `data.aff_code`，或 `GET /api/user/aff` | 同上 |

### 6.3 刷新策略

1. **base_url 变化**（与当前 config 规范化 origin 比较）：必须立即远端重拉并重写 `invite_link`；禁止只改前缀而不校验码，也禁止继续复用旧 `base_url` 的 link。
2. **base_url 未变**：若 `checked_at` 距今不足 `ttl_seconds` 且 envelope 合法，可跳过远端（TTL 命中）；否则远端重拉。
3. 显式 force（CLI `--force`）忽略 TTL，仍受 base_url 规则约束。
4. 远端或 contract 失败：**禁止**用空/残缺结果覆盖已有合法 latest。
5. reader 必须拒绝：未知 `schema_version`、错 `site_id`、错 `backend`、`aff_code` 非法、`invite_link` 与拼装规则不一致。

### 6.4 Non-goals

- 不进入 groups timer 默认路径；独立 CLI oneshot。
- 不把 aff 当作 auth cache（不要求 0600）。
- 不承诺跨站聚合文件。

## 7. 冻结所需 artifacts

| Contract | 必需 artifacts |
|---|---|
| Sub2API groups legacy | schema/识别器、number/string id、Unicode、额外字段、历史 hash vectors |
| New-API groups v1 | schema、ratio/null、Unicode、A-B-A、半行和崩溃窗口 vectors |
| Models v1 | schema、顺序/重复/空白、null/empty、malformed id、失败保旧 vectors |
| Models migration | legacy fixture、幂等、备份/回滚、无伪 event vectors |
| Invite link v1 | envelope 识别、base_url 失效、TTL 跳过、失败保旧 vectors（冻结前补齐） |

完整 JSON 示例必须从 vectors 生成或由 schema 校验，禁止散文和实现各维护一套互相漂移的字段表。
