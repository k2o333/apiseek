# Deployment And Security Contract

> 状态：draft  
> Contract ID：`deployment/systemd-v1`

## 1. Groups timer

所有生产 groups timer 必须：

- service `Type=oneshot`；
- timer `OnBootSec=1min`；
- timer `OnUnitInactiveSec=240s`；
- timer `RandomizedDelaySec=60s`；
- timer `AccuracySec=1s`；
- timer 指定对应 instance service；
- timer 禁止 `Requires=` service；
- service 禁止 `[Install]`，只 enable timer；
- service `TimeoutStartSec=240`；
- service 失败等待下一 timer，禁止 `Restart=always` 紧循环；
- 同站 once timer 与 legacy simple 禁止同时 active。

Sub2API legacy simple 仅供回滚，状态为 deprecated。新站和默认安装禁止选择它。

## 2. Models daily timer

所有 models daily timer 必须：

- `OnCalendar=*-*-* 00:00:00 Asia/Shanghai`；
- `RandomizedDelaySec=300`；
- `Persistent=true`；
- service `TimeoutStartSec=600`；
- ExecStart 使用对应 CLI 的 `--models-refresh`；
- service 禁止 `[Install]`；
- timer 默认只安装、不 enable；
- 只有显式 installer `--enable-models` 才 enable；
- enable 前应当验证 `bootstrap_completed_at` 和 provider mutation capability；
- 与 groups 共用同站 lock；full 有界等待，groups 可以因锁跳过一轮。

如果 installer 在无法证明 bootstrap 时选择 warning 后继续，这属于偏离 SHOULD，必须在 install output 清晰说明；推荐改为拒绝 enable。

## 3. Credential-bearing service hardening

所有读取 env/auth/key 的 once 和 daily service 必须至少包含：

```ini
UMask=0077
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/root/projects/zhongzhuan/data
```

还必须：

- `WorkingDirectory` 指向已验证项目根；
- `ExecStart` 使用项目 venv 的绝对 Python 路径；
- env 路径由 `%i` 形成，site id 已通过 config contract；
- 不把 credential 写进 unit Environment；
- `ReadWritePaths` 只放业务 data 根，不放仓库/系统宽路径。

若某 deployment profile 不能使用某 hardening 项，必须记录理由和替代措施；禁止静默缺失。

## 4. Installer contract

Installer 必须：

1. 验证 venv、source unit 和 env 文件存在；
2. 将 env mode 收紧为 0600；
3. 运行 `--validate`；
4. 运行 `systemd-analyze verify`；
5. 静态检查 timer/service 不变量；
6. 拒绝同站 dual run；
7. models timer 默认 disabled；
8. 明确打印 status/log/stop/rollback 命令；
9. 任一站失败时整体返回非零；
10. 禁止因安装 models template 自动触发 models service。

安装行为改变，例如默认 enable models 或停止旧 service，属于 deployment contract breaking change。

## 5. Lock contract

- 每个 site/backend 使用独立 `monitor.lock`；
- groups 默认/preflight 使用非阻塞 acquire；
- bootstrap/refresh 使用有界等待；
- lock unavailable 返回 CLI 2；
- lock 只保证互斥，不保证错 site/backend 不轮流覆盖同一 DATA_DIR；
- storage writer 仍必须检查 site/backend；
- service exit/exception/signal 后必须 release。

## 6. Path profile

当前部署路径固定为 `/root/projects/zhongzhuan`。这属于此 deployment profile，而不是通用可移植保证。

未来支持其他路径必须：

- 由 installer 渲染绝对路径，或使用受控 drop-in；
- 同步更新 WorkingDirectory、ExecStart、ReadWritePaths；
- 提供 `systemd-analyze verify` 和 contract tests；
- 禁止在相同 contract version 中静默改路径语义。

## 7. Secret boundaries

禁止普通输出/落盘：

- password；
- access/refresh token；
- session value；
- 完整 API Key；
- proxy userinfo；
- 原始 Authorization/header/body；
- 可能含 active secret 的 exception string。

允许的敏感文件只有 0600 env/auth cache。models/groups latest/events 必须可通过 secret scanner。

错误落盘必须：

- 使用稳定 error kind；
- 对 active secret 做替换；
- 限长；
- 不保存完整 response body；
- 同时测试 stdout、stderr、logging capture 和非 auth JSON。

## 8. File modes

| Path | 要求 |
|---|---|
| `sites/*.env` | 0600 |
| `token.json` | 0600 |
| `auth_state.json` | 0600 |
| latest/events | 内容无 secret；文件可以 0644，service UMask 可以更严格 |
| lock | 不含 secret；互斥语义正确 |

## 9. Contract tests

- `systemd-analyze verify` 所有 source unit；
- timer schedule、jitter、persistent、accuracy；
- service ExecStart/mode/timeout；
- 无 `Requires=` / service `[Install]`；
- 全 credential-bearing unit hardening 矩阵；
- models default disabled；
- installer bootstrap/provider gate；
- dual-run refusal；
- paths 与 ReadWritePaths 对齐；
- source unit 变化触发 contract test。
