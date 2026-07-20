# 对单进程多线程方案的评审与替代建议

## 结论先行

当前方案抓住了一个真实问题：常驻多进程会重复承担 CPython 和依赖库的内存成本。现有两站实测也支持这一点。但方案从“两进程约 65 MB”直接推导到“100～200 站应采用每站一个常驻线程”，证据链并不充分；它证明了多进程常驻昂贵，没有证明常驻多线程就是最优解。

本项目本质上是一个低频、短时、可独立执行的周期任务。更高一层的简化是把“循环、错峰、进程拉起、故障恢复”交给 systemd timer，现有 Python 只执行已经实现的 `--once`。这样无需在应用内新增 Supervisor、线程生命周期、自愈、热加载和全局停止协议，同时获得更强的站点故障隔离。

建议优先级：

1. 首选：每站 `oneshot service + timer`，用 `RandomizedDelaySec` 错峰。
2. 只有实测证明 unit 数量确实造成运维问题，再做“单个 timer + 有界并发 batch once”。
3. 不建议第一步实现“每站一个永久线程”。

## 一、事实依据审查

### 1. 已经坚实的事实

- 当前机器确有两个常驻实例，RSS 分别约 32～33 MB，PSS 分别约 24 MB；草稿中的两站锚点与实际进程一致。
- 当前代码是单站入口：`--env-file` 为必填参数，尚无 `--sites-dir`、Supervisor 或线程池。
- 每站 token、快照、事件和锁均已按目录隔离，现有 49 个测试全部通过。
- 轮询使用阻塞式 `requests`，网络 I/O 占主导的判断合理，因此若一定要并发，线程是可行手段。

### 2. 尚不坚实或需要修正的事实

#### 内存外推缺少实测

`40 MB + 2 MB/站`、100 站 `150～440 MB` 都是规划假设，不是当前实测。线程栈的虚拟地址空间、Session、TLS、响应体和站点快照各占多少，没有用 10/50/100 个 mock 站做过基准。它们可以作为待验证假设，不能作为选型结论。

应至少测量：N=1/10/50/100 时的 RSS、PSS、VSZ、线程数、一次轮询峰值内存和稳定两个周期后的内存。比较对象应包括常驻多进程、常驻多线程和 timer oneshot 三种模型。

#### CPU 公式错误

草稿把单站的墙钟工作时间 `w` 当成 CPU 时间：

```text
N * w / T = 100 * 3 / 300 = 1
```

如果 `w=3s` 真是 CPU 时间，结果是平均占用一个完整逻辑核，不是约 1%。如果 `w` 主要是网络等待，它又不能用于估算 CPU。正确做法是分别记录 `cpu_seconds_per_poll` 和 `wall_seconds_per_poll`：

```text
平均占用核数 = N * cpu_seconds_per_poll / T
所需并发度约束 = N * wall_seconds_per_poll / T
```

在没有这两个测量值之前，只能定性说“可能是网络瓶颈”，不能给出 CPU 数字结论。

#### “上百 systemd unit 难运维”没有被证明

模板 unit 的配置仍只有一份；站点实例可用 glob、脚本或 target 批量管理。unit 数量多会增加状态展示项，但不等于配置代码线性增长。应先定义具体痛点，例如启动耗时、journal 查询、部署命令还是人工启停，再判断是否值得牺牲进程级隔离。

## 二、逻辑与实现风险

### 1. 当前配置加载器不能直接用于多站

这是主方案落地前的阻断项。`load_config()` 默认把 env 文件内容通过 `setdefault` 写入进程级 `os.environ`，且进程环境优先。循环加载多个 env 时，第一个站写入的 `MONITOR_BASE_URL`、账号、密码、`DATA_DIR` 等可能继续覆盖后续站点。

因此“每站调用一次现有 `load_config` 即可复用”不成立。应先将配置加载改成纯函数：读取单个文件，叠加一份明确允许的进程级覆盖表，但绝不修改全局环境。还必须新增“连续加载两份真实 env 不串值”的测试。

### 2. 永久线程与任务形态不匹配

每个站每 300 秒工作数秒，其余时间只是等待。为每个站永久保留线程，是用应用代码模拟调度器。Supervisor、初始错峰、退避、线程重建、停止、热加载和状态文件，都是由这个选择衍生出的复杂度，而不是业务本身要求的复杂度。

线程池也不只是“线程更少”。如果保留现有 `run_loop()`，任务永不返回，线程池无法调度多于池大小的站点。正确的线程池模型必须把业务改成一次 poll 任务，再由独立调度器维护 `next_run`，这已是另一种架构。

### 3. 故障隔离显著退化

单站未捕获的普通 Python 异常可以在线程顶层捕获，但以下故障会影响全站：内存泄漏、进程 OOM、解释器或 native 扩展崩溃、全局日志配置错误、共享全局状态污染。草稿承认 native 崩溃，却没有把故障域扩大计入选型成本。

此外，伪代码创建的是非 daemon 线程。主线程 `join(timeout)` 后返回并不会保证进程退出，Python 会等待非 daemon 线程；“超时后退出，systemd 会 SIGKILL”的描述不严谨。应依靠严格的 HTTP 总超时和可终止的工作单元，而不是假定 join 超时等于进程终止。

