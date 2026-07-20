# Sub2API 单进程多线程多站点监控方案（详细设计）

> 目录：`docs/drafts/onethred/`  
> 状态：设计草稿  
> 背景：当前 `sub2api-monitor@%i` 一站一进程，单站约 30MB RSS；几十～上百站时内存与 unit 数量不可接受。

---

## 1. 问题与目标

### 1.1 现状问题

| 维度 | 一站一进程（现状） | 站数放大后的问题 |
|------|-------------------|------------------|
| 内存 | ~30MB RSS / 站 | 100 站 ≈ 3GB 仅解释器重复开销 |
| systemd | 每站一个 unit | 上百 unit 难运维 |
| 运维 | 按站 restart 方便 | 批量启停、统一升级脚本繁琐 |
| 隔离 | 进程级，很强 | 对「纯 I/O 轮询」偏贵 |

### 1.2 目标

1. **一个主进程** 同时监控 N 个站点（目标 N = 50～200 量级可跑）。
2. **配置模型不变**：`sites/<id>.env` + `data/<id>/`（token / latest / events）。
3. **站点间业务隔离**：token、HTTP Session、失败计数、快照互不串用。
4. **故障隔离（尽力）**：一站超时/认证失败不拖死其它站；一站线程异常可记录并可选自动重建。
5. **资源可控**：100 站常驻内存目标量级 **数百 MB 内**（见 `resource-model.md`），平均 CPU 仍接近空闲。
6. **兼容演进**：保留 `--env-file` 单站模式（调试、临时单站）；生产默认 `--sites-dir`。
7. **不加第一版已排除的重物**：仍不强制 SQLite / Prometheus / 多适配器框架；需要时可后续加。

### 1.3 非目标（本方案阶段）

- 不引入浏览器 / LLM / 验证码破解。
- 不要求跨站统一账号。
- 不实现分布式多机分片（单机先吃满；多机分片可作为二期）。
- 不保证「一站 native 崩溃不影响进程」（Python 线程无法做到进程级隔离；见风险节）。

---

## 2. 总体架构

### 2.1 逻辑结构

```text
                    ┌─────────────────────────────────────────┐
                    │  sub2api_monitor.py（单进程）              │
                    │  ┌───────────────────────────────────┐  │
                    │  │ Supervisor（主线程）                 │  │
                    │  │  - 扫描 sites/*.env                 │  │
                    │  │  - 加载/校验配置                    │  │
                    │  │  - 启动/监督 Worker                 │  │
                    │  │  - 处理 SIGTERM/SIGINT              │  │
                    │  │  - 可选：热加载新增/删除站            │  │
                    │  └───────────────────────────────────┘  │
                    │           │ 创建/join/stop                │
                    │  ┌────────▼────────┐  ┌──────────────┐  │
                    │  │ Worker 线程     │  │ Worker 线程  │ …│
                    │  │ site=pinaic     │  │ site=xxx     │  │
                    │  │ 独立 Session    │  │ 独立 Session │  │
                    │  │ 独立 TokenStore │  │ 独立 Token   │  │
                    │  │ 独立 MonitorLoop│  │ 独立 Loop    │  │
                    │  └────────┬────────┘  └──────┬───────┘  │
                    └───────────┼──────────────────┼──────────┘
                                │                  │
                    sites/pinaic.env          sites/xxx.env
                    data/pinaic/*             data/xxx/*
```

### 2.2 与现状的关系

| 模块 | 是否复用现有 `sub2api_monitor.py` |
|------|-----------------------------------|
| `load_config` / 校验 | 复用，每站一次 |
| `AuthGroupClient` / login/refresh/groups | 复用，**每站实例独立** |
| `TokenStore` / `SnapshotStore` | 复用，路径在 `data/<id>/` |
| `GroupMonitor.run_loop` | 复用，每站线程内跑 |
| CLI `--env-file` 单站 | 保留 |
| CLI `--sites-dir` 多站 | **新增** |
| systemd `@%i` | 保留可选；生产推荐 **单 unit** |

**原则：** 不要把「多站」做成第二套业务逻辑；只加 **Supervisor + 线程生命周期**。

