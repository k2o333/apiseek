# 评审意见采纳矩阵（含二次评审）

## 一、review-and-alternative-architecture.md

| 意见 | 采纳 | 落点 |
|------|------|------|
| timer + oneshot 默认 | ✅ | architecture §1、§3 |
| batch once 升级 | ✅ | §4 |
| 常驻线程不优先 | ✅ | §1、§4.3 |
| 错峰下沉 RandomizedDelaySec | ✅ | timer 蓝本 |
| 保留 Protect* | ✅ | service 蓝本 |
| load_config 纯函数 | ✅ 推荐 / batch 强制 | §5.2 |
| 百站数字不作硬依据 | ✅ | §7 定性 |

## 二、suggestions-for-simplification.md

| 意见 | 采纳 | 落点 |
|------|------|------|
| 砍预支 Supervisor/热加载/health | ✅ | §1 明确不做 |
| 渐进交付 | ✅ | 新名并行迁移 |
| MVP=常驻多线程 | ❌ | 改为 timer+oneshot |
| 不做任何错峰 | ❌ | timer 错峰 |
| 砍 security | ❌ | 保留 |

## 三、二次评审（阻断项）— 全部闭合

| # | 意见 | 采纳 | 落点 |
|---|------|------|------|
| 1 | `--once` 丢失退避/Retry-After | ✅ **有界进程内重试**，禁止静默回退 | architecture §2、§5.1 |
| 2 | timer `Requires=` 惊群 | ✅ **删除 Requires=** | timer 蓝本 |
| 3 | AccuracySec 默认 1min | ✅ **AccuracySec=1s** | timer 蓝本 |
| 4 | TimeoutStartSec=120 过紧 | ✅ **240s** + 预算公式 | service 蓝本 §3.2 |
| 5 | POLL_INTERVAL 被忽略不告警 | ✅ 安装校验 / drop-in | §3.4 |
| 6 | 原地替换难回滚 | ✅ **`sub2api-monitor-once@`** 新名 | §3.5、§8 |
| 7 | Persistent / 误导 Install | ✅ 删除 | timer/service 蓝本 |

## 四、实现进度

- [x] `--once` 有界重试实现 + 单测（`GroupMonitor.run_once` / `OnceBoundedRetryTests`）
- [x] 仓库内 once service/timer 文件（`sub2api-monitor-once@.{service,timer}`）
- [x] install 脚本：周期一致性、与 simple 互斥、回滚命令（`install_service.sh`）
- [x] 超时杀进程后锁可再获取（单测：fd 关闭释 flock；生产路径靠内核行为）
- [ ] 真实 timer 路径双站双周期验证（运维验收；`./install_service.sh` 试点后观察 ≥2 周期）

代码与 unit 闸门（§5.1、§9.1 自动化）已落地；**生产切换**仍须按 architecture §8 试点并完成 §9.2 现场验收后，方可宣称无功能回退上线。
