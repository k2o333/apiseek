# 最终方案（修订后）

本目录是 `docs/drafts/onethred/` 的**唯一执行方向**。

| 文档 | 说明 |
|------|------|
| [architecture.md](./architecture.md) | 主方案（含二次评审七项闭合） |
| [timer-units.example.md](./timer-units.example.md) | `sub2api-monitor-once@` service/timer 蓝本 |
| [adoption-matrix.md](./adoption-matrix.md) | 一/二次评审采纳表 |

**一句话：**  
systemd **timer + oneshot**；应用 **`--once` 有界重试**（保留退避/Retry-After）；unit **新名并行**可回滚；**无**常驻多线程 Supervisor。

**上线闸门：** architecture §2、§5.1、§8、§9 全部落地并验证后，才可切换生产。
