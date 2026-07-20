# Sub2API 多站点分组监控

公共脚本 `sub2api_monitor.py`：每个站点一份 env、独立数据目录、独立 systemd 实例。  
默认约每 5 分钟拉取完整分组；access token 临近过期时 refresh，失败则密码登录；401 会恢复一次。地区型 403、超时、5xx 不会清空 token，也不会覆盖最后一次成功快照。

## 目录

```text
sub2api_monitor.py              # 公共入口
sites/<site-id>.env             # 每站配置（0600，不入库）
sites/<site-id>.env.example     # 示例
data/<site-id>/
  token.json                    # access/refresh，0600
  groups_latest.json            # 最近一次成功快照
  groups_events.jsonl           # 仅内容变化时追加
  monitor.lock                  # 单实例锁
sub2api-monitor@.service        # systemd 模板
```

第一版**不使用** SQLite、`sites.yaml`、Prometheus 或独立告警服务。

## 配置与试运行

```bash
cd /root/projects/zhongzhuan
python3 -m pip install -r requirements.txt   # 或使用已有 .venv

cp sites/pinaic.env.example sites/pinaic.env
chmod 600 sites/pinaic.env
# 编辑 sites/pinaic.env：账号、密码、DATA_DIR 等

.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --validate
.venv/bin/python sub2api_monitor.py --env-file sites/pinaic.env --once
```

成功后检查：

- `data/<site-id>/groups_latest.json` — 最新完整分组
- `data/<site-id>/groups_events.jsonl` — 变化事件（首次为 `initial`）
- `data/<site-id>/token.json` — 权限应为 `600`

## 后台保活

```bash
chmod +x install_service.sh
./install_service.sh                 # 默认 enable --now aiapibank + pinaic
# 或指定站点：
./install_service.sh pinaic aiapibank
```

```bash
systemctl status  'sub2api-monitor@*'
systemctl start   'sub2api-monitor@*'
systemctl stop    'sub2api-monitor@*'
systemctl restart 'sub2api-monitor@*'

journalctl -u sub2api-monitor@pinaic -f
journalctl -u sub2api-monitor@aiapibank -f
```

修改 `sites/<id>.env` 后：

```bash
systemctl restart sub2api-monitor@<id>
```

停用某一站：

```bash
systemctl disable --now sub2api-monitor@<id>
```

> 服务运行时占用 `monitor.lock`。若要手动 `--once`，先 `systemctl stop sub2api-monitor@<id>`，结束后再 `start`。

## 新增站点

1. `cp sites/pinaic.env.example sites/<id>.env && chmod 600 sites/<id>.env`
2. 填写 `MONITOR_SITE_ID`、`MONITOR_BASE_URL`、账号密码，以及该站独立的 `DATA_DIR` / `TOKEN_STATE_FILE`
3. `--validate` → `--once`
4. `systemctl enable --now sub2api-monitor@<id>`

`MONITOR_SITE_ID` 仅允许小写字母、数字、连字符；`MONITOR_BASE_URL` 必须 HTTPS；`POLL_INTERVAL_SECONDS` ≥ 60。

## 测试

```bash
.venv/bin/python -m unittest tests.test_monitor -v
```

## 安全

- 不把密码、access/refresh token、Cookie 写入日志或 Git
- `sites/*.env` 与 `data/` 已在 `.gitignore`
- 勿用未知公共代理传输账号密码
