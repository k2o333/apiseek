# Sub2API 多站点监控 — 最终架构（修订）

**状态：** 可执行设计（已吸收二次评审阻断项；**未完成代码/生产切换前不得宣称无功能回退**）  
**问题：** 一站一常驻进程，站数放大后空闲内存与常驻 unit 成本偏高。  
**原则：** 优雅、简洁、高信息密度；调度在 systemd；业务在短生命周期进程；不扩大故障域；**不静默削弱既有退避语义**。

---

## 1. 结论

| 层级 | 选择 |
|------|------|
| **默认生产形态** | 每站 **`sub2api-monitor-once@%i.timer` + `sub2api-monitor-once@%i.service`（oneshot）** |
| **配置与数据** | 不变：`sites/<id>.env` + `data/<id>/` |
| **应用入口** | `--env-file … --once`，但 oneshot 语义必须是 **「有界重试的一次轮询回合」**，不是「失败立刻退出且丢掉退避」 |
| **调度权威** | **timer**（`OnUnitInactiveSec` + `RandomizedDelaySec` + `AccuracySec=1s`） |
| **与旧 unit** | **新名并行**，验证 ≥2 周期后再 disable 旧 `sub2api-monitor@%i` simple；禁止同站双开 |
| **升级路径** | 有证据后再 **batch once**；常驻多线程 **默认不做** |
| **明确不做** | 应用内 Supervisor/热加载/status 文件/全局 inflight/SQLite/多机分片 |

**一句话：**  
空闲不占 Python 内存；周期与错峰交给 timer；**单次触发内仍尊重 429 Retry-After 与渐进退避（有界）**；迁移可回滚且不覆盖旧 unit 文件。

---

## 2. 二次评审阻断项与闭合方式

| # | 严重度 | 问题 | 最终决策 |
|---|--------|------|----------|
| 1 | **高** | 现网 `--once` 失败直接退出，**不执行** `backoff_delay()` / Retry-After | **不接受静默行为变更**。oneshot 必须走 **有界进程内重试**（§5.1） |
| 2 | **高** | timer `Requires=…service` 会在 enable timer 时立刻拉起 service，绕过 `OnBootSec` | **删除 `Requires=`**；仅 `Unit=`（或默认同名） |
| 3 | **中** | 默认 `AccuracySec=1min` 拉长/合并触发，削弱错峰 | **`AccuracySec=1s`** |
| 4 | **中** | `TimeoutStartSec=120` 可能截断 refresh+login+groups（读超时 60s 时） | **默认 240s**；与最长调用链对齐（§3.2） |
| 5 | **中** | `POLL_INTERVAL_SECONDS` 生产不驱动调度却仍校验，易误配 | **安装/启用时校验与 timer 一致**；不一致则要求 drop-in 或拒绝（§3.4） |
| 6 | **中** | 原地替换 `@.service` 使回滚依赖「找回旧文件」 | **新名 `sub2api-monitor-once@`**，旧 simple 保留至验收后（§8） |
| 7 | **低** | `Persistent=false` 无用；oneshot `[Install] WantedBy` 易被误 enable | **删除二者** |

---

## 3. 目标架构

### 3.1 组件

```text
sites/<id>.env
data/<id>/{token.json, groups_latest.json, groups_events.jsonl, monitor.lock}

python sub2api_monitor.py --env-file sites/%i.env --once
        ▲  ExecStart（短生命周期，进程内有界重试）
sub2api-monitor-once@%i.service    # Type=oneshot；无 [Install]
        ▲  Unit=（无 Requires=）
sub2api-monitor-once@%i.timer      # OnBootSec + OnUnitInactiveSec + 随机 + AccuracySec=1s
```

新站：

```bash
# 1) sites/<id>.env
# 2) validate + 手动 --once
# 3) systemctl enable --now sub2api-monitor-once@<id>.timer
```

### 3.2 Service 模板（语义）

**文件名：** `sub2api-monitor-once@.service`

```ini
[Unit]
Description=Sub2API one-shot group poll (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/root/projects/zhongzhuan
ExecStart=/root/projects/zhongzhuan/.venv/bin/python \
  /root/projects/zhongzhuan/sub2api_monitor.py \
  --env-file /root/projects/zhongzhuan/sites/%i.env --once
# 超时预算：须覆盖「有界重试 + 最坏调用链」，见下方计算
TimeoutStartSec=240
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/root/projects/zhongzhuan/data
# 无 Restart=always：避免认证失败 tight-loop 打登录
# 无 [Install]：禁止 systemctl enable 本 service；只 enable timer
```

