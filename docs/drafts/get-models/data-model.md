# 数据模型：分组 + 模型持久化（定稿草案）

> 已按 `review.md` 收敛为 **最小状态模型**：仅 `models_latest.json` + `models_events.jsonl`。  
> 不引入 `keys_index.json`；不在 models 中复制 group 元数据。

## 1. 原则

1. 文件即库；停机可读 latest。  
2. **最后成功** 与 **最后尝试** 正交；失败不覆盖成功 `models`。  
3. 从未成功用 `models: null`；成功空列表用 `models: []`。  
4. group 是否存在 / name / rate → 只信 `groups_latest`；join 键为 `norm_id = str(id).strip()`。  
5. 事件只在 models `content_hash` 变化时追加（允许 at-least-once）。  
6. 禁止落盘完整 api_key / token / password。

---

## 2. 目录

```text
data/<site_id>/
  token.json              # 已有 0600
  monitor.lock            # 已有
  groups_latest.json      # 已有（分组权威）
  groups_events.jsonl     # 已有
  models_latest.json      # 新增
  models_events.jsonl     # 新增
```

**不做（首版）：** `keys_index.json`、`keys_secrets.json`、models 侧 orphaned/retention 状态机。

汇总（可选生成物）：

```text
docs/websites/table/models_all.{json,md}
```

---

## 3. `groups_latest.json`

保持现有 schema 与 content_hash 算法。  
**不**把 models 嵌进 group 对象。

---

## 4. `models_latest.json`

### 4.1 结构

```json
{
  "schema_version": 1,
  "site_id": "littleapi",
  "updated_at": "2026-07-21T13:20:45Z",
  "bootstrap_completed_at": "2026-07-21T12:05:00Z",
  "last_full_attempt_at": "2026-07-21T16:00:12Z",
  "last_full_success_at": "2026-07-21T16:00:12Z",
  "last_full_result": {
    "target": 7,
    "ok": 7,
    "failed": 0
  },
  "last_incremental_at": "2026-07-21T13:20:45Z",
  "models_path": "/v1/models",
  "models_by_group": {
    "52": {
      "key_id": 843,
      "models": ["composer-2.5", "grok", "grok-4.5"],
      "content_hash": "sha256:…",
      "last_success_at": "2026-07-21T13:20:40Z",
      "last_attempt_at": "2026-07-21T13:20:40Z",
      "last_error": null,
      "next_retry_at": null,
      "source": "incremental"
    },
    "9": {
      "key_id": 835,
      "models": ["gpt-5.6", "gpt-5.5"],
      "content_hash": "sha256:…",
      "last_success_at": "2026-07-20T16:00:01Z",
      "last_attempt_at": "2026-07-21T16:00:10Z",
      "last_error": "HTTP 503",
      "next_retry_at": "2026-07-22T16:00:00Z",
      "source": "daily"
    },
    "99": {
      "key_id": null,
      "models": null,
      "content_hash": null,
      "last_success_at": null,
      "last_attempt_at": "2026-07-21T12:10:00Z",
      "last_error": "no_usable_key",
      "next_retry_at": "2026-07-22T16:00:00Z",
      "source": "bootstrap"
    }
  }
}
```

### 4.2 顶层字段

| 字段 | 含义 |
|------|------|
| `schema_version` | 当前 `1` |
| `bootstrap_completed_at` | 成功完成 `--models-bootstrap` 后写入；**T-new 前置条件** |
| `last_full_attempt_at` | 最近一次 bootstrap/daily **开始尝试全量**完成写盘的时间（含部分失败） |
| `last_full_success_at` | 最近一次 **全部目标组成功** 的全量时间；部分失败 **不** 更新 |
| `last_full_result` | `{target, ok, failed}` 最近全量结果 |
| `last_incremental_at` | 最近一次 T-new 写盘时间 |
| `models_by_group` | 键 = `norm_id(group_id)` |

### 4.3 每组字段

