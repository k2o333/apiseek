# New-API Unit 蓝本（修订）

与 architecture 一致：固定 data root、无 Requires、短超时保险、UMask。

| 文件 | 路径 |
|------|------|
| 仓库 | `newapi-monitor-once@.service`、`newapi-monitor-once@.timer` |
| 系统 | `/etc/systemd/system/` 同上 |

---

## newapi-monitor-once@.service

```ini
[Unit]
Description=New-API one-shot token group poll (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/root/projects/zhongzhuan
ExecStart=/root/projects/zhongzhuan/.venv/bin/python \
  /root/projects/zhongzhuan/newapi_monitor.py \
  --env-file /root/projects/zhongzhuan/sites/%i.env
# 应用内 deadline 约 150–180s；240s 为最终保险
TimeoutStartSec=240
UMask=0077
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
# 与不可配置 DATA_DIR=<项目根>/data/<site_id> 对齐；禁止任意自定义路径
ReadWritePaths=/root/projects/zhongzhuan/data
# 无 Restart=always
# 无 [Install]
```

说明：

- 入口 **默认单次**（无 `--once` 标志亦可；CLI 只有 `--validate` 与默认采集）。  
- `%i` == env stem == `MONITOR_SITE_ID`。  
- **已接受风险：** 现布局下可能以 root 跑在 `/root/projects/...`（与现 Sub2API 相同）；专用用户迁移为后续。

---

## newapi-monitor-once@.timer

```ini
[Unit]
Description=Periodic New-API token group poll (%i)
# 禁止 Requires= 对应 service

[Timer]
OnBootSec=1min
OnUnitInactiveSec=240s
RandomizedDelaySec=60s
AccuracySec=1s
Unit=newapi-monitor-once@%i.service

[Install]
WantedBy=timers.target
```

- 无 `Persistent=`。  
- 必须 `AccuracySec=1s`。  
- **无** env 侧 `POLL_INTERVAL` 双源校验（入口无 loop，timer 为唯一调度权威）。

期望间隔 ≈ `240s + U(0,60s)` + 任务时长。

---

## 校验

```bash
systemd-analyze verify \
  /etc/systemd/system/newapi-monitor-once@.service \
  /etc/systemd/system/newapi-monitor-once@.timer
```

- [ ] 无 `Requires=`  
- [ ] `AccuracySec=1s`  
- [ ] `TimeoutStartSec=240`  
- [ ] service 无 `[Install]`  
- [ ] `ReadWritePaths` 指向固定 data 根  

---

## 操作

```bash
systemctl enable --now newapi-monitor-once@botcf.timer
systemctl start newapi-monitor-once@botcf.service   # 立刻补跑

# 停用（不称“回滚到旧实现”）
systemctl disable --now newapi-monitor-once@botcf.timer
systemctl stop newapi-monitor-once@botcf.service 2>/dev/null || true
```
