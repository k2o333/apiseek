# design-review 采纳表

**来源：** [design-review.md](./design-review.md)（2026-07-21）  
**落点：** 修订后的 architecture / data-model / site-notes / timer-units / checklist。

## 保留（评审 §1）

| 意见 | 落点 |
|------|------|
| 独立协议入口 | architecture §1、§3 |
| timer + oneshot | architecture §1、§6 |
| 失败不覆盖 latest | data-model、architecture §4 |
| 每站凭据/状态/flock | architecture §3、§5 |
| session 持久化 | data-model §2 |
| 原子 latest + 事件先于 latest（算法已修正） | data-model §7 |
| 不做浏览器/Turnstile 破解 | architecture §1、§4 |

## P0 — 全部采纳

| # | 意见 | 落点 |
|---|------|------|
| P0-1 | 手册明文凭据；轮换；脱敏；扫描；auth 不存 username | architecture §2 安全闸门；websites 手册占位符；checklist |
| P0-2 | 全文件 hash 去重错误 → **仅尾事件**；A→B→A 测试 | data-model §7；architecture §8 测试 |

## P1 — 全部采纳（有取舍处见备注）

| # | 意见 | 落点 |
|---|------|------|
| P1-1 | 收窄为 BotCF/TorchAI legacy session；非全系通用 | architecture §1、§2；site-notes |
| P1-2 | login/groups 成功契约 + 按动作分类失败 | architecture §4.2–4.3 |
| P1-3 | 删除静态 TURNSTILE_VALUE | architecture §4.2、§4.1 env |
| P1-4 | 单一 session 结构；REQUIRE_NEW_API_USER_HEADER | data-model §2；architecture §4.1 |
| P1-5 | 禁止跟随 redirect；BASE 纯 origin | architecture §4.1、§4.5 |
| P1-6 | 删除虚构 status=active | data-model §4 |
| P1-7 | ratio 有限非负数值校验 | data-model §4.2 |
| P1-8 | events 保存 before/after（审计语义） | data-model §7 |
| P1-9 | 显式 schema_version + backend | data-model §5、§9 |
| P1-10 | 更短 timeout；最多 2 次；monotonic deadline | architecture §4.4、§6 |
| P1-11 | 固定 DATA_DIR 派生；site_id 三一致 | architecture §4.1、§5 |
| P1-12 | data root 对齐 ReadWritePaths；UMask；root 风险声明 | timer-units；architecture §6 |
| P1-13 | 明确采集器 + freshness 规则 | architecture §6.4、§8 |

## P2 — 采纳

| # | 意见 | 落点 |
|---|------|------|
| P2-1 | v1 删除在线 retention/prune | data-model；architecture |
| P2-2 | initial.added = 完整初始组 | data-model §7 |
| §6 精简 env / 无 run_loop / 短脚本 / 摘要日志 / 单文件+可选 storage | architecture §3–§4、§6 |

## 刻意不采纳或延后

| 项 | 说明 |
|----|------|
| 立即 `git filter-repo` 清历史 | **文档要求列为开工阻断操作**；是否执行取决于仓库是否曾外传，由运维决定，方案不替运维强制改写远端。 |
| 专用系统用户 + `/var/lib` 迁移 | 记为 **已接受债务 / 后续**；首版仍用仓库 `data/` + root 布局（与现 Sub2API 一致），并在 architecture 写明风险。 |
| 立刻修复 Sub2API 全局 hash 去重 | New-API **不得复制**该 bug；Sub2API 迁移共享 storage 时一并修（architecture 升级路径）。 |
| 新建告警平台 | 只定义 freshness 规则与 journal 钩子，不新建服务。 |

冲突时以 **architecture.md + data-model.md** 为准，不以 design-review 原文为准。