### 2.3 进程 / 线程模型选型

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| 多进程（现状） | 隔离最强 | 内存 ×N | 站少可保留调试 |
| **每站一线程 + 阻塞 I/O（requests）** | 实现简单，直接复用现码 | 线程数 ≈ 站数；极端站数时栈内存上升 | **推荐作为第一落地** |
| 有界线程池 + 任务队列 | 线程数封顶 | 站数 > 池大小时周期被拉长；调度复杂 | 站数 >200 或线程开销明显时升级 |
| asyncio + httpx | 单线程高并发连接 | 需重写 HTTP 层；与现 requests 代码分叉 | 二期优化项 |

**第一落地推荐：每站一个 `threading.Thread`，线程内继续用现有 `requests` 阻塞轮询。**

理由：

- 轮询是 **I/O 等待为主**，GIL 影响小。
- 与现有 `GroupMonitor` / 退避 / 可中断 sleep 几乎零改动。
- 100 线程对 Linux 完全可接受；内存主要仍是 **一份解释器 + 每站少量状态**。

---

## 3. 配置与发现

### 3.1 仍不引入 sites.yaml（本阶段）

继续：

```text
sites/
  pinaic.env
  aiapibank.env
  site-003.env
  …
  *.env.example   # 不加载
```

Supervisor 启动时扫描：

```text
sites_dir.glob("*.env")
排除：*.env.example、备份文件（可选：*~、*.bak）
```

排序：按 `site_id` 或文件名稳定排序，保证日志与启动顺序可复现。

### 3.2 每站配置字段（与现网一致）

必须：`MONITOR_SITE_ID`、`MONITOR_BASE_URL`、`MONITOR_USERNAME`、`MONITOR_PASSWORD`、`DATA_DIR`、`TOKEN_STATE_FILE`  

默认：login/refresh/groups 路径、interval≥60、超时、jitter 等（同现实现）。

### 3.3 启动时校验策略

| 策略 | 行为 | 建议 |
|------|------|------|
| fail-fast | 任一站 env 非法则整个进程退出 | 仅 CI / `--validate` |
| **best-effort（推荐生产）** | 非法站记 ERROR 并跳过，其余站照跑 | 默认 |
| 运行中热加载 | 定时重扫目录 | 可选二期 |

`--validate` + `--sites-dir`：校验全部 env，任一失败返回非 0（运维上线闸门）。

### 3.4 数据目录隔离（不变）

```text
data/<site-id>/
  token.json
  groups_latest.json
  groups_events.jsonl
  monitor.lock          # 见 4.4
```

禁止两站共享同一 `DATA_DIR` / `TOKEN_STATE_FILE`：  
启动时 Supervisor 检测路径冲突，冲突站拒绝加载。

---

## 4. 运行时行为

### 4.1 Worker 职责（每站一线程）

每个 Worker：

1. `load_config(env_file)`（或使用主线程已加载的 `MonitorConfig` 只读对象）。
2. 创建 **独立** `TokenStore`、`AuthGroupClient`（独立 `requests.Session`）、`GroupMonitor`。
3. 尝试获取 `monitor.lock`（可选策略见 4.4）。
4. 进入 `run_loop()`：poll → 成功 delay / 失败 backoff → 可中断 sleep。
5. 全局 `STOP_REQUESTED` 为真时退出；`session.close()`，释放锁。

### 4.2 错峰启动（强烈建议）

100 站若同一秒全部 login/groups，会：

- 打满出口带宽 / 触发源站或中间设备限流  
- 瞬时 CPU/SSL 握手尖峰  

启动时对第 i 个站增加错峰：

```text
initial_delay_i = (i % stagger_buckets) * stagger_step_seconds
                + random(0, site.request_jitter_seconds)
```

建议默认：

- `STAGGER_STEP_SECONDS=2`（或 `POLL_INTERVAL / max(N,1)` 封顶）
- 最大启动铺开窗口不超过一个 poll interval（例如 300s 内铺完）

也可在成功周期上继续用现有 `REQUEST_JITTER_SECONDS`。

### 4.3 停止与信号

| 信号 | 行为 |
|------|------|
| SIGTERM / SIGINT | 主线程置 `STOP_REQUESTED=True` |
| Worker | `interruptible_sleep` 每 ≤1s 检查标志，退出 loop |
| 主线程 | `thread.join(timeout=…)`，超时打警告后退出进程（systemd 会 SIGKILL） |

