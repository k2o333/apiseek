# New-API 令牌分组采集 — 方案目录

> **正式替代入口：** [New-API groups](../../03%20designs/newapi-monitor.md)、[New-API models](../../03%20designs/newapi-models.md) 与 [正式契约](../../02%20specs/README.md)。本目录保留历史架构、评审、站级探针和实施清单。

本目录描述如何把 **BotCF / TorchAI**（已探活的 **legacy session** 协议）纳入仓库监控体系。

与现网 **Sub2API**（`sub2api_monitor.py` + `sub2api-monitor-once@`）是 **另一类协议**，共用「每站 env + data + timer oneshot」运维壳，**不共用** JWT Bearer 登录链路。

| 文档 | 说明 |
|------|------|
| [architecture.md](./architecture.md) | **主方案**（可执行；已吸收 design-review） |
| [data-model.md](./data-model.md) | 快照、events（尾事件去重 + before/after）、auth 状态 |
| [timer-units.example.md](./timer-units.example.md) | systemd oneshot / timer 蓝本 |
| [site-notes.md](./site-notes.md) | BotCF / TorchAI 站级差异与探活 |
| [implementation-checklist.md](./implementation-checklist.md) | 实现与验收勾选 |
| [design-review.md](./design-review.md) | 评审原文（历史；冲突以 architecture 为准） |
| [review-adoption.md](./review-adoption.md) | 评审意见采纳表 |
| [get-models/requirements.md](./get-models/requirements.md) | **Token 覆盖 + 模型采集**详细需求（草案） |
| [get-models/touch_ai.md](./get-models/touch_ai.md) | torchai 探针手记 |

**一句话：**  
只做两个已探活站的 **可靠采集器**（非通用 New-API 框架）；timer + oneshot；session 复用；规范化快照；events 可审计。

**实现状态（2026-07-21）：** 代码与 unit 已落地；`botcf` / `torchai` 真实采集与 timer 已启用。入口 `newapi_monitor.py`、存储 `monitor_storage.py`。

**依据手册：** `docs/websites/botcf.md`、`torchai.md`（凭据已改为占位符；真实账号只在 `sites/*.env`）。

**Sub2API 对照：** [正式实现设计](../../03%20designs/sub2api-monitor.md)