**超时预算（默认值推导，可按站 drop-in 加大）：**

```text
单次 HTTP 上界 ≈ CONNECT + READ（例 15+60）
一轮认证恢复 ≈ refresh + login + groups  ≤ 3 × 单次 HTTP 上界
有界重试（见 §5.1）≈ 最多 R 次 poll 尝试 + 其间退避时间上界

TimeoutStartSec ≥ 退避总和上界 + R × 单次调用链上界 + 余量
默认取 240s：覆盖「少量退避 + 2～3 次完整 login/groups」的常见最坏路径；
若 READ_TIMEOUT_SECONDS≥60 且允许更多进程内重试，站专属 override 到 300～360s。
```

**超时终止与锁：**

- 应用在 `finally` 中释放 `monitor.lock`（现网 `main` 已有 finally 路径；落地时确认 **SIGTERM/超时杀进程** 时 flock 随 fd 关闭自动释放）。
- 集成测试：模拟卡住请求 → unit 超时失败 → 锁文件不阻挡下一轮 `--once`。
- **不**在超时后清 token（与现「timeout 不清 token」一致）。

### 3.3 Timer 模板（语义）

**文件名：** `sub2api-monitor-once@.timer`

```ini
[Unit]
Description=Periodic Sub2API group poll (%i)
# 禁止 Requires= 对应 service：enable/start timer 时会立刻拉起 service，
# 绕过 OnBootSec，导致开机惊群。

[Timer]
OnBootSec=1min
OnUnitInactiveSec=240s
RandomizedDelaySec=60s
AccuracySec=1s
Unit=sub2api-monitor-once@%i.service

[Install]
WantedBy=timers.target
```

| 字段 | 作用 |
|------|------|
| `OnBootSec=1min` | 开机错开，真正延迟生效（无 Requires 时） |
| `OnUnitInactiveSec=240s` | 上一轮 oneshot **结束进入 inactive 后** 再等基准间隔 |
| `RandomizedDelaySec=60s` | 错峰：额外 U(0,60s) |
| `AccuracySec=1s` | 避免默认 1min 精度合并/平移削弱错峰 |
| `Unit=` | 指向 oneshot service；**无** `Requires=` |

**实际周期（说清楚，避免「4～5 分钟」含糊）：**

```text
相邻两次 service 启动的间隔
  ≈ (本轮 service 运行时长)
    + OnUnitInactiveSec
    + RandomizedDelaySec 抽样
    + AccuracySec 量化误差（≈1s 量级）

当运行时长 ≪ 间隔时：
  期望间隔 ≈ 240s + E[U(0,60s)] = 240 + 30 = 270s
  范围大约 [240s, 300s]（再加运行时长）

若需要「墙钟上严格每 300s」应用 OnCalendar，语义不同，首版不采用。
```

文档与运维话术使用：**约 4～5 分钟量级（期望 ~270s + 任务时长）**，并写明受运行时长影响；禁止写成固定 300s 墙钟。

### 3.4 周期双源：`POLL_INTERVAL_SECONDS` vs timer

| 角色 | 权威 |
|------|------|
| **生产调度** | timer 参数 |
| **env `POLL_INTERVAL_SECONDS`** | 仍 ≥60 校验；**前台 `run_loop` 调试用**；生产 oneshot **不**用它 sleep |

**禁止「合法但被忽略且不告警」：**

安装/启用脚本（`install_service.sh` 或等价）对每个将 enable 的站：

1. 读取 env 的 `POLL_INTERVAL_SECONDS`（默认 300）。  
2. 计算 timer 期望中点：`OnUnitInactiveSec + RandomizedDelaySec/2`（及文档约定）。  
3. 若 `|env_interval - timer_expected| > 容差`（建议 30s）：  
   - **拒绝 enable**，并打印如何 `systemctl edit sub2api-monitor-once@<id>.timer`；或  
   - 提供 `--write-timer-dropin` 从 env 生成 drop-in（实现可选，首版至少拒绝+说明）。  
4. 当前全站均为 300 且模板对齐时：校验通过。

前台调试：`python …` 无 `--once` 进入 `run_loop` 时，**仍使用** env interval（行为与现网调试一致）。

### 3.5 与旧常驻 unit 的关系