`TimeoutStopSec=` 建议 30～60s，保证多数站能优雅退出。

### 4.4 与「单站进程」的锁关系

现状：`data/<id>/monitor.lock` 防止同站双开。

多线程单进程后：

| 场景 | 策略 |
|------|------|
| 仅跑多站进程 | 每站仍可 flock，防止用户又手动起了 `@pinaic` 单站 unit |
| 同进程内 | 不会对同一站起两个 Worker（启动表去重 `site_id`） |
| 迁移期 | **禁止** `sub2api-monitor@*` 与多站主服务同时跑同一站 |

推荐：多站服务与模板 unit **二选一** 作为生产入口；调试单站时先停多站服务或从目录暂时移走该 env。

### 4.5 一站失败的语义

与现单站逻辑相同，仅作用域在线程内：

- 401：refresh 一次 → login 一次 → 本站失败计数 + 退避  
- 地区 403：不 login 循环  
- timeout/5xx：保留 token，退避  
- 坏 JSON：不覆盖 latest  

**禁止** 因一站失败 `sys.exit` 整个进程（除非 Supervisor 自身崩溃）。

### 4.6 线程异常与自愈

```text
Worker 顶层 try/except：
  未捕获异常 → LOG.exception(site=…)
  → 可选：sleep 后重建该站 Worker（有界次数）
  → 或：标记 site 为 dead，等热加载/进程重启
```

建议：默认 **记录 + 有界重启该线程**（如 5 次后放弃该站直至进程重启），避免 silent death。

### 4.7 日志

- 格式保持：`site=<id> …`（现有已具备）
- 多线程下 logging 模块线程安全；避免在日志中输出 token/密码（现有约束不变）
- 可选：`logging.Formatter` 增加 `threadName=site-pinaic` 便于 journal 过滤

```bash
journalctl -u sub2api-monitor -f | grep 'site=pinaic'
```

---

## 5. CLI 设计

### 5.1 参数

```bash
# 单站（兼容现网/调试）
python3 sub2api_monitor.py --env-file sites/pinaic.env --validate
python3 sub2api_monitor.py --env-file sites/pinaic.env --once
python3 sub2api_monitor.py --env-file sites/pinaic.env

# 多站（新）
python3 sub2api_monitor.py --sites-dir sites --validate
python3 sub2api_monitor.py --sites-dir sites --once
python3 sub2api_monitor.py --sites-dir sites

# 可选过滤
python3 sub2api_monitor.py --sites-dir sites --only pinaic,aiapibank
python3 sub2api_monitor.py --sites-dir sites --exclude experimental-1
```

规则：

- `--env-file` 与 `--sites-dir` **互斥**（必须且只能其一，或：两者都无时默认 `sites/`）。
- `--once`：所有站各 poll 一次（可用线程池并行，或有界并发如 10）；汇总退出码：任一站失败返回 1，配置全失败返回 2。
- `--validate`：只校验不联网。

### 5.2 环境变量（进程级，可选）

| 变量 | 含义 | 默认 |
|------|------|------|
| `MONITOR_SITES_DIR` | 默认站点目录 | `sites` |
| `MONITOR_STAGGER_STEP_SECONDS` | 错峰步长 | `2` |
| `MONITOR_MAX_SITE_THREADS` | 若用线程池模式上限 | `0`=每站一线程 |
| `MONITOR_WORKER_RESTART_LIMIT` | 单站线程崩溃重建次数 | `5` |
| `LOG_LEVEL` | 全局日志 | 可被站内覆盖仅影响该站日志级别时需设计；建议全局统一 |

---

## 6. systemd 设计

### 6.1 推荐：单一服务

文件：`sub2api-monitor.service`（注意：无 `@`）

```ini
[Unit]
Description=Sub2API multi-site group monitor (threaded)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/projects/zhongzhuan
ExecStart=/root/projects/zhongzhuan/.venv/bin/python \
  /root/projects/zhongzhuan/sub2api_monitor.py \
  --sites-dir /root/projects/zhongzhuan/sites
Restart=always
RestartSec=10
TimeoutStopSec=60
# 站数上去后可按需提高
# LimitNOFILE=65535
# TasksMax=512
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/root/projects/zhongzhuan/data

[Install]
WantedBy=multi-user.target
```

