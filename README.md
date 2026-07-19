# AIAPIBANK 分组监控

常驻进程每 5 分钟获取一次完整分组信息。访问 token 临近过期或接口返回
401/403 时，脚本会用账号密码重新登录；systemd 会在进程异常退出后自动拉起。

## 配置与试运行

```bash
cd /root/projects/zhongzhuan
cp config.env.example config.env
chmod 600 config.env
# 编辑 config.env，填写账号和密码
python3 -m pip install -r requirements.txt
python3 aiapibank_monitor.py --once
```

成功后会生成：

- `data/groups_latest.json`：最近一次完整分组数据。
- `data/groups_history.jsonl`：每次轮询的历史记录，一行一次。
- `data/token_state.json`：缓存的登录 token，权限为 600。

## 后台保活

```bash
chmod +x install_service.sh
sudo ./install_service.sh
```

查看运行状态和日志：

```bash
systemctl status aiapibank-monitor
journalctl -u aiapibank-monitor -f
```

修改 `config.env` 后执行 `systemctl restart aiapibank-monitor`。停止并取消开机启动：

```bash
systemctl disable --now aiapibank-monitor
```

## 监控其他站点

脚本也支持独立站点配置。PINAIC 使用 `pinaic.env`，与 AIAPIBANK 的
`config.env` 完全分离，数据写入 `data/pinaic/`：

```bash
cp pinaic.env.example pinaic.env
chmod 600 pinaic.env
# 编辑 pinaic.env，填写账号密码；若当前区域受限，配置 MONITOR_PROXY_URL
python3 aiapibank_monitor.py --env-file pinaic.env --once
sudo ./install_pinaic_service.sh
```

登录地址、分组地址和登录名字段分别由 `MONITOR_LOGIN_PATH`、
`MONITOR_GROUPS_PATH`、`MONITOR_USERNAME_FIELD` 控制。每个站点应使用独立的
env 文件、`DATA_DIR` 和 `TOKEN_STATE_FILE`，避免凭据、token 和历史数据串用。
