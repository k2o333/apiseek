# 分组倍率规范化 — 主方案

> 状态：implemented（2026-07-23）  
> 实现落点：`monitor_rates.py`、`sub2api_monitor.py`、`newapi_monitor.py` / `monitor_storage.py`、站级 `sites/<id>.env`  
> 正式设计入口：`docs/03 designs/sub2api-monitor.md`、`newapi-monitor.md`

## 1. 问题

1. 分组采集已有 `rate_multiplier`（远端原样），但 **业务含义因站而异**。  
2. **pinaic / hubway**：站方习惯倍率 = 远端值 **÷ 10**（例：`16` → `1.6`）。  
3. 该换算必须 **可配置**，不能写死在代码分支名里。  
4. 换算后的倍率必须与 **同一轮** 的分组 id/name/status 等 **同快照对应保存**。

非目标：改远端计费；替代 `get-models`；为每个模型单独倍率表。

## 2. 原则

| # | 原则 |
|---|------|
| P1 | **Raw 权威**：`rate_multiplier` 继续表示 **provider 返回值**（或 New-API 规范化后的 `ratio` 映射值），用于 diff/hash 与远端对照。 |
| P2 | **Effective 派生**：业务倍率只由 `raw` + 站配置算出，禁止手填覆盖。 |
| P3 | **站级配置**：换算规则放在 `sites/<id>.env`，与 `DATA_DIR`/凭证同生命周期。 |
| P4 | **同轮同对象**：effective 写在每个 group 记录上，不另开「只含倍率」的平行文件做真相源。 |
| P5 | **失败保旧**：poll 失败不覆盖 `groups_latest`（沿用现监控语义）。 |
| P6 | **无新调度**：不增加 timer；挂在现有 ~4–5 分钟 oneshot 成功路径。 |

## 3. 配置

### 3.1 环境变量

| 变量 | 类型 | 默认 | 含义 |
|------|------|------|------|
| `MONITOR_RATE_DIVISOR` | 正有限浮点 | `1` | 业务倍率 = `raw / divisor` |

校验（`--validate` 与启动时）：

- 缺省或空 → `1`  
- 必须可解析为 float，且 `> 0`、有限（非 NaN/Inf）  
- 非法 → `ConfigError`，exit 2（与其它 env 错误一致）

**推荐站级取值：**

| site_id | `MONITOR_RATE_DIVISOR` | 说明 |
|---------|------------------------:|------|
| `pinaic` | `10` | 例 raw `16` → effective `1.6` |
| `hubway` | `10` | 同 pinaic |
| 其余站 | 省略或 `1` | 显示即业务倍率 |

> 注意：用户笔误 `pianic` → 仓库站点 id 为 **`pinaic`**。

### 3.2 示例（env 片段）

```bash
# sites/pinaic.env
MONITOR_RATE_DIVISOR=10

# sites/hubway.env
MONITOR_RATE_DIVISOR=10

# sites/aiapibank.env
# 可不写；默认 1
# MONITOR_RATE_DIVISOR=1
```

### 3.3 为何用 divisor 而不是 scale

- 运维语义对齐需求原文「除以 10」。  
- `MONITOR_RATE_SCALE=0.1` 与 `DIVISOR=10` 数学等价；首版只支持 **一个** 旋钮，避免双源。  
- 若未来要「乘系数」，再引入互斥的 scale 或统一为 affine `a*x+b`；**首版不做**。

### 3.4 不把表写死在代码

允许代码内 **文档化推荐表**（README / installer 注释），但 **运行时只读 env**。  
禁止：`if site_id in ("pinaic", "hubway"): divisor = 10` 作为唯一配置源。

## 4. 计算语义

对单条 group，在 **规范化入库前** 应用：

```text
raw = group.rate_multiplier   # 已存在；缺失见 §4.2
divisor = config.rate_divisor # float > 0
effective = raw / divisor     # 仅当 raw 为有限数字
```

