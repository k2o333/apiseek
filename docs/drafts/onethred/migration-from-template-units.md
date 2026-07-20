# 从一站一进程（`sub2api-monitor@%i`）迁移到单进程多线程

> 前置阅读：`sub2api-multithread-multi-site.md`  
> 原则：**先并行验证，再切流量；不删 data；可回滚。**

---

## 1. 迁移前检查清单

- [ ] 所有生产站均有 `sites/<id>.env`（0600）
- [ ] `data/<id>/token.json`、`groups_latest.json` 路径在 env 中正确
- [ ] 无两站共享同一 `DATA_DIR`
- [ ] 代码已支持 `--sites-dir` 且测试通过
- [ ] 已安装 `sub2api-monitor.service`（多站）unit 文件
- [ ] 已记录当前各 `@` 实例状态（便于回滚）

```bash
systemctl list-units 'sub2api-monitor@*' --all
ls -la /root/projects/zhongzhuan/sites/*.env
```

---

## 2. 推荐迁移步骤（生产）

### 步骤 1 — 旁路验证（不停旧服务也可，注意锁）

若旧 `@` 仍占用 `monitor.lock`，旁路 `--once` 会失败。两种做法：

**做法 A（推荐）：维护窗口短暂停旧服务**

```bash
cd /root/projects/zhongzhuan
systemctl stop 'sub2api-monitor@*'

.venv/bin/python sub2api_monitor.py --sites-dir sites --validate
.venv/bin/python sub2api_monitor.py --sites-dir sites --once
# 检查各 data/*/groups_latest.json 时间戳与 count
```

**做法 B：** 在测试目录复制 env + data 做隔离验证（不碰生产锁）

### 步骤 2 — 停用模板实例

```bash
systemctl disable --now sub2api-monitor@pinaic.service
systemctl disable --now sub2api-monitor@aiapibank.service
# 有其它站则同样 disable --now
```

### 步骤 3 — 启用多站服务

```bash
install -m 644 sub2api-monitor.service /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/sub2api-monitor.service
systemctl daemon-reload
systemctl enable --now sub2api-monitor.service
systemctl status sub2api-monitor.service
journalctl -u sub2api-monitor -f
```

### 步骤 4 — 观察至少两个成功周期

- 日志中每个 `site=` 均有 `fetched … groups`
- `data/*/groups_latest.json` 的 `fetched_at` 更新
- 内存：`ps -o rss,nlwp -p $(systemctl show -p MainPID --value sub2api-monitor)`
- 确认无 token/密码进 journal

### 步骤 5 — 清理（可选）

- 保留 `sub2api-monitor@.service` 文件供调试，或标记 deprecated
- 更新 README / install_service.sh 默认走多站
- 不再 `enable` 各 `@` 实例

---

## 3. 回滚

```bash
systemctl disable --now sub2api-monitor.service
systemctl enable --now sub2api-monitor@pinaic.service
systemctl enable --now sub2api-monitor@aiapibank.service
```

数据文件格式未变，**无需回滚 data**。  
若多站进程曾写过 token，单站进程继续读同一 `token.json` 即可。

---

## 4. 迁移期双开风险

| 风险 | 后果 | 避免 |
|------|------|------|
| `@pinaic` 与多站 Worker 同时跑 pinaic | flock 失败或 token 双写 | 先 stop `@*` 再起多站 |
| 只 stop 未 disable | 重启机器又拉起双开 | `disable --now` |
| 手动 `--env-file --once` 时多站在跑 | once 拿不到锁 | 临时 stop 多站或用拷贝 data |

---

## 5. 新站在迁移后的操作

只需：

```bash
cp sites/pinaic.env.example sites/newsite.env
chmod 600 sites/newsite.env
# 编辑配置
.venv/bin/python sub2api_monitor.py --env-file sites/newsite.env --once
systemctl restart sub2api-monitor    # 热加载上线前需要重启
```

**不需要** `systemctl enable sub2api-monitor@newsite`。

---

## 6. install_service.sh 行为建议

| 模式 | 行为 |
|------|------|
| 默认 | 安装并 enable `sub2api-monitor.service`；disable 所有 `@` |
| `./install_service.sh --per-site pinaic` | 旧模式，仅调试 |
| `./install_service.sh --multi` | 显式多站（与默认相同） |

---

## 7. 验收签字项

- [ ] `@` 实例均为 inactive/disabled  
- [ ] `sub2api-monitor.service` active  
- [ ] 全部站点 once/周期成功或失败原因可接受  
- [ ] RSS 对比迁移前有记录  
- [ ] 回滚演练做过一次（或书面确认可回滚）  
