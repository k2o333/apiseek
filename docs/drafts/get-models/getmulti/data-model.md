# 分组倍率 — 数据模型（draft）

## 1. 原则

1. **分组真相源**仍是 `data/<site_id>/groups_latest.json`。  
2. 原始倍率与业务倍率 **同 group 对象**；不建平行 `rates_latest.json` 作真相源。  
3. 失败不覆盖成功 latest（沿用 monitor）。  
4. 禁止在 latest/events 写入 secret/token。

## 2. 目录（无新增文件）

```text
data/<site_id>/
  groups_latest.json      # 扩展字段（本方案）
  groups_events.jsonl     # diff/摘要可带 effective
  ...
```

可选生成物（非采集写路径）：

```text
docs/websites/table/groups_rates.csv
docs/websites/table/groups_rates.json
```

## 3. `groups_latest.json` 扩展

### 3.1 顶层（新增推荐字段）

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `rate_divisor` | number | 推荐 | 本快照写入时使用的 divisor；缺省消费者可当 `1` |
| 既有字段 | | 是 | `site_id`、`fetched_at`、`content_hash`、`count`、`groups`；New-API 另有 `schema_version`、`backend` |

示例（Sub2API 形，字段可多于摘要）：

```json
{
  "site_id": "pinaic",
  "fetched_at": "2026-07-23T09:07:37Z",
  "content_hash": "sha256:…",
  "count": 8,
  "rate_divisor": 10,
  "groups": [
    {
      "id": 69,
      "name": "CCMAX",
      "rate_multiplier": 16,
      "rate_multiplier_effective": 1.6,
      "status": "active",
      "platform": "anthropic",
      "description": "…"
    }
  ]
}
```

### 3.2 group 对象字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `id` | number \| string | 是 | 既有 |
| `name` | string | 是 | 既有 |
| `rate_multiplier` | number | 是 | **Provider 原始倍率** |
| `rate_multiplier_effective` | number | 是（本方案启用后） | `rate_multiplier / rate_divisor` |
| `status` / `platform` / `description` / … | | 视后端 | 既有；不因本方案删除 |

**对应保存语义：**  
消费者用 `(site_id, id)` 或 `(site_id, name)` 定位分组时，raw 与 effective **同一次 `fetched_at`**。

### 3.3 New-API 规范化 group

当前四字段：`id`, `name`, `rate_multiplier`, `description`。  
本方案扩展为五字段（+ `rate_multiplier_effective`）；顶层同样写 `rate_divisor`。

`group_content_json` / `content_hash_groups` / `diff_groups` 应纳入 effective（见 design §7）。

## 4. 计算不变量

对成功写入的每一条 group：

```text
rate_multiplier_effective * rate_divisor == rate_multiplier   # 浮点近似相等
rate_divisor == 快照顶层 rate_divisor
rate_divisor > 0 且有限
```

单测用相对误差或「除法再序列化」比对，避免硬编码二进制 float。

## 5. 事件

### 5.1 不新增 event type（首版）

继续使用既有 `initial` / 变更事件；倍率变化落在 modified 语义内。

### 5.2 New-API before/after 建议形状

```json
{
  "id": "Gemini",
  "before": {
    "rate_multiplier": 0.4,
    "rate_multiplier_effective": 0.4,
    "description": ""
  },
  "after": {
    "rate_multiplier": 0.5,
    "rate_multiplier_effective": 0.5,
    "description": ""
  }
}
```

仅改 divisor（raw 不变）时：

```json
"before": { "rate_multiplier": 16, "rate_multiplier_effective": 16 },
"after":  { "rate_multiplier": 16, "rate_multiplier_effective": 1.6 }
```

（假设旧快照曾以 divisor=1 写入。）

## 6. 与 models 的关系

| 数据 | 关系 |
|------|------|
| `models_latest.json` | **不**复制 rate 字段 |
| 查询 | `models_by_group[id]` ⟕ `groups_latest.groups[id]` |
| join 键 | `str(id).strip()`（既有约定） |

## 7. 导出表（可选）

`groups_rates` 类导出建议列：

```text
site_id, backend, fetched_at, group_id, group_name,
rate_multiplier, rate_multiplier_effective, rate_divisor,
status, platform, description
```

生成时间戳与站快照 `fetched_at` 分离标注，避免误以为导出瞬间重拉了远端。

## 8. 兼容矩阵

| 读方 | 旧 latest（无 effective） | 新 latest |
|------|---------------------------|-----------|
| 旧脚本只读 `rate_multiplier` | 行为不变 | 行为不变（raw 仍在） |
| 新脚本读 effective | 回退：`raw / env_divisor` 或 `raw` | 直接读字段 |
| content_hash 消费者 | 升级后首轮变化 | 稳定于 raw+effective+其它字段 |

## 9. 明确不做

- 历史 events 回填 effective  
- SQLite / 独立 rates 表  
- 按 group 名的 divisor 覆盖表（预留：未来 `MONITOR_RATE_DIVISOR_JSON` 类扩展，首版不写）  
- 将 effective 写回 provider