### 6.2 与模板 unit 的关系

| Unit | 用途 |
|------|------|
| `sub2api-monitor.service` | **生产默认**：全站线程 |
| `sub2api-monitor@%i.service` | 可选：单站调试 / 灰度 / 问题站隔离拉出 |

生产开启多站服务前：

```bash
systemctl disable --now 'sub2api-monitor@*'
systemctl enable --now sub2api-monitor.service
```

### 6.3 安装脚本调整方向

`install_service.sh`：

1. 安装 `sub2api-monitor.service` + 仍可安装模板 unit（调试用）
2. 默认 `enable --now sub2api-monitor`（多站）
3. 参数 `--per-site` 时才 enable `@pinaic` 等旧模式

---

## 7. 新增站点流程（运维）

与现在几乎相同，**不必再新建 systemd 实例**（若已用多站服务）：

1. `cp sites/pinaic.env.example sites/newsite.env && chmod 600`
2. 填 URL、用户名、密码、SITE_ID、DATA_DIR、TOKEN 路径
3. `python3 sub2api_monitor.py --env-file sites/newsite.env --validate`
4. `python3 sub2api_monitor.py --env-file sites/newsite.env --once`（建议先单站验证）
5. **热加载未实现时**：`systemctl restart sub2api-monitor`  
   **热加载实现后**：放入 `sites/` 即可在下个扫描周期被拉起

---

## 8. 容量与性能（摘要）

详见 [resource-model.md](./resource-model.md)。

粗算（orders of magnitude）：

| 站数 | 多进程 RSS 粗算 | 单进程多线程 RSS 粗算 |
|------|-----------------|------------------------|
| 2 | ~65 MB | ~40–50 MB |
| 20 | ~600 MB | ~80–150 MB |
| 100 | ~3 GB | ~150–400 MB（视并发与库缓存） |

CPU：默认 300s 周期，100 站即使每次请求 2s，平均 CPU 仍很低；瓶颈通常是 **出口 IP 限流 / 源站风控**，不是本机算力。

文件描述符：每站活跃时约数个；100 站建议 `LimitNOFILE=65535` 预留。

---

## 9. 实现分期

### 阶段 A — 最小可用（建议先做）

1. CLI：`--sites-dir` / `--only` / 与 `--env-file` 互斥  
2. `discover_site_env_files()` + 路径冲突检测  
3. `MultiSiteSupervisor`：每站一线程 + 错峰 + 统一 STOP  
4. `--once` 并行有界（如最多 16 并发）  
5. 单元测试：双站并行 mock、一站失败不影响另一站、路径冲突、discover 忽略 example  
6. 新 unit `sub2api-monitor.service`；文档与 `install_service.sh`  
7. 本机迁移：停 `@pinaic`/`@aiapibank`，起多站服务，观察两周期  

### 阶段 B — 运维增强

1. 热加载：`SIGHUP` 或定时扫描，增站起线程、删站协作停止  
2. `/health` 可选本地 unix socket 或 status 文件（每站 last_ok / last_error）  
3. 线程崩溃有界自愈  
4. 指标：可选写入 `data/_supervisor/status.json`（非 Prometheus 也行）  

### 阶段 C — 超大规模

1. `MONITOR_MAX_SITE_THREADS` 线程池 + 每站 next_run 堆调度  
2. 或 asyncio 迁移 HTTP  
3. 多机分片：`SITE_SHARD=1/3` 按 hash(site_id) 取模  
4. 若需跨站查询历史：再评估 SQLite  

---

## 10. 代码结构建议（仍单文件或极小拆分）

优先保持可测、少文件：

```text
sub2api_monitor.py
  - 现有 config/auth/groups/snapshot/monitor
  - discover_sites()
  - run_single_site(config, once=…)
  - MultiSiteSupervisor
  - main() 分支 single | multi
```

若文件过长再拆：

```text
sub2api_monitor/
  config.py
  client.py
  store.py
  worker.py
  supervisor.py
  __main__.py
```

**不要** 为了多线程先上十模块框架。

---

## 11. 测试矩阵（落地时必须覆盖）