### 4. 配置、日志与退出语义尚未闭合

- `logging.basicConfig()` 是进程级的，不能自然支持每站 `LOG_LEVEL`；需明确改为全局级别或使用 LoggerAdapter/filter。
- 默认 best-effort 跳过坏站，可能让服务显示 active 但部分站永久未运行；没有健康出口时属于静默降级。
- “线程崩溃重启 5 次”缺少时间窗口和成功后是否清零的定义，容易把永久配置错误变成重试噪声。
- `--validate` 当前会创建数据目录，不是无副作用校验；批量校验时应决定是否接受这一行为。
- 路径冲突不能只比较配置字符串，应比较规范化后的真实路径，并同时检查重复 `site_id`、token、latest、events 和 lock 路径。

### 5. 错峰不应删除，但应下沉

“100 站同时启动不太可能触发限流”同样缺少证据。登录、TLS 握手和同一出口 IP 的突发请求确有风险。错峰本身是合理控制，但不必写进 Python；systemd timer 的 `RandomizedDelaySec` 已能表达这一需求。

安全加固项也不应为了 MVP 删除。`NoNewPrivileges`、`ProtectSystem`、`ReadWritePaths` 是声明式配置，代码熵很低，且监控进程持有真实账号和 token，保留它们的收益高于维护成本。

## 三、推荐的更低代码熵方案

### 方案 A：每站 oneshot + timer（推荐）

将现有模板 service 改为执行一次：

```ini
[Service]
Type=oneshot
WorkingDirectory=/root/projects/zhongzhuan
ExecStart=/root/projects/zhongzhuan/.venv/bin/python \
  /root/projects/zhongzhuan/sub2api_monitor.py \
  --env-file /root/projects/zhongzhuan/sites/%i.env --once
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/root/projects/zhongzhuan/data
```

增加一个模板 timer：

```ini
[Timer]
OnBootSec=30s
OnUnitInactiveSec=240s
RandomizedDelaySec=60s
Unit=sub2api-monitor@%i.service

[Install]
WantedBy=timers.target
```

收益：

- 空闲时 Python 常驻内存为零。
- 不修改监控业务代码，也不新增应用内调度器。
- 每站仍是独立进程、日志、退出码、超时和锁，故障域最小。
- 错峰和周期由成熟的 service manager 管理。
- 新站仍只是 env + `enable --now sub2api-monitor@<id>.timer`。

代价是保留较多 unit 实例，并且每次轮询会启动一次解释器。以 300 秒周期衡量，启动开销通常值得用实测确认，而不是预先假定不可接受。

上述示例在任务完成后随机等待 240～300 秒，既分散请求，也避免把 300 秒基础周期再额外随机延长 300 秒。若业务要求严格的墙钟时刻或机器关机期间漏跑后补跑，应改用 `OnCalendar` 并单独评估 `Persistent=true`，而不是混淆两类 timer 语义。同时设置 service 的 `RuntimeMaxSec` 或等价超时，避免一次请求永久占住下一周期。

### 方案 B：单 timer + batch once（unit 数量确有痛点时）

若验证后确认上百 timer 实例确实妨碍运维，再新增一个很薄的批处理入口：

1. 纯函数发现并加载所有站点配置。
2. 每次 timer 触发时，用固定大小 `ThreadPoolExecutor` 并发执行每站一次 poll。
3. 所有 future 完成后进程退出，由下次 timer 重新建立干净状态。
4. 返回汇总退出码，并输出成功、失败、跳过站点清单。

这个模型仍会引入并发，但只在工作窗口存在，不需要永久 Worker、自愈、热加载、停止协议或状态文件。相比当前主方案，它保留了单 unit 的运维体验，同时把长期状态和调度复杂度降到最低。

## 四、建议的决策与验证顺序

1. 先修复 `load_config()` 的全局环境污染，并补多 env 隔离测试；无论选择哪种聚合方案都需要它，timer-per-site 除外。
2. 用 10～50 个本地 mock 站做三组基准：现有常驻进程、timer/oneshot 等价重复执行、batch once 有界并发。
3. 记录 RSS/PSS 峰值与稳态、CPU seconds、wall time、登录次数、失败隔离和停止时间。
4. 默认落地方案 A。只有数据证明解释器启动成本或 unit 管理成本不可接受，才升级到方案 B。
5. 只有当任务需要秒级连续连接或调度频率远高于当前 300 秒时，才重新考虑常驻线程或 asyncio。

## 最终判断

- 事实依据：两站内存数据真实，但百站资源预测和 CPU 推导不足，部分计算错误。
- 逻辑推理：从“多进程常驻浪费内存”推到“每站常驻线程”存在方案跳跃，且遗漏当前配置加载器的全局污染。
- 当前方案是否最优：不是。它可实现，但把调度和监督复杂度搬进了业务进程，并扩大故障域。
- 更高维、低代码熵方案：优先用 systemd timer 驱动现有 `--once`；若必须单 unit，再用短生命周期 batch once + 有界并发。