### 4.1 数值与精度

- `raw` 与 `effective` 均以 JSON number 落盘（Python float）。  
- **不**强制四舍五入到固定小数位；展示层可自行 format。  
- 若需稳定 hash：对 effective 使用与 raw 相同的 JSON 序列化（`sort_keys` / 默认 float 文本），不引入额外 rounding 步。

### 4.2 raw 缺失 / 非法

| 情况 | 行为 |
|------|------|
| `rate_multiplier` 缺失 | contract 失败（Sub2API 现已要求可读该字段；New-API 现已 `parse_ratio`）— **不**发明 0 |
| 非有限数 | contract 失败，不覆盖 latest |
| `divisor` 非法 | 配置失败，本轮不启动 poll |

### 4.3 其它倍率字段（明确边界）

Sub2API 还可能返回：

- `image_rate_multiplier` / `video_rate_multiplier` / `peak_rate_multiplier` …

**首版仅对主字段 `rate_multiplier` 做 effective 派生。**  
不对这些附属字段自动 ÷divisor（语义未统一；可在 P1 用前缀规则扩展）。

## 5. 落盘形状（摘要）

每个 group 对象在写入 `groups_latest.json` 时增加：

| 字段 | 含义 |
|------|------|
| `rate_multiplier` | 远端原始倍率（不变） |
| `rate_multiplier_effective` | `raw / MONITOR_RATE_DIVISOR` |
| （可选快照级）`rate_divisor` | 本轮使用的 divisor，便于审计 |

快照顶层建议增加（见 [data-model.md](./data-model.md)）：

```json
{
  "rate_divisor": 10,
  "groups": [
    {
      "id": 69,
      "name": "CCMAX",
      "rate_multiplier": 16,
      "rate_multiplier_effective": 1.6,
      "status": "active"
    }
  ]
}
```

**对应关系：** 同一数组元素内 `id`/`name` 与两种倍率共址；禁止只写「全站 rates 数组」而无 group 键。

可选只读导出（非真相源）：

```text
docs/websites/table/groups_rates.csv   # 可含 raw + effective 两列
```

由脚本从 latest 生成，不参与 monitor 写路径。

## 6. 数据流（单轮成功路径）

### 6.1 Sub2API（`sub2api_monitor.py`）

```text
load config  (+ parse MONITOR_RATE_DIVISOR)
  -> lock
  -> auth + GET groups
  -> 现有 contract 校验（含 rate_multiplier 可读）
  -> annotate_rates(groups, divisor)   # 新增：写 rate_multiplier_effective
  -> canonicalize + content_hash
  -> diff / events
  -> atomic write groups_latest  (含顶层 rate_divisor)
  -> unlock
```

### 6.2 New-API（`newapi_monitor.py` + `monitor_storage.py`）

```text
normalize_groups(data)  # 已映射 ratio -> rate_multiplier
  -> annotate_rates(..., divisor)
  -> content_hash / diff（见 §7）
  -> persist_success
```

botcf/torchai 默认 divisor=1 时 effective == raw，字段仍写出，便于统一消费。

## 7. Hash、事件与兼容

### 7.1 content_hash

**推荐（首版）：** hash 输入包含 **完整 group 对象**（Sub2API 现状）或 New-API 规范化四字段 **加上** `rate_multiplier_effective`（若四字段模型扩展）。

影响：

- 首次上线启用 effective 字段 → 几乎所有站会打 **一次** 变更事件（可接受；日志标 `rate_schema_upgrade` 可选）。  
- 仅改 `MONITOR_RATE_DIVISOR` 且 raw 不变 → effective 变 → **应**记 modified（业务倍率真的变了）。

**备选（不推荐首版）：** hash 仍只基于 raw，忽略 effective。缺点：改 divisor 无事件，审计弱。

### 7.2 events.jsonl