| 字段 | 含义 |
|------|------|
| `key_id` | 最后一次 **成功** 使用的代表 Key id；从未成功可为 null |
| `models` | 最后成功列表；`null` = 从未成功；`[]` = 成功且上游为空 |
| `content_hash` | 对成功 `models` 排序后的 sha256；从未成功为 null |
| `last_success_at` | 最后成功时间 |
| `last_attempt_at` | 最后尝试时间（成功或失败） |
| `last_error` | 最后失败短消息（脱敏）；成功尝试后可清 null |
| `next_retry_at` | 增量路径下次允许尝试；null 表示无冷却限制（仍受触发器约束） |
| `source` | `bootstrap` \| `daily` \| `incremental` |

**失败写盘规则：** 只更新 `last_attempt_at` / `last_error` / `next_retry_at`（及可选 source）；**不得**清空或改写 `models` / `content_hash` / `last_success_at` / 成功时的 `key_id`。

**成功写盘规则：** 更新 models、hash、last_success_at、key_id；clear last_error；last_attempt_at=now。

### 4.4 content_hash

```text
canonical = json.dumps(sorted(models), ensure_ascii=False, separators=(",", ":"))
content_hash = "sha256:" + sha256(utf8(canonical))
```

### 4.5 逐组 checkpoint

每处理完一个 group，合并进内存结构后 **立即** `write_json_atomic(models_latest)`。  
允许 events 与 latest 在崩溃时 at-least-once 重复；读取以 latest 为准。

### 4.6 与 groups 的 join（只读查询）

```python
groups = load_groups_latest()["groups"]
mbg = load_models_latest().get("models_by_group") or {}

for g in groups:
    gid = str(g["id"]).strip()
    m = mbg.get(gid) or {}
    models = m.get("models")          # null | list
    degraded = m.get("last_error") and m.get("last_success_at")
    # 展示：若 models is not None，用 models（可同时展示 last_error 表示 stale/degraded）
    # 若 models is None：尚无成功结果
```

**不要**用「status==ok 才返回 models」把旧成功隐藏掉。

`models_by_group` 中存在但 `groups_latest` 已无的 id：当前查询自然忽略；无需 `group_status=removed` 字段。

---

## 5. `models_events.jsonl`

### 5.1 事件

| event | 何时 |
|-------|------|
| `initial` | 该 group 第一次成功写入 models |
| `models_changed` | 同 group 成功且 content_hash 变化 |
| `bootstrap_completed` | 可选：整站 bootstrap 成功一条 |

不强制写每轮 `models_error`（防刷屏）；失败看 latest 的 `last_error`。

### 5.2 示例

```json
{
  "site_id": "littleapi",
  "observed_at": "2026-07-21T13:20:40Z",
  "event": "models_changed",
  "group_id": "9",
  "key_id": 835,
  "model_count": 24,
  "content_hash": "sha256:…",
  "source": "daily",
  "added_models": ["gpt-5.7"],
  "removed_models": [],
  "truncated": false
}
```

`group_id` 在事件中亦为字符串。  
`added_models`/`removed_models` 可截断（如各 50）并 `truncated: true`。

### 5.3 保留

与 groups 类似，按 `observed_at` 与 `EVENTS_RETENTION_DAYS` prune（实现可复用逻辑）。

---

## 6. 远端 Key（不落盘为控制面）

| 事实 | 权威 |
|------|------|
| 有哪些 Key、绑哪组 | 每次 `list_keys_all()` |
| 是否 managed | `name == "sub2api-monitor:g:" + norm_id` |
| secret | 列表字段实时读取；不入 models_latest |

日志可打 reconcile 摘要：`created=N skipped=N failed=N`，不写 keys_index 文件。

---

## 7. 全局 `models_all`（可选）

由各站 `models_latest` + `groups_latest` **只读 join** 生成；不访问线上 API。  
列：`site_id, group_id, group_name, platform, rate, model_count, models(截断), last_success_at, last_error`。

---

## 8. Schema 版本

| version | 说明 |
|---------|------|
| 1 | 本定稿：`models_by_group` + 正交成功/尝试 + bootstrap/full meta |

---

## 9. 禁止

- 完整 sk / jwt / password  
- 用空数组表示「从未成功」  
- 失败时把 `models` 改成 `[]` 或删除  
- 依赖 `keys_index` 做 coverage 决策  