| 用例 | 期望 |
|------|------|
| discover 只加载 `*.env` 不加载 `*.example` | 站列表正确 |
| 两站不同 token 文件并行 once | 无交叉写入 |
| 站 A mock 超时，站 B 成功 | B 的 latest 更新，A 的 latest 保持 |
| 两站 DATA_DIR 冲突 | 拒绝加载并报错 |
| `--sites-dir --validate` 缺密码 | 非 0 |
| SIGTERM 多线程 | 主进程在 Timeout 内退出 |
| 与单站 flock | 多站进程占用锁时，另一 `--env-file` 同站失败 |
| 错峰 | 启动时间戳不完全相同（可测 stub clock） |

真实验证：用现有 pinaic + aiapibank 跑 `--sites-dir sites --once`，再挂 systemd 观察 ≥2 个周期。

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 一站触发解释器致命错误 | 全站停 | 少用 native 扩展；阶段 C 可多 worker 进程分片 |
| 线程数 = 站数过大 | 栈内存、调度开销 | 阶段 C 线程池；或每进程 50 站 × 多进程 |
| 同时请求过多 | 限流/封 IP | 错峰 + jitter + 可选全局并发信号量 |
| 与旧 `@%i` 双开 | token 竞争 | 迁移脚本 disable 模板实例；flock |
| 热加载误删 env | 站被停 | 删站需显式标记或宽限期 |
| 日志量 ×N | journal 膨胀 | 成功轮询可降到 INFO 摘要，DEBUG 再打全部分组 |

---

## 13. 决策记录（ADR 摘要）

1. **配置继续 env 分文件**，不强制 yaml：运维已熟悉；百站以内可接受。  
2. **第一落地每站一线程 + requests**：复用成本最低。  
3. **生产单 unit，模板 unit 降为调试**：降低 systemd 数量。  
4. **数据面零迁库**：latest/events/token 格式不变，降低切换风险。  
5. **SQLite 仍非必须**：只有跨站查询/复杂报表时再上。

---

## 14. 验收标准（方案落地完成时）

- [ ] 单进程可加载 ≥2 站（真实）并持续轮询  
- [ ] 内存明显低于「站数 × 30MB」（例如 2 站 < 80MB，有记录）  
- [ ] 新站只需新增 `sites/<id>.env` + 重启（或热加载）  
- [ ] 单站失败不阻止其它站更新 latest  
- [ ] 自动化测试覆盖并行隔离与 discover  
- [ ] systemd 单一服务 verify 通过；旧 `@` 实例已迁移说明完整  
- [ ] 文档：README 改为多站默认，单站模式仍有说明  

---

## 15. 附录：关键伪代码

```python
def main_multi(sites_dir: Path, once: bool, validate: bool) -> int:
    env_files = discover_site_env_files(sites_dir)
    configs = []
    for path in env_files:
        try:
            configs.append(load_config(path))
        except ConfigError as e:
            LOG.error("skip %s: %s", path, e)
    assert_no_data_dir_conflicts(configs)
    if validate:
        return 0 if len(configs) == len(env_files) else 2
    if once:
        return run_all_once_parallel(configs, max_workers=16)

    stop = threading.Event()
    def on_signal(*_):
        stop.set()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    threads = []
    for i, cfg in enumerate(configs):
        t = threading.Thread(
            target=site_worker,
            name=f"site-{cfg.site_id}",
            args=(cfg, stop, i * STAGGER_STEP),
            daemon=False,
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return 0

def site_worker(cfg, stop: threading.Event, initial_delay: float) -> None:
    interruptible_sleep(initial_delay, stop)
    monitor, lock, client = build_monitor(cfg)
    monitor.stop_flag = stop.is_set  # 或 lambda: stop.is_set()
    try:
        lock.acquire()
        monitor.run_loop()
    finally:
        client.close()
        lock.release()
```

---

## 16. 参考

- 现网实现：`/root/projects/zhongzhuan/sub2api_monitor.py`
- 现网单站 unit：`sub2api-monitor@.service`
- 先前最终方案（一站一进程）：`docs/drafts/2/sub2api-group-monitor-final.md`
- 资源估算：`docs/drafts/onethred/resource-model.md`
- 迁移步骤：`docs/drafts/onethred/migration-from-template-units.md`