| 名称 | 角色 |
|------|------|
| `sub2api-monitor@%i.service`（现 Type=simple） | **旧**；迁移验收前保留文件以便回滚 |
| `sub2api-monitor-once@%i.service` / `.timer` | **新**生产路径 |
| 同站双开 | **禁止**；flock 兜底 + 迁移脚本互斥检查 |

---

## 4. 升级路径（有证据再走）

与修订前相同：

1. timer 冷启动成本或 unit 运维成本有**实测**不可接受 → batch once。  
2. batch 前必修：`load_config` 纯函数、双 env 不串值、路径 `resolve` 冲突检测。  
3. 常驻多线程仅当秒级周期/长连接需求出现。

---

## 5. 应用层变更（上线前必做）

### 5.1 `--once` = 有界重试回合（阻断项 #1 的闭合）

**事实：** 当前 `main --once` 只调一次 `poll_once()`；`backoff_delay()` / Retry-After 仅在 `run_loop()` 使用。若 oneshot 直接套用现状，则相对常驻 loop **有功能回退**。

**决策：不接受回退。** 将 `--once` 定义为：

```text
在本进程生命周期内：
  最多 MAX_ONCE_ATTEMPTS 次 poll_once（建议默认 3，或 1+len(有界退避)）
  成功 → 退出 0
  失败 → 按 backoff_delay(exc) 可中断 sleep（尊重 ApiError.retry_after）
        → 再试
  用尽次数仍失败 → 退出 1
  不进入无限 loop；不替代 timer 的长周期调度
```

约束：

- 退避阶梯 **复用** 现有 `BACKOFF_SECONDS` 与 `ApiError.retry_after`（429）。  
- 单次 oneshot 内退避总和必须 **小于** `TimeoutStartSec` 安全余量；实现时 cap：  
  `min(backoff_delay(exc), remaining_budget)`。  
- **认证类** 失败：仍只做现有「一轮内 refresh/login 恢复」（在 `get_groups` 内）；进程级重试针对 timeout/5xx/429/network 等 transient。  
- **region / contract** 类：可不重试或只试 1 次，避免无意义重复登录（与现分类一致即可，写进实现注释）。  
- 退出后由 **timer** 负责分钟级再调度；进程内只负责「这一脚别被一次毛刺打飞」。

可选 CLI（若需显式）：`--once-attempts N`（默认 3）；timer 不传则用默认。

### 5.2 其它最小变更

| 项 | 说明 |
|----|------|
| `load_config` 纯函数化 | 推荐同步做；单站 timer 非硬阻断，batch 硬阻断 |
| finally 释锁 | 确认超时杀进程时 fd 关闭即释锁；测试覆盖 |
| 日志 | 保持 `site=`；重试打 `attempt=i/n kind=…` |
| 退出码 | 0 成功 / 1 回合失败 / 2 配置 |

### 5.3 明确不改

认证字段、快照/events 崩溃顺序、token 0600、分类错误枚举、禁止日志打密钥。

---

## 6. 运维界面

```bash
# 校验 + 试跑
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --validate
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --once

# 启用新路径（名称带 once）
systemctl enable --now sub2api-monitor-once@pinaic.timer
systemctl enable --now sub2api-monitor-once@aiapibank.timer

systemctl list-timers 'sub2api-monitor-once@*'
journalctl -u sub2api-monitor-once@pinaic -n 80 --no-pager

# 立刻补跑（不改周期）
systemctl start sub2api-monitor-once@pinaic.service
```

批量：

```bash
for f in sites/*.env; do
  id=$(basename "$f" .env)
  systemctl enable --now "sub2api-monitor-once@${id}.timer"
done
```

---

## 7. 资源（定性）

| 模式 | 空闲 RSS | 隔离 |
|------|----------|------|
| 旧 simple 常驻 ×N | ~30MB×N | 强 |
| **once timer ×N** | **~0** | 强 |
| batch once | ~0 空闲 | 中 |

---

## 8. 迁移与回滚（闭合）

### 8.1 正向（不覆盖旧 unit 文件）

1. 落地 §5.1 有界重试 + 测试；`systemd-analyze verify` 新模板。  
2. 安装 **`sub2api-monitor-once@.service`** 与 **`sub2api-monitor-once@.timer`** 到 `/etc/systemd/system/`。  
3. `daemon-reload`。  
4. **先不 disable 旧服务**：在维护窗对单站试点：  
   - `systemctl stop sub2api-monitor@pinaic`（释放锁）  
   - `systemctl enable --now sub2api-monitor-once@pinaic.timer`  
   - 观察 ≥2 个成功周期  
