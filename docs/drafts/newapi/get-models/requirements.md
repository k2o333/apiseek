# New-API get-models — 详细需求（评审修订定稿）

> **状态：** 需求已吸收 [review.md](./review.md)（2026-07-22）P0/P1；**P0 闭合前不编码 create/models 写路径**。  
> **唯一权威：** 本文。不另写重复 design/data-model；timer 细节可薄摘到 unit 示例。  
> **探针：** [touch_ai.md](./touch_ai.md) — **仅 torchai rc.21 正常路径**；凭据已脱敏。其他站须重新 preflight。  
> **对标：** Sub2API `docs/drafts/get-models/` 的调度/状态/锁经验；**Token 协议层按 New-API 特有契约实现，禁止照搬 usable_keys 而不 hydration。**  
> **现网：** `newapi_monitor.py` 已采 groups；入口默认 **单轮** groups（**无** `--once` 旗标）。

---

## 0. 一句话

> 分组高频只读；Token 覆盖 + 模型低频。Session 管理面与 API Key 模型面分域。  
> **默认零远端写。** 须 **seed Token + preflight + 显式 bootstrap**。  
> list 后 **先 hydration secret** 再算 Missing；仅 **inventory-suitable** 算覆盖；managed 可修复、用户 Token 永不改删。  
> models 失败保旧、逐组 checkpoint；refresh **必须** 已 bootstrap；日更有界等锁。

---

## 1. 目标、非目标与安全不变量

### 1.1 目标

| ID | 目标 | 验收要点 |
|----|------|----------|
| G1 | torchai（及 preflight 过的 New-API 站）可 bootstrap：每组有 inventory-suitable 覆盖或可解释 error | preflight **零写**；bootstrap×2 第二次 create=0 |
| G2 | 落盘模型结果 | 仅 `models_latest.json` + `models_events.jsonl` |
| G3 | 保留现网 groups 单轮 + timer 语义 | 不改默认 CLI；与 models daily 抢锁可跳过 1 轮 groups |
| G4 | 模型低频 | 真新组 T-new（可选）/ 日更 / 显式 bootstrap |
| G5 | 人为删 Key、同组多 Key | 全量 ensure 可恢复；**不删**用户 Token；重复允许 |
| G6 | 快照表示 **inventory-suitable 视角下的完整模型列表** | 不用受限 Token 子集冒充全量 |

### 1.2 非目标

- 用量/余额/completion；SQLite；跨协议框架；`keys_index`；默认 secret 落盘  
- **自动删除** 冗余 Token；修改 **用户** Token  
- 零 Token 账号自动首创（见 §3.4）  
- 同日 00:30 backup timer；models 侧 30 天 prune 状态机  
- 为未探针站预置大量 Token 字段 env  

### 1.3 安全不变量

1. 凭据仅 `sites/*.env` + `auth_state.json`（0600）。  
2. models / events / 汇总 **禁止** 完整 key / session。  
3. 日志 mask。  
4. cold 默认 groups 轮次：**零 create、零 models 请求**。  
5. `/v1/models` 401 **禁止** session 重登。

---

## 2. torchai 已验证协议契约（首版固定）

> 未在本表写死的字段：须在 **只读脱敏探针**（§6）补齐后再 create。第一版 **只服务 torchai 已验证载荷**；botcf 独立 preflight 后仅加真实差异的最小配置。

### 2.1 与 Sub2API 差异

| 维度 | New-API (torchai) |
|------|-------------------|
| 管理鉴权 | `session` Cookie + `new-api-user: <uid>` |
| 分组 | `GET /api/user/self/groups` → `data` **object**，键=组名；规范化 `id=name`、`rate_multiplier=ratio` |
| Token 列表 | `GET /api/token/?p=&size=` → `data.items`；`key` **脱敏** |
| 创建 | `POST /api/token/` 一次可带 `group`（名称字符串） |
| 明文 | `POST /api/token/{id}/key` → `data.key`（常无 `sk-` 前缀） |
| 模型 | `GET /v1/models`，`Authorization: Bearer <plaintext>` |
| 入口 | `newapi_monitor.py` + `newapi_models.py`（Token 适配层） |
| 鉴权持有 | **monitor 持有 Session / user_id / 一次性恢复**；models 模块只注入 list/create/update/get-secret |

### 2.2 身份与命名

```text
norm_group(x) = str(x).strip()
managed_token_name(g) = "newapi-monitor:g:" + norm_group(g)
```

- map 键、比较一律 `norm_group`。  
- managed **精确匹配** name；用户 Token（任意 name）永不修改/删除。  
- **名称长度/唯一性：** create 前用探针确认上限；超长则 `managed_token_name` 改为前缀 + 短 hash/代次（并 **承认** 不能保证同名 managed ≤1），记入站契约。