- Sub2API：现有 modified 多为 id 列表或摘要；保持现有策略，必要时在 summarize 中带上 `rate_multiplier` + `rate_multiplier_effective`。  
- New-API：`diff_groups` 的 before/after 应同时含 raw 与 effective，便于「只改了换算配置」的排查。

### 7.3 旧快照

- 读侧：无 `rate_multiplier_effective` 时，消费者可 **即时** 用顶层/当前 env 的 divisor 回算；monitor 写路径在下一成功轮补齐。  
- **不**做离线批量 rewrite 历史 `groups_events.jsonl`。

## 8. 消费约定

| 用途 | 读哪个字段 |
|------|------------|
| 与站后台/API 对照 | `rate_multiplier` |
| 业务报价、跨站比较、对外报表 | `rate_multiplier_effective` |
| 调试换算 | 顶层 `rate_divisor` + 两字段 |

Join 模型快照时：继续以 `str(id)` 对齐 `groups_latest`；**不要**把 effective 复制进 `models_latest`（沿用 get-models「不复制 group 元数据」）。

## 9. 实现落点（建议）

| 模块 | 改动 |
|------|------|
| `load_config`（两端） | 解析 `MONITOR_RATE_DIVISOR` |
| 纯函数 `annotate_group_rates(groups, divisor) -> groups` | 可测、无 IO |
| 写 latest 前调用 | Sub2API `GroupMonitor` 成功路径；New-API `persist` 前 |
| `sites/pinaic.env` / `hubway.env` | 设 `MONITOR_RATE_DIVISOR=10` |
| `*.env.example` | 注释说明默认 1 与 pinaic/hubway=10 |
| 单测 | divisor 默认；÷10；非法 divisor；hash 随 effective 变 |

可选共享：若两端逻辑完全一致，可抽到小模块（如 `monitor_rates.py`），**首版允许复制 10 行纯函数** 以降低耦合。

## 10. 运维

```bash
# 配置后下一轮 timer 自动带 effective；立刻验证：
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --validate
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --once

# 抽查
.venv/bin/python -c '
import json
from pathlib import Path
d=json.loads(Path("data/pinaic/groups_latest.json").read_text())
print("divisor", d.get("rate_divisor"))
for g in d["groups"]:
    print(g["id"], g["name"], g.get("rate_multiplier"), g.get("rate_multiplier_effective"))
'
```

修改 `MONITOR_RATE_DIVISOR` 后无需重启常驻进程（oneshot 每轮重读 env）。

## 11. 风险与对策

| 风险 | 对策 |
|------|------|
| 下游已把 raw 当业务倍率 | 文档明确双字段；effective 新字段不覆盖 raw |
| 上线首轮 event 风暴 | 可接受；或发布说明「schema 扩展一次」 |
| 误给全站 divisor=10 | env 默认 1；仅 pinaic/hubway 写入 10 |
| peak/image 倍率混淆 | 首版范围写死只处理主 `rate_multiplier` |
| 浮点（`0.1` 展示） | 不额外 round；展示层 format |

## 12. 分阶段

| 阶段 | 内容 | 出口 |
|------|------|------|
| A | 文档定稿（本目录） | 评审无阻塞 |
| B | 纯函数 + 配置 + Sub2API 落盘 + 单测 | pinaic once 可见 16→1.6 |
| C | New-API 对齐 + hubway env | 两端字段一致 |
| D | 导出脚本/表增加 effective 列；正式 design 回写 `docs/03 designs/` | 运维可查 |

## 13. 决议摘要

1. **不**用改写 `rate_multiplier` 的方式「修」pinaic/hubway。  
2. **用** `MONITOR_RATE_DIVISOR`（默认 1；pinaic/hubway=10）。  
3. **同对象** 保存 `rate_multiplier` + `rate_multiplier_effective`。  
4. **挂在** 现有 5 分钟级 groups 成功路径；无新服务。  
5. models / invite 链路 **不**复制该字段；需要时 join groups。