5. 第二站同样操作。  
6. 两站均稳定后：`systemctl disable --now sub2api-monitor@pinaic sub2api-monitor@aiapibank`（旧 simple）。  
7. **保留** 仓库/磁盘上的旧 `sub2api-monitor@.service` 定义直至确认不再回滚（或显式归档备份路径）。  
8. 更新 `install_service.sh` 默认 enable `*-once@*.timer`。

### 8.2 回滚（无需从备份「想起」被覆盖的文件）

```bash
systemctl disable --now sub2api-monitor-once@pinaic.timer
systemctl stop sub2api-monitor-once@pinaic.service 2>/dev/null || true
systemctl enable --now sub2api-monitor@pinaic.service   # 旧 simple
```

data/token 格式不变，无需数据回滚。

### 8.3 安装脚本检查单

- [ ] 新 unit verify 通过  
- [ ] env 0600 / sites 0700  
- [ ] `POLL_INTERVAL` 与 timer 模板一致性检查  
- [ ] 同站旧 simple active 时拒绝 enable once-timer（或先 stop）  
- [ ] 打印回滚命令  

---

## 9. 测试与验收（上线闸门）

### 9.1 自动化（必加）

| 用例 | 期望 |
|------|------|
| `--once` 首次 429（Retry-After 小值）后成功 | 进程内等待后成功退出 0；调用 `backoff`/sleep |
| `--once` 连续 transient 失败达上限 | 退出 1；尝试次数 = 上界 |
| region/contract 不无意义连打登录 | 与分类策略一致 |
| 双 env 纯函数加载（若已纯函数化） | 不串值 |
| 锁：模拟超时杀进程后再次 once | 可获取锁 |

### 9.2 systemd / 集成（必做）

| 用例 | 期望 |
|------|------|
| `systemd-analyze verify` once service+timer | 通过 |
| enable timer **不会**立刻 start service（无 Requires） | 直到 OnBootSec/下次 elapse |
| `AccuracySec=1s` 存在于已安装 timer | 静态检查 |
| TimeoutStartSec≥240 | 静态检查 |
| 真实 pinaic+aiapibank：once-timer 路径 ≥2 成功周期 | latest 更新、无密钥日志 |
| 超时终止后下轮可跑 | 集成或故障注入 |

### 9.3 验收通过标准

- [ ] §2 七项均在文档与实现/unit 中闭合  
- [ ] 现有单测不回归 + §9.1 新增通过  
- [ ] 生产已切 once-timer 或试点报告完整  
- [ ] 回滚演练一次成功  

**在 §9 完成前：不得宣称「相对常驻 loop 无功能回退」。**

---

## 10. 风险清单

| 风险 | 控制 |
|------|------|
| oneshot 无进程内退避 | §5.1 有界重试 **强制** |
| Requires 惊群 | 禁止 Requires |
| AccuracySec 默认 1min | 强制 1s |
| 超时截断认证恢复 | 240s 默认 + 站级 override |
| interval 双源误配 | 安装校验 |
| 原地替换无法回滚 | 新 unit 名 |
| 误 enable oneshot service | 无 [Install] |
| simple+once 双开 | 脚本互斥 + flock |
| 进程内重试过长 | cap 退避；TimeoutStartSec 总预算 |

---

## 11. 决策记录

1. **timer + oneshot** 仍是默认；常驻多线程不做默认。  
2. **功能无回退优先于「最薄 --once」**：进程内有界重试复用退避/Retry-After。  
3. **长周期只在 timer**；短退避只在 oneshot 进程内。  
4. **新 unit 名** 保证迁移/回滚闭合。  
5. **声明式安全加固保留**；删除无价值的 Persistent / 误导性 Install。

---

## 12. 文档位置

| 文档 | 角色 |
|------|------|
| **`final/architecture.md`（本文）** | 唯一执行方向 |
| `final/timer-units.example.md` | unit 全文蓝本（与本文一致） |
| `final/adoption-matrix.md` | 一/二次评审采纳表 |
| 其它 `onethred/*` 历史稿 | 冲突以 final 为准 |

实现、改 unit、迁生产：**以本文 §2–§5、§8–§9 为闸门。**