### 2.3 inventory-suitable Token（替代模糊 usable）

Token 计入覆盖 / 阻止 create managed / 用于 inventory models，**必须同时**：

| 条件 | 说明 |
|------|------|
| 已绑目标组 | `norm_group(token.group) == g` |
| secret 可回读 | 本轮 `get_token_secret(id)` 成功且非空（**列表掩码不算**） |
| 启用 | `status` 语义以探针表为准（缺字段 → **不得**默认“可用”，由 preflight 契约确认） |
| 未过期 | `expired_time` 语义以探针为准；`-1` 等“无限期”须写死 |
| **无模型白名单** | `model_limits_enabled == false`（字段缺失 → 不当作 inventory-suitable，除非 preflight 证明缺省=false） |
| IP 限制 | `allow_ips` 空/未限制才可；非空 → 不适合 inventory（除非 preflight 证明本机必过） |

排序：`norm_id(token.id)` 整数优先稳定序。  
`pick` = 第一个 inventory-suitable；models 401/403 试下一个 **inventory-suitable**。

**仅 inventory-suitable 业务 Token 才能使该组不 Missing。** 受限 Token 返回的 `/v1/models` 子集 **不得** 写入为“全量成功快照”（见 §4）。

### 2.4 分页 `list_tokens_all`

1. `p=1` 起循环；`size` 默认 100；**`max_pages` 上限** + 重复页/无进展保护。  
2. 结束顺序（fail-closed，同 Sub2API 修复）：  
   - `has_more is True` → 继续（即使短页）  
   - `total` 存在且 merged 不足 → 继续；短页仍不足 → `paging_complete=False`  
   - 再才可用短页/空页/`has_more=False`/`len>=total` 证明 complete  
3. incomplete → **禁止任何 create**。

### 2.5 create 载荷（torchai 探针固定，首版不拆 env）

```json
{
  "name": "<managed_token_name>",
  "remain_quota": 0,
  "expired_time": -1,
  "unlimited_quota": true,
  "model_limits_enabled": false,
  "model_limits": "",
  "allow_ips": "",
  "group": "<group_name>",
  "cross_group_retry": false
}
```

PUT 修复 managed 的载荷：以只读探针确认后写入实现表（§6）；用户 Token **禁止** PUT。

### 2.6 HTTP / 业务失败

- 管理面：复用现网对 **HTTP 401 与 200+`success=false` /「请先登录」/「未提供 New-Api-User」** 的分类；**一次** session 恢复预算。  
- `success=false` 不得当成功 create。  
- models：仅 Key 域错误；禁止 session 恢复。

---

## 3. 四条流程与门禁

### 3.1 Secret hydration（P0-1，先于 Missing）

```text
list_tokens_all() → paging 完整
→ 对本轮相关、可能可用的 Token 执行 get_token_secret(id)
→ 得到内存 hydrated_tokens（永不落盘 secret）
→ 再算 inventory coverage / Missing
```

| 情况 | 动作 |
|------|------|
| 业务 Token 回读 secret 成功且 inventory-suitable | 组 **不** Missing；**不 create** |
| secret 获取 timeout/5xx/契约不明/分页漂移 | 该组 **`coverage_unknown`** → **fail closed，不 create**（≠ “不存在”） |
| create 后 | 必须 re-list + 取新 Token secret → `tokens_after` hydrated 再交给 models |

### 3.2 preflight（只读，零 create）

| 检查 | 要求 |
|------|------|
| session + user_id | 可恢复/登录 |
| groups | 成功；**空 object 失败**（与现网 `normalize_groups_dict` 一致，不覆盖旧 groups） |
| ratio | 有限非负 float；整包 contract |
| token 分页 | complete |
| **seed Token** | 至少一个 Token；可 `get_token_secret`；建议 inventory-suitable（运维准备） |
| models | 用 seed secret 验证 `/v1/models` envelope |

**零 Token 站：** preflight **失败**。第一版 **不** 零写验证下自动首创；运维先人工建一个未受限 seed Token。

```bash
python newapi_monitor.py --env-file sites/torchai.env --models-preflight
```

### 3.3 默认 groups 轮次（现网默认，无 models flag）

```text
非阻塞锁 → ensure session → get_groups → persist
若 MODELS_INCREMENTAL=1 且 bootstrap_completed_at:
  added = 本轮真正新增组名
  refresh_set = { g in added | should_attempt_now }
  ensure_coverage(refresh_set only)   # 禁止对全量 G ensure
  refresh_models(refresh_set)
T-new 部分失败 → exit 0
```

