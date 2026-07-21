# 多站点分组采集（Sub2API + New-API）

## Sub2API（JWT / Bearer）

公共脚本 `sub2api_monitor.py`：每个站点一份 env、独立数据目录、独立 systemd **timer + oneshot** 实例。  
约每 4～5 分钟拉取完整分组；access token 临近过期时 refresh，失败则密码登录；401 会恢复一次。失败不覆盖上次成功快照。

**生产路径：** `sub2api-monitor-once@<site>.timer` → oneshot → `python … --once`。

## New-API / legacy session（BotCF、TorchAI）

独立入口 `newapi_monitor.py`（**不要**与 Sub2API 混用 DATA_DIR）。Session cookie 鉴权；TorchAI 需 `new-api-user` 头。  
设计文档：`docs/drafts/newapi/`。

```bash
cp sites/botcf.env.example sites/botcf.env && chmod 600 sites/botcf.env
# 填写 MONITOR_USERNAME / MONITOR_PASSWORD
.venv/bin/python newapi_monitor.py --env-file sites/botcf.env --validate
.venv/bin/python newapi_monitor.py --env-file sites/botcf.env
./install_newapi_service.sh botcf torchai
```

数据：`data/<id>/auth_state.json`（0600）、`groups_latest.json`、`groups_events.jsonl`。  
Timer：`newapi-monitor-once@<id>.timer`。

## 目录

```text
sub2api_monitor.py                 # Sub2API 入口
newapi_monitor.py                  # New-API 采集入口
monitor_storage.py                 # New-API 快照/锁/尾事件去重
sites/<site-id>.env                # 每站配置（0600，不入库）
data/<site-id>/
  token.json | auth_state.json     # 按后端
  groups_latest.json
  groups_events.jsonl
  monitor.lock
sub2api-monitor-once@.{service,timer}
newapi-monitor-once@.{service,timer}
sub2api-monitor@.service           # 旧 simple 常驻（回滚用）
```

第一版**不使用** SQLite、`sites.yaml`、Prometheus 或独立告警服务；**不做**应用内 Supervisor / 常驻多线程。

## 配置与试运行

```bash
cd /root/projects/zhongzhuan
python3 -m pip install -r requirements.txt   # 或使用已有 .venv

cp sites/pinaic.env.example sites/pinaic.env
chmod 600 sites/pinaic.env
# 编辑 sites/pinaic.env：账号、密码、DATA_DIR 等

.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --validate
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --once
# 可选：调整有界重试次数（默认 3）
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --once --once-attempts 3
```

`--once` 语义：在本进程内最多 N 次 `poll_once`；transient 失败（timeout/5xx/429/network）按 `BACKOFF_SECONDS` 与 `Retry-After` 可中断 sleep 后重试；region/contract/auth 不无意义连打。成功退出 0，回合失败退出 1，配置错误退出 2。

成功后检查：

- `data/<site-id>/groups_latest.json` — 最新完整分组
- `data/<site-id>/groups_events.jsonl` — 变化事件（首次为 `initial`）
- `data/<site-id>/token.json` — 权限应为 `600`

前台长循环调试（仍用 env 的 `POLL_INTERVAL_SECONDS` sleep）：

```bash
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env
```

## 后台保活（timer + oneshot）

```bash
chmod +x install_service.sh
./install_service.sh                 # 默认 enable --now once-timer：aiapibank + pinaic
# 或指定站点：
./install_service.sh pinaic aiapibank
```

安装脚本会：

- 安装 `sub2api-monitor-once@` 与旧 `sub2api-monitor@` 模板（旧模板仅供回滚）
- `systemd-analyze verify`，并检查 `AccuracySec=1s`、无 `Requires=`、`TimeoutStartSec=240`
- 校验 `POLL_INTERVAL_SECONDS` 与 timer 期望中点（~270s，容差 30s）一致
- 若旧 simple 仍 active，**拒绝** enable once-timer（禁止同站双开）
- 打印回滚命令

```bash
systemctl list-timers 'sub2api-monitor-once@*'
systemctl status  'sub2api-monitor-once@pinaic.timer'
systemctl start   sub2api-monitor-once@pinaic.service   # 立刻补跑，不改周期
journalctl -u sub2api-monitor-once@pinaic -n 80 --no-pager

systemctl disable --now sub2api-monitor-once@pinaic.timer
```

修改 `sites/<id>.env` 后无需 restart 常驻进程；下一轮 timer 触发会重新读 env。立刻生效可：

```bash
systemctl start sub2api-monitor-once@<id>.service
```

### 回滚到旧 simple 常驻

```bash
systemctl disable --now sub2api-monitor-once@pinaic.timer
systemctl stop sub2api-monitor-once@pinaic.service 2>/dev/null || true
systemctl enable --now sub2api-monitor@pinaic.service
# 或：
./install_service.sh --legacy-simple pinaic
```

> 同站禁止 simple 与 once-timer 同时 active。手动 `--once` 时若 timer 正好触发，会争用 `monitor.lock`；短暂失败可忽略或先 `systemctl stop` 对应 oneshot。

## 新增站点

1. `cp sites/pinaic.env.example sites/<id>.env && chmod 600 sites/<id>.env`
2. 填写 `MONITOR_SITE_ID`、`MONITOR_BASE_URL`、账号密码，以及该站独立的 `DATA_DIR` / `TOKEN_STATE_FILE`
3. `--validate` → `--once`
4. `./install_service.sh <id>` 或 `systemctl enable --now sub2api-monitor-once@<id>.timer`

`MONITOR_SITE_ID` 仅允许小写字母、数字、连字符；`MONITOR_BASE_URL` 必须 HTTPS；`POLL_INTERVAL_SECONDS` ≥ 60。  
生产调度以 timer 为准；`POLL_INTERVAL_SECONDS` 须与 timer 期望中点大致一致（安装脚本校验），前台 `run_loop` 调试仍用该值 sleep。

站级不同周期：

```bash
systemctl edit sub2api-monitor-once@<id>.timer
# 调整 OnUnitInactiveSec / RandomizedDelaySec 后与 env 对齐，再 enable
```

## 测试

```bash
.venv/bin/python -m unittest tests.test_monitor -v
```

## 安全

- 不把密码、access/refresh token、Cookie 写入日志或 Git
- `sites/*.env` 与 `data/` 已在 `.gitignore`
- 勿用未知公共代理传输账号密码

## 设计文档

可执行方案：`docs/drafts/onethred/final/architecture.md`
