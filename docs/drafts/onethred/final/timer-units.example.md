# Unit 蓝本（与 architecture.md 一致）

**生产路径使用新名称，不覆盖旧 `sub2api-monitor@.service`（simple 常驻）。**

| 文件 | 路径 |
|------|------|
| 仓库 | `sub2api-monitor-once@.service`、`sub2api-monitor-once@.timer` |
| 系统 | `/etc/systemd/system/` 同上 |

同站禁止与旧 `sub2api-monitor@%i.service`（Type=simple）同时 active。

---

## sub2api-monitor-once@.service

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
# ≥ 有界重试 + 最坏 login/refresh/groups 链；读超时 60s 时 120s 不够
TimeoutStartSec=240
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/root/projects/zhongzhuan/data
```

说明：

- **无** `[Install]` / `WantedBy=`：防止 `enable` 本 service 造成与 timer 双入口。  
- **无** `Restart=always`：失败等下一轮 timer，避免登录打爆。  
- 应用 `--once` 必须为 **有界重试回合**（见 architecture §5.1），不是失败即退且无退避。

---

## sub2api-monitor-once@.timer

```ini
[Unit]
Description=Periodic Sub2API group poll (%i)
# 不要写 Requires=sub2api-monitor-once@%i.service
# 否则 enable/start timer 会立刻拉起 service，绕过 OnBootSec，开机惊群。

[Timer]
OnBootSec=1min
OnUnitInactiveSec=240s
RandomizedDelaySec=60s
AccuracySec=1s
Unit=sub2api-monitor-once@%i.service

[Install]
WantedBy=timers.target
```

说明：

- **无** `Persistent=`（单调 timer 无收益，省掉噪音字段）。  
- **必须** `AccuracySec=1s`，避免默认 1min 精度合并/平移。  
- 期望间隔约 `240s + U(0,60s)` 再加本轮运行时长（期望中点约 270s + 任务时间），**不是**固定墙钟 300s。  
- 站级不同周期：`systemctl edit sub2api-monitor-once@<id>.timer`，并与 env `POLL_INTERVAL_SECONDS` 由安装脚本校验一致。

---

## 校验

```bash
systemd-analyze verify \
  /etc/systemd/system/sub2api-monitor-once@.service \
  /etc/systemd/system/sub2api-monitor-once@.timer
```

---

## 与旧 unit 并存时的操作顺序

```bash
# 试点单站
systemctl stop sub2api-monitor@pinaic.service
systemctl enable --now sub2api-monitor-once@pinaic.timer

# 回滚该站
systemctl disable --now sub2api-monitor-once@pinaic.timer
systemctl enable --now sub2api-monitor@pinaic.service
```