- **不**新增无价值的 `--once` 旗标。  
- 无 `bootstrap_completed_at` 或增量关：**零** create、**零** models。

### 3.4 bootstrap

```text
有界等锁 → ensure session → preflight 强制
→ re-GET groups → list + hydrate → ensure_coverage(全量 G)
→ refresh_models(全量 G) → full meta；全成功则 bootstrap_completed_at
```

### 3.5 models-refresh / daily

```text
若无 bootstrap_completed_at → exit ≠0，零 create、零 models 写盘
否则同 bootstrap 的 re-GET + ensure 全量 + models（preflight 可短检 secret 仍可读）
```

**禁止** 未 bootstrap 用 refresh 绕过首次门禁。

### 3.6 reconcile_token_for_group（managed only）

对 `g`（已 hydrate 后仍 Missing 且非 coverage_unknown）：

1. 已有 **inventory-suitable** → 返回 created=false  
2. 存在 managed 未绑 / 绑本组但 **不 suitable**（禁用、过期、limits、无 secret）→ **仅对 managed** PUT 修复：重绑本组、启用、无限期、`model_limits_enabled=false`、清 allow_ips（以探针为准）；再 list+hydrate 验证  
3. managed 被改绑到**其他非空组** → PUT **改回** 本组（仅 managed 名精确匹配）；用户 Token 不动  
4. 无 managed → POST create（§2.5）；未知结果 **禁止盲重 POST**，re-list 按名认领  
5. 始终返回 hydrated `tokens_after`

上游若禁止修复 → 站契约改用 hash 代次名，并文档声明 managed≤1 不保证。

### 3.7 ensure_coverage

- incomplete paging 或任一相关组 `coverage_unknown` 且需 create → **abort create**（该组/本批策略：unknown 组跳过 create，记 error）  
- 只对 Missing 调 reconcile  
- **T-new：仅 refresh_set；bootstrap/daily：全量 G**  

### 3.8 refresh_models_for_groups

- 仅使用 inventory-suitable 候选  
- 串行；逐组 checkpoint；deadline 停止开新组  
- **`target = ok + failed + skipped`；`skipped>0` 或 failed>0 → 不更新 `last_full_success_at`，exit ≠0**  
- `last_full_result` 固定含 `{target, ok, failed, skipped}`（skipped 单独字段，不与 failed 混写两种 schema）  
- models 规范化：id `str.strip`、去空、去重、**稳定排序** 后 hash；合法 `data:[]` → `models:[]` 成功；不可识别 envelope → contract  

### 3.9 重试（第一版可验收）

| 路径 | 规则 |
|------|------|
| T-new | 每新组 **最多尝试一次** |
| 429 | 用 `Retry-After` 写 `next_retry_at` |
| timeout/5xx | 本轮该组失败；**不**组内循环重试；交给 daily/显式 refresh |
| contract / no_usable_key / coverage_unknown | 冷却至 daily 或显式 refresh |
| daily | **不做** 逐组失败循环重试 |

### 3.10 锁与 CLI

| 命令 | 锁 | 备注 |
|------|-----|------|
| 默认 groups | 非阻塞 | 现网默认 |
| `--models-preflight` | 非阻塞即可 | 零写 |
| `--models-bootstrap` / `--models-refresh` | 有界等待 + 有限重试 | 互斥三旗标 |

```bash
--models-preflight | --models-bootstrap | --models-refresh   # 互斥
# 无 models flag → 现网 groups 单轮（不要 --once）
```

| 退出码 | |
|--------|--|
| groups 失败 | ≠0 |
| groups 成功 + T-new 部分失败 | **0** |
| bootstrap/refresh 部分失败或 skipped>0 或未 bootstrap 跑 refresh | **≠0** |
| preflight 失败 | ≠0 |

日更 unit 建议：`newapi-models-daily@`，`OnCalendar=*-*-* 00:00:00 Asia/Shanghai`，`RandomizedDelaySec=300`，`TimeoutStartSec=600`，`ExecStart=... --models-refresh`。install **不默认 enable**。

---

## 4. models 状态语义（引用 Sub2API schema）

与 Sub2API `models_latest` **schema_version=1** 同构：

- 键 = `norm_group`（组名字符串）  
- **成功/尝试正交**：失败不改 `models` / `content_hash` / `last_success_at` / 成功 `key_id`  
- `null` = 从未成功；`[]` = 成功空列表  
- 顶层：`bootstrap_completed_at`、`last_full_attempt_at`、`last_full_success_at`、`last_full_result{target,ok,failed,skipped}`  
- **不复制** groups 的 ratio/desc  

