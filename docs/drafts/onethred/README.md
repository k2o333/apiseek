# 多站点高效运行 — 方案索引

## 唯一执行方向（请先读）

| 文档 | 内容 |
|------|------|
| **[final/architecture.md](./final/architecture.md)** | **最终架构**：timer + oneshot，不常驻多线程 |
| [final/adoption-matrix.md](./final/adoption-matrix.md) | 两份评审的采纳/不采纳表 |
| [final/timer-units.example.md](./final/timer-units.example.md) | service/timer 示例 |
| [final/README.md](./final/README.md) | final 目录说明 |

**结论：** 配置仍是 `sites/<id>.env` + `data/<id>/`；调度用 **`sub2api-monitor-once@` timer + oneshot**；应用 **`--once` 有界重试**（保留退避/Retry-After）。空闲无 Python 常驻。二次评审七项已写入 final；**代码未做完前不可称无功能回退上线**。

## 历史材料（冲突时以 final 为准）

| 文档 | 内容 |
|------|------|
| [review-and-alternative-architecture.md](./review-and-alternative-architecture.md) | 评审与 timer/batch 替代 |
| [suggestions-for-simplification.md](./suggestions-for-simplification.md) | 简化/MVP 建议 |
| [sub2api-multithread-multi-site.md](./sub2api-multithread-multi-site.md) | 原「常驻多线程」草案（已降级） |
| [resource-model.md](./resource-model.md) | 旧资源估算（部分公式已被评审否定） |
| [migration-from-template-units.md](./migration-from-template-units.md) | 旧「迁到多线程」步骤（已被 final 架构 §8 替代） |
| [cli-and-api-sketch.md](./cli-and-api-sketch.md) | 旧多站 CLI 草图 |
