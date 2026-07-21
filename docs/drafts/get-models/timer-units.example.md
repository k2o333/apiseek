# systemd 单元示例：分组轮询 + 模型日更（定稿草案）

## 1. 原则

| 单元 | 作用 | 周期 |
|------|------|------|
| `sub2api-monitor-once@<site>.timer` | 分组拉取；**仅当**增量开关且已 bootstrap 时对 **真正新增组** 做一次 models | ~4–5 分钟 |
| `sub2api-models-daily@<site>.timer` | 全量 ensure keys + 全组 models | 上海 **00:00 后 0～5 分钟**（抖动） |

- Timer 不 `Requires=` oneshot service。  
- 只 enable timer；service 无 `[Install]`。  
- 共用 `data/<site>/monitor.lock`。  
- **时区只写在 OnCalendar**，不使用 Python `MONITOR_MODELS_DAILY_TZ`。

---

## 2. 分组 oneshot（现网，示意）

`TimeoutStartSec=240` 保持。  
`--once` **默认不** create Key、不打全量 models。

```ini
# sub2api-monitor-once@.service — ExecStart 仍为 --once
TimeoutStartSec=240
```

取锁：**非阻塞**；拿不到则本轮 exit，下周期再来。

---

## 3. 模型日更

### 3.1 Service

```ini
[Unit]
Description=Sub2API daily models refresh (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/root/projects/zhongzhuan
ExecStart=/root/projects/zhongzhuan/.venv/bin/python \
  /root/projects/zhongzhuan/sub2api_monitor.py \
  --env-file /root/projects/zhongzhuan/sites/%i.env --models-refresh
TimeoutStartSec=600
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/root/projects/zhongzhuan/data
```

实现要求（与 unit 配合）：

- 进程内总 deadline ~500s，逐组 checkpoint  
- 部分失败 exit **非 0**  
- 取锁：**有界等待**（建议 90～120s，覆盖一轮 groups），失败则进程内有限重试（如 3 次短 sleep），避免 **整天漏跑**

### 3.2 Timer（推荐唯一写法）

```ini
[Unit]
Description=Daily Sub2API models refresh (%i)

[Timer]
OnCalendar=*-*-* 00:00:00 Asia/Shanghai
RandomizedDelaySec=300
Persistent=true
AccuracySec=1min
Unit=sub2api-models-daily@%i.service

[Install]
WantedBy=timers.target
```

语义说明（验收文案）：

- 触发窗口 = **上海时区当天 00:00 起 0～300 秒均匀抖动**，不是精确 00:00:00。  
- `Persistent=true`：补的是 **关机错过的 calendar**，**不**补「service 因锁失败 exit 1」；锁失败靠 service 内有界等待 + 有限重试。  
- 无需维护 UTC 换算的 A/B 两套 timer。

可选加固（非必须）：同站增加 `OnCalendar=*-*-* 00:30:00 Asia/Shanghai` 的 backup timer 仅在主 timer 连续失败时——第一版 **不做**，以免双跑；优先做好锁等待。

---

## 4. 安装与首次启用

```bash
# 1) 只读
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --models-preflight

# 2) 显式冷启动（写远端 Key + models）
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --models-bootstrap
# 再跑一次：created 必须为 0
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --models-bootstrap

# 3) 可选：打开 5 分钟增量（真正新组）
# sites/pinaic.env: MONITOR_MODELS_INCREMENTAL_ENABLE=1

# 4) 日更
systemctl enable --now sub2api-models-daily@pinaic.timer
systemctl start sub2api-models-daily@pinaic.service
journalctl -u sub2api-models-daily@pinaic -n 80 --no-pager
systemctl list-timers 'sub2api-models-daily@*'
```

`install_service.sh` 扩展时：默认 **不要** 在未 bootstrap 的站上自动 enable daily；或 enable 但文档要求先 bootstrap。

---

## 5. 锁与 stale 窗口（诚实表述）

| 场景 | 行为 |
|------|------|
| groups 与 daily 同时 | daily 等待锁；groups 非阻塞失败则下周期再跑 |
| daily 持锁数分钟 | groups 可能 **跳过 1 轮** |
| 最大分组 stale（偶发） | ≈ `OnUnitInactive+jitter + daily 持锁时长`，**不是**严格 ≤5 分钟永远成立 |
| daily 锁等待耗尽 | 同进程有限重试；仍失败 → exit≠0 + 告警；**不**依赖人工当正常路径 |

---

## 6. 关闭模型能力

```bash
# 关日更
systemctl disable --now sub2api-models-daily@pinaic.timer

# 关增量
# MONITOR_MODELS_INCREMENTAL_ENABLE=0

# 分组 5 分钟 timer 保持
```

「关闭模型能力」= **两者都关**；仅关 env 不等于停 daily。

---

## 7. 多站错峰

全局 `RandomizedDelaySec=300` 即可。  
不必应用内协调多站。