| 失败 kind | create | models 写入 |
|-----------|--------|-------------|
| paging_incomplete | 禁 | 不刷全量成功 meta |
| coverage_unknown | 禁 | attempt error；保旧 models |
| no inventory-suitable | ensure 可补 managed | 或 `no_usable_key` |
| key_auth | — | 试下一 suitable |
| contract envelope | — | 不写假 [] 当成功 |

**完整性（bootstrap/refresh 全成功后）：**

```text
∀ g ∈ groups_latest:
  models_by_group[g].models is not null
  且所用 key 满足 inventory-suitable（记录 key_id）
```

人为删 Key：旧 models 可保留；**一次 refresh**（已 bootstrap）应 re-ensure + 重采。

---

## 5. 验收与测试（驱动 shipped 代码）

### 5.1 必须单测

| # | 用例 |
|---|------|
| 1 | 已有脱敏业务 Token，hydrate 后 inventory-suitable → **create=0** |
| 2 | secret 临时失败 → **coverage_unknown，create=0** |
| 3 | create 后 re-list+hydrate 可用于 models |
| 4 | `model_limits_enabled=true` **不**阻止 Missing；不得用其 models 子集标全量成功 |
| 5 | 分页 incomplete / has_more+短页 / total 不足 → 不 create |
| 6 | managed 禁用/过期/错绑 → PUT 修复路径（mock）；用户 Token 不 PUT |
| 7 | POST 未知结果 → re-list 认领，盲重 POST=0 |
| 8 | preflight 零 Token 失败；refresh 无 bootstrap → 零写 exit≠0 |
| 9 | models 401 不 session login；null vs []；checkpoint；deadline skipped 进 result 且 exit≠0 |
| 10 | cold 默认 groups：零 create 零 models；T-new 只 ensure refresh_set |
| 11 | 日志/落盘无完整 secret/session |

### 5.2 DoD

1. torchai：seed + preflight + bootstrap 后每组 models 非 null（或可解释 error 且不抹成功）。  
2. 二次 bootstrap create=0。  
3. 默认发布不写 Token/models。  
4. 受限 Token 不冒充全量 inventory。  
5. 删 Key 后 refresh 可恢复。  
6. groups 空 object 行为与现网一致（失败、不覆盖）。  
7. newapi groups 回归 + 新 models 测试绿。

### 5.3 编码顺序（评审 §6）

1. 只读探针补齐 envelope/status/expiry/limits/PUT/name 限制 → 写入附录或 site-notes  
2. hydration + inventory-suitable + fail-closed  
3. preflight（含 seed）  
4. managed reconcile 故障测试  
5. ModelsStore + bootstrap/refresh 门禁  
6. daily timer；T-new 默认关  

---

## 6. 编码前只读探针清单（P0-5，阻断 create）

在任意自动 create 前，对 torchai 做一次 **只读、脱敏** 探针并落成契约表：

- [ ] token list 完整 envelope、分页参数名、页码起点、`total/has_more`  
- [ ] `status` 枚举；`expired_time` 单位与 `-1`  
- [ ] `model_limits_enabled` / `allow_ips` / 其他限制字段  
- [ ] HTTP 200 + `success=false` 语义  
- [ ] PUT 能否修复 managed（重绑/启用/关 limits）  
- [ ] Token name 长度与唯一性  

未勾选完成：**禁止** 合并 create 代码到生产路径。

---

## 7. 决策摘要（评审采纳）

| 项 | 定稿 |
|----|------|
| P0-1 hydration 后算 Missing | **采纳** |
| P0-2 inventory-suitable | **采纳** |
| P0-3 managed 修复 / 可选 hash 名 | **采纳** |
| P0-4 seed Token；refresh 须 bootstrap | **采纳** |
| P0-5 探针契约 | **采纳**（编码门禁） |
| P1-1 不引入 `--once` | **采纳** |
| P1-2 空 groups 失败 | **采纳** |
| P1-3 管理认证复用 monitor Session | **采纳** |
| P1-4 skipped 计入 full result | **采纳** |
| P1-5 models 规范化 | **采纳** |
| P1-6 简化重试 | **采纳** |
| P1-7 T-new 只 ensure refresh_set | **采纳** |
| 文档收敛 | 本文为唯一权威；不重复造 design 厚本 |

---

## 8. 端到端数据流（简图）

```text
[默认] groups 单轮 → groups_latest
[可选 T-new] hydrate → ensure(refresh_set) → models 增量
[bootstrap/refresh] re-GET groups → list → hydrate → ensure(G)
  → for g: suitable secret → GET /v1/models → checkpoint models_latest
```

**产出：** 完整分组（已有）+ 每组 inventory 模型列表（本需求）+ 可选只读 join 汇总表。
