# BotCF / TorchAI 采集 — 数据模型（修订）

吸收 design-review：尾事件去重、审计型 events、单一 session、无虚构 status、无 v1 retention、显式 schema。

---

## 1. 目录布局

```text
<项目根>/data/<site_id>/
  auth_state.json       # 0600
  groups_latest.json    # 0644
  groups_events.jsonl   # 0644
  monitor.lock
```

- `DATA_DIR` **固定**为上述路径，不可配置。  
- 与 Sub2API 站 **禁止** 相同 `site_id` 或相同 resolve 后的数据路径。  
- flock 只防并发，**不能**替代路径唯一性（安装时扫描全部 env）。

---

## 2. `auth_state.json`

权限 **0600**；读时校验 mode（及可行时的 owner）；原子写。

### 2.1 唯一结构（v1）

```json
{
  "schema_version": 1,
  "saved_at": "2026-07-21T00:00:00Z",
  "user_id": 12345,
  "session": {
    "value": "<secret>",
    "domain": "torchai.ai",
    "path": "/",
    "expires": null
  }
}
```

| 字段 | 说明 |
|------|------|
| `user_id` | 仅 `REQUIRE_NEW_API_USER_HEADER=1` 时必需；正整数 |
| `session` | **唯一** cookie 真相；名固定为 `session` |
| `domain` | 必须与 `MONITOR_BASE_URL` 的 host 匹配（或为其后缀策略中明确允许的 host；v1 建议 **精确匹配 host**） |
| `path` | `/` 或校验后允许 |
| `saved_at` | **凭据最后变化**时间；成功 GET **不要**无意义 touch |

**禁止：** `username`、双份 `cookies` map + `cookie_jar`、任意名 cookie、跨站 domain。

### 2.2 生命周期

```text
restore → 注入 Session（仅 session cookie）
login 成功 → 立即原子保存
groups 响应若 Set-Cookie 轮换 session → 更新 auth_state
401 恢复 → 清 session 字段后重登再写
文件损坏 → 重登；日志不输出内容
```

---

## 3. 上游 groups 原始形态

```json
{
  "success": true,
  "data": {
    "Codex-Plus": { "desc": "…", "ratio": 0.08 }
  }
}
```

- 键 = 分组名（stable id 来源）。  
- `ratio` 须通过 §4.2 校验；`"自动"` 等 → **整包** contract 失败（v1 不支持标签倍率）。  
- 整包失败比跳过单条安全（避免假 removed）。

---

## 4. 规范化 group

```json
{
  "id": "Codex-Plus",
  "name": "Codex-Plus",
  "rate_multiplier": 0.08,
  "description": "目前性价比之选 plus 号池"
}
```

| 字段 | 规则 |
|------|------|
| `id` / `name` | 字典键字符串（可含 emoji） |
| `rate_multiplier` | 有限非负 float |
| `description` | `desc`；缺失/null → `""` |
| **无 `status`** | 上游无此字段；**禁止**虚构 `active` 并进 hash |

列表按 `id` **字典序**排序。

### 4.2 ratio 校验

```text
拒绝 bool（bool 是 int 子类）
接受 int / float / 十进制数字字符串
转换后 math.isfinite(value) 且 value >= 0
0 合法
```

失败 → 整包 contract，**不**写 latest。

### 4.3 content_hash

对规范化 groups 列表（仅 `id,name,rate_multiplier,description`，键排序）做紧凑 JSON + SHA-256，前缀 `sha256:`。  
**不纳入：** `fetched_at`、auth、status。

---

## 5. `groups_latest.json`

```json
{
  "schema_version": 1,
  "site_id": "botcf",
  "backend": "newapi",
  "fetched_at": "2026-07-21T00:00:00Z",
  "count": 1,
  "content_hash": "sha256:…",
  "groups": [ /* 已排序 */ ]
}
```

- 失败 **不**覆盖。  
- 同 hash 成功：仍可更新 `fetched_at`（元数据刷新）。  
- 读取已有 latest：若存在 `site_id`/`backend` 且与当前配置不符 → **硬失败**，不 diff。  
- 历史缺失 `backend`：仅当明确迁移工具处理；New-API 写入必须始终带 `backend`。

消费方 **只看 `backend` 字段**，禁止用 id 是否像数字来猜类型。

---

## 6. Diff

```text
added    = new_ids - old_ids
removed  = old_ids - new_ids
modified = intersection 中规范化内容 JSON 不等
```

名称变更 = remove 旧名 + add 新名（可接受）。

---

## 7. `groups_events.jsonl`（审计语义）

### 7.1 尾事件去重（修正 P0-2）

**禁止**扫描全文件“是否出现过该 hash”（会吞掉 `A→B→A` 的恢复）。

```text
if latest.content_hash == new_hash:
    只刷新 latest.fetched_at（可选写 latest）
else:
    last = 最后一条完整 JSONL 事件（跳过尾部半行）
    if last is None or last.after_hash != new_hash:
        append 新事件 + fsync
    atomic replace latest
```

崩溃：event 已写、latest 未写 → 重跑时 `last.after_hash == new_hash`，不重复 append，只补 latest。

半行尾：截断到最后一个 `\n` 或将损坏文件隔离后重建策略；**禁止**在半行后续写。

### 7.2 事件字段

```json
{
  "schema_version": 1,
  "site_id": "botcf",
  "backend": "newapi",
  "observed_at": "2026-07-21T00:00:00Z",
  "event": "groups_changed",
  "before_hash": "sha256:…",
  "after_hash": "sha256:…",
  "added": [
    { "id": "new", "name": "new", "rate_multiplier": 0.2, "description": "" }
  ],
  "removed": [
    { "id": "old", "name": "old", "rate_multiplier": 0.3, "description": "…" }
  ],
  "modified": [
    {
      "id": "Codex-Plus",
      "before": { "rate_multiplier": 0.08, "description": "…" },
      "after": { "rate_multiplier": 0.1, "description": "…" }
    }
  ]
}
```

`initial`：

- `event`: `"initial"`  
- `before_hash`: `null` 或省略  
- `after_hash`: 当前 hash  
- **`added`：完整初始组对象列表**（测试锁死；非空 diff 三字段中 removed/modified 为空）

两站分组量级小；before/after 空间可接受。

### 7.3 写入顺序

1. 判定变化 + 尾事件去重  
2. append event + fsync（若需）  
3. atomic replace latest  

### 7.4 Retention

**v1 不做**在线 prune / `EVENTS_RETENTION_DAYS`。  
体量问题出现后用独立维护命令处理。

---

## 8. 与 Sub2API 共存

| | Sub2API（现状） | New-API（本方案） |
|--|-----------------|-------------------|
| auth 文件 | `token.json` | `auth_state.json` |
| backend 字段 | 历史可能缺失 | **必须** `newapi` |
| events 去重 | 全文件 hash（有 bug） | **尾事件**；勿复制旧 bug |
| status | 常有上游字段 | **不写** |

未来共享 storage 时：以本文件尾事件语义为准修 Sub2API。

---

## 9. 安全

| 项 | 要求 |
|----|------|
| auth_state | 0600；读时校验 |
| env | 0600；gitignore |
| 日志 | 禁止 session value、密码、代理凭据 |
| 文档 | 手册占位符；真实凭据不入库 |

---

## 10. 版本

| 版本 | 说明 |
|------|------|
| v1 | 修订稿：尾事件、审计 diff、单一 session、无 status、无 prune |
