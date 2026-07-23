# Deployment And Security Contract

> Publication：formal
> Status：draft
> Contract ID：`deployment/systemd-v1`

## 1. Groups timer

生产 groups timer 目标不变量：

- service `Type=oneshot`；
- timer `OnBootSec=1min`；
- `OnUnitInactiveSec=240s`；
- `RandomizedDelaySec=60s`；
- `AccuracySec=1s`；
- `Unit=` 指向同 backend instance service；
- timer 禁止 `Requires=` service；
- service 禁止 `[Install]`，只 enable timer；
- service `TimeoutStartSec=240`；
- 禁止 `Restart=always` 认证 tight loop；
- 同站 once timer 与 legacy simple 禁止同时 active。

Sub2API `sub2api-monitor@` legacy simple 仅用于回滚，状态 deprecated；新站和默认安装禁止选用。

## 2. Models daily timer

- `OnCalendar=*-*-* 00:00:00 Asia/Shanghai`；
- `RandomizedDelaySec=300`；
- `Persistent=true`；
- service `TimeoutStartSec=600`；
- ExecStart 使用对应 `--models-refresh`；
- service 禁止 `[Install]`；
- timer 默认只安装、不 enable；
- 仅显式 `--enable-models` 才 enable；
- enable 前应当验证 bootstrap 和 provider mutation capability；
- groups/models 共用同站 lock；full 有界等待，groups 可以因锁跳过一轮。

Installer 若只能 warning 而无法证明 bootstrap，属于 SHOULD 偏离，必须在输出中清楚说明；目标行为是拒绝 enable。

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

还必须使用已验证项目根和 venv 的绝对路径，不把 credential 放入 unit Environment，且 `ReadWritePaths` 不得扩大到仓库或系统路径。不能使用某项 hardening 时必须记录理由和替代控制。

当前已知 gap：Sub2API once/daily 缺 `UMask`；New-API daily 缺完整 hardening。contract 在修复并加静态测试前保持 draft。

## 4. Installer

Installer 必须：

1. 验证 venv、source unit 和 env；
2. env 收紧为 0600；
3. 运行 `--validate`；
4. 运行 `systemd-analyze verify`；
5. 静态检查 timer/service 不变量；
6. 拒绝同站 dual run；
7. models timer 默认 disabled；
8. 输出 status/log/stop/rollback 命令；
9. 任一站失败时整体非零；
10. 禁止安装 template 时自动触发 models service；
11. enable models 前验证 bootstrap/provider capability。

默认 enable models 或自动停止旧服务属于 breaking deployment change。

## 5. Lock

- 每个 site/backend 使用独立 `monitor.lock`；
- groups 默认/preflight 非阻塞；
- bootstrap/refresh 有界等待；
- lock unavailable 返回 CLI 2；
- lock 只提供互斥，不证明 data dir 的 site/backend 所有权；
- storage writer 仍必须校验 site/backend；
- exit/exception/signal 后必须 release。

## 6. Path profile

当前 deployment profile 固定 `/root/projects/zhongzhuan`。支持其他路径时必须由 installer 渲染或受控 drop-in 同步修改 `WorkingDirectory/ExecStart/ReadWritePaths`，并补 `systemd-analyze verify` 与 contract tests。

## 7. Secret boundary

普通输出和落盘禁止包含 password、access/refresh token、session、完整 API Key、proxy userinfo、原始 Authorization/header/body 或可能包含 active secret 的 exception string。

允许的敏感文件仅为 0600 env/auth cache。latest/events 必须可通过 secret scan。错误持久化必须使用稳定 kind、mask、限长，并禁止完整 response body。

| Path | 要求 |
|---|---|
| `sites/*.env` | 0600 |
| `token.json` | 0600 |
| `auth_state.json` | 0600 |
| latest/events | 不含 secret；可以 0644，service UMask 可更严格 |
| lock | 不含 secret；互斥语义正确 |

## 8. 冻结所需 tests

- verify 所有 source units；
- timer schedule/jitter/persistent/accuracy；
- service ExecStart/mode/timeout；
- 无 `Requires=` 和 service `[Install]`；
- hardening matrix；
- models default disabled；
- installer bootstrap/provider gate；
- dual-run refusal；
- deployment path 与 `ReadWritePaths` 对齐；
- stdout/stderr/log/latest/events secret scan。
